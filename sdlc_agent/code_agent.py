from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .git_utils import (
    add_all,
    apply_patch,
    checkout,
    checkout_new,
    clone_repo,
    commit,
    current_branch,
    ensure_git_identity,
    fetch_all,
    git,
    git_status_short,
    list_tracked_files,
    pull,
    push,
    set_origin_with_token,
    working_tree_dirty,
)
from .github_api import GitHubREST, normalize_repo
from .llm import get_llm
from .prompts import IssueContext, build_file_select_prompt, build_patch_prompt
from .settings import Settings
from .state import AgentLabels, get_iteration, iter_labels
from .text_utils import extract_first_json, extract_unified_diff

console = Console()

AGENT_PR_MARKER = "<!--sdlc-agent:pr="
AGENT_REVIEW_MARKER = "<!--sdlc-agent-review-->"


def _shorten(s: str, n: int = 72) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _read_file(repo_dir: Path, rel_path: str, *, max_chars: int = 20_000) -> str:
    path = repo_dir / rel_path
    if not path.exists():
        return f"<MISSING FILE: {rel_path}>"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n<TRUNCATED: {len(text)} chars total>"
    return text


def _ensure_repo_dir(repo_full_name: str, *, token: str, repo_dir: Path | None) -> Path:
    if repo_dir and (repo_dir / ".git").exists():
        return repo_dir

    tmp = Path(tempfile.mkdtemp(prefix="sdlc-agent-"))
    dest = tmp / repo_full_name.replace("/", "__")
    clone_repo(repo_full_name, token=token, dest=dest)
    return dest


def _find_pr_number_in_issue_comments(gh: GitHubREST, issue_number: int) -> int | None:
    try:
        comments = gh.list_issue_comments(issue_number)
    except Exception:
        return None

    for c in reversed(comments):
        body = c.get("body", "")
        if AGENT_PR_MARKER in body:
            m = re.search(r"<!--sdlc-agent:pr=(\d+)-->", body)
            if m:
                return int(m.group(1))
    return None


def _find_issue_number_in_pr_body(pr_body: str) -> int | None:
    m = re.search(r"Closes\s+#(\d+)", pr_body, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _find_latest_reviewer_feedback(gh: GitHubREST, pr_number: int) -> str | None:
    # NOTE: for demo we read issue comments on PR
    comments = gh.list_issue_comments(pr_number)
    for c in reversed(comments):
        body = c.get("body", "")
        if AGENT_REVIEW_MARKER in body:
            try:
                data = extract_first_json(body)
                return json.dumps(data, ensure_ascii=False, indent=2)[:8_000]
            except Exception:
                return body[-8_000:]
    return None


def _safe_remove_label(gh: GitHubREST, number: int, label: str) -> None:
    try:
        gh.remove_label(number, label)
    except Exception:
        return


def _checkout_branch(workdir: Path, branch: str) -> None:
    res = git(["checkout", branch], cwd=workdir, check=False)
    if res.returncode == 0:
        return
    # Try remote
    git(["checkout", "-B", branch, f"origin/{branch}"], cwd=workdir, check=False)


def _normalize_patch(text: str) -> str:
    """Extract a clean unified diff from LLM output (strip markdown/text around)."""
    if not text:
        return ""
    t = text.strip()

    # Try to pick the fenced block that contains a diff
    if "```" in t:
        parts = t.split("```")
        cand = [p for p in parts if "diff --git" in p]
        if cand:
            t = cand[0]
        else:
            t = t.replace("```diff", "").replace("```", "")

    # Cut everything before first diff header
    idx = t.find("diff --git")
    if idx != -1:
        t = t[idx:]

    return t.strip() + "\n"


def _split_diff_blocks(patch: str) -> list[str]:
    """Split a unified diff into 'diff --git ...' blocks."""
    if not patch:
        return []
    lines = patch.splitlines(True)
    blocks: list[list[str]] = []
    cur: list[str] = []

    for ln in lines:
        if ln.startswith("diff --git "):
            if cur:
                blocks.append(cur)
            cur = [ln]
        else:
            if cur:
                cur.append(ln)

    if cur:
        blocks.append(cur)

    return ["".join(b) for b in blocks]


def _filter_diff_blocks(patch: str, allow_paths: list[str] | None = None) -> str:
    """
    Keep only well-formed diff blocks.
    If allow_paths provided, keep only blocks whose 'diff --git' header mentions one of them.
    Fallback: keep the first block.
    """
    blocks = _split_diff_blocks(patch)
    if not blocks:
        return patch

    if allow_paths:
        kept = []
        for b in blocks:
            header = b.splitlines()[0]
            if any(p in header for p in allow_paths):
                kept.append(b)
        if kept:
            return "".join(kept)

    return blocks[0]


def _sanitize_unified_diff(patch: str) -> str:
    """
    Drop any lines that are not valid unified-diff syntax.
    Prevents LLM from inserting plain text inside hunks.
    """
    if not patch:
        return ""

    allowed_starts = (
        "diff --git ",
        "index ",
        "--- ",
        "+++ ",
        "@@ ",
        "new file mode ",
        "deleted file mode ",
        "similarity index ",
        "rename from ",
        "rename to ",
        "old mode ",
        "new mode ",
        "Binary files ",
        "\\ No newline at end of file",
    )

    out: list[str] = []
    for ln in patch.splitlines():
        if ln.startswith(allowed_starts) or (ln and ln[0] in (" ", "+", "-")):
            out.append(ln)
        else:
            # мусор (план/markdown/текст) — выкидываем
            continue

    return "\n".join(out).strip() + "\n"


def _safe_comment(gh: GitHubREST, number: int, body: str) -> None:
    try:
        gh.create_issue_comment(number, body)
    except Exception as e:
        console.print(f"[yellow]WARN[/yellow] cannot comment on issue/PR: {e}")


def run_issue(*, repo: str, issue_number: int, repo_dir: Path | None, settings: Settings) -> None:
    """Process Issue -> create/update PR."""
    repo_full_name = normalize_repo(repo)
    gh = GitHubREST(
        token=settings.github_token,
        repo_full_name=repo_full_name,
        api_base=settings.github_api_base,
    )

    issue = gh.get_issue(issue_number)
    issue_ctx = IssueContext(
        number=issue_number,
        title=issue.get("title", ""),
        body=issue.get("body") or "",
    )

    base_branch = settings.base_branch or gh.default_branch()
    branch = f"agent/issue-{issue_number}"

    llm = get_llm(settings)
    workdir = _ensure_repo_dir(repo_full_name, token=settings.github_token, repo_dir=repo_dir)

    console.print(
        Panel.fit(
            f"[bold]Repo[/bold]: {repo_full_name}\n[bold]Workdir[/bold]: {workdir}\n[bold]Issue[/bold]: #{issue_number}",
            title="Code Agent",
        )
    )

    ensure_git_identity(cwd=workdir, name=settings.git_user_name, email=settings.git_user_email)
    set_origin_with_token(repo_full_name, token=settings.github_token, cwd=workdir)
    fetch_all(cwd=workdir)

    checkout(base_branch, cwd=workdir)
    pull(cwd=workdir)

    res = git(["checkout", branch], cwd=workdir, check=False)
    if res.returncode != 0:
        checkout_new(branch, cwd=workdir)

    console.print(f"[green]On branch[/green] {current_branch(cwd=workdir)}")

    all_files = list_tracked_files(cwd=workdir)
    select_prompt = build_file_select_prompt(issue_ctx, all_files)

    sel_raw = llm.complete(system="Ты выбираешь файлы для чтения.", user=select_prompt, temperature=0.0)
    sel = extract_first_json(sel_raw)

    files = [p for p in sel.get("files", []) if isinstance(p, str)]
    if not files:
        files = all_files[:3]
    files = files[:8]

    # fast/robust demo mode: README-only issues should only touch README
    if "readme" in issue_ctx.title.lower():
        files = ["README.md"]

    files_with_content = {p: _read_file(workdir, p) for p in files}
    patch_prompt = build_patch_prompt(issue_ctx, files_with_content, feedback=None)

    # If it's a README task, do full-file rewrite (more reliable than git apply)
    if "readme" in issue_ctx.title.lower():
        readme_path = workdir / "README.md"
        current = _read_file(workdir, "README.md", max_chars=50_000)

        new_text = llm.complete(
            system=(
                "Ты редактируешь README.md. Верни ТОЛЬКО полный новый текст файла README.md. "
                "Никаких пояснений, markdown-блоков ``` и диффов. Только содержимое файла."
            ),
            user=(
                f"Issue #{issue_ctx.number}: {issue_ctx.title}\n\n"
                f"Описание:\n{issue_ctx.body}\n\n"
                "Текущее содержимое README.md:\n"
                "-----\n"
                f"{current}\n"
                "-----\n\n"
                "Верни полный обновлённый README.md:"
            ),
            temperature=0.2,
        ).strip()

        # strip accidental fences if model adds them
        if new_text.startswith("```"):
            new_text = re.sub(r"^```[a-zA-Z]*\n?", "", new_text)
            new_text = new_text.replace("```", "").strip()

        readme_path.write_text(new_text + "\n", encoding="utf-8")

    else:
        patch = None
        last_err = ""
        allow = None

        for attempt in range(1, 3):
            patch_raw = llm.complete(
                system="Верни ТОЛЬКО unified diff (git). Никакого текста/плана/markdown. Начинай с 'diff --git'.",
                user=patch_prompt
                + (
                    f"\n\nПопытка #{attempt}. Если раньше патч не применился — исправь diff так, чтобы он применился git apply."
                    if attempt > 1
                    else ""
                ),
                temperature=0.0,
            )

            patch = extract_unified_diff(patch_raw) or patch_raw
            patch = _normalize_patch(patch)
            patch = _sanitize_unified_diff(patch)
            patch = _filter_diff_blocks(patch, allow_paths=allow)

            apply_res = apply_patch(patch, cwd=workdir)
            if apply_res.returncode == 0:
                last_err = ""
                break
            last_err = (apply_res.stderr or "")[-2000:]

        if last_err:
            msg = (
                "❌ Не смог применить патч (git apply).\n\n"
                f"stderr:\n```\n{last_err}\n```"
            )
            _safe_comment(gh, issue_number, msg)
            raise RuntimeError(msg)

    status = git_status_short(cwd=workdir)
    if ".rej" in status:
        msg = (
            "❌ Патч применился частично, появились .rej файлы. "
            "Нужна следующая итерация с корректным diff."
        )
        _safe_comment(gh, issue_number, msg)
        raise RuntimeError(msg)

    if not working_tree_dirty(cwd=workdir):
        _safe_comment(gh, issue_number, "ℹ️ Агент не внёс изменений (working tree чист).")
        return

    add_all(cwd=workdir)
    commit_msg = _shorten(f"Agent: {issue_ctx.title} (#{issue_number})", 72)
    commit(commit_msg, cwd=workdir)
    push(branch, cwd=workdir)

    # Create or reuse PR
    pr_number = _find_pr_number_in_issue_comments(gh, issue_number)
    pr = None
    if pr_number:
        pr = gh.get_pull(pr_number)
        console.print(f"[yellow]Updating existing PR[/yellow] #{pr_number}")
    else:
        head = f"{gh.owner}:{branch}"
        existing = gh.list_pulls(state="open", head=head)
        if existing:
            pr = existing[0]
            pr_number = pr["number"]
        else:
            pr_title = issue_ctx.title or f"Issue #{issue_number}"
            pr_body = (
                f"Closes #{issue_number}\n\n"
                "Generated by **sdlc-agent**.\n"
                f"- Branch: `{branch}`\n"
            )
            pr = gh.create_pull(title=pr_title, body=pr_body, head=branch, base=base_branch, draft=False)
            pr_number = pr["number"]

        _safe_comment(
            gh,
            issue_number,
            f"✅ PR создан: {pr.get('html_url')}\n\n<!--sdlc-agent:pr={pr_number}-->",
        )

    labels = AgentLabels()
    try:
        gh.add_labels(pr_number, [labels.managed, labels.iter_label(1)])
    except Exception as e:
        console.print(f"[yellow]WARN[/yellow] cannot add labels: {e}")

    console.print(f"[green]Done[/green]. PR: #{pr_number} {pr.get('html_url')}")


def run_fix(*, repo: str, pr_number: int, repo_dir: Path | None, settings: Settings) -> None:
    """Process PR labeled with agent:fix -> push next commit to same PR."""
    repo_full_name = normalize_repo(repo)
    gh = GitHubREST(
        token=settings.github_token,
        repo_full_name=repo_full_name,
        api_base=settings.github_api_base,
    )

    pr = gh.get_pull(pr_number)
    pr_body = pr.get("body") or ""
    issue_number = _find_issue_number_in_pr_body(pr_body)
    if not issue_number:
        raise RuntimeError("Cannot find Issue number in PR body (expected 'Closes #<n>')")

    issue = gh.get_issue(issue_number)
    issue_ctx = IssueContext(
        number=issue_number,
        title=issue.get("title", ""),
        body=issue.get("body") or "",
    )

    pr_issue = gh.get_issue(pr_number)
    labels_list = [label["name"] for label in pr_issue.get("labels", [])]

    labels = AgentLabels()
    cur_iter = get_iteration(labels_list)
    if cur_iter >= settings.max_iters:
        _safe_comment(
            gh,
            pr_number,
            f"🛑 Достигнут лимит итераций ({settings.max_iters}). Останавливаюсь. Нужно вмешательство человека.",
        )
        try:
            gh.add_labels(pr_number, [labels.stopped])
        except Exception:
            pass
        _safe_remove_label(gh, pr_number, labels.fix)
        return

    next_iter = cur_iter + 1 if cur_iter else 2  # first fix is iter=2

    # update iteration labels
    for old in iter_labels(labels_list):
        _safe_remove_label(gh, pr_number, old)
    try:
        gh.add_labels(pr_number, [labels.iter_label(next_iter)])
    except Exception:
        pass

    feedback = _find_latest_reviewer_feedback(gh, pr_number)

    llm = get_llm(settings)
    workdir = _ensure_repo_dir(repo_full_name, token=settings.github_token, repo_dir=repo_dir)

    console.print(
        Panel.fit(
            f"[bold]Repo[/bold]: {repo_full_name}\n[bold]Workdir[/bold]: {workdir}\n[bold]PR[/bold]: #{pr_number} (iter {next_iter})",
            title="Code Agent (fix)",
        )
    )

    ensure_git_identity(cwd=workdir, name=settings.git_user_name, email=settings.git_user_email)
    set_origin_with_token(repo_full_name, token=settings.github_token, cwd=workdir)
    fetch_all(cwd=workdir)

    head_ref = pr.get("head", {}).get("ref")
    if not head_ref:
        raise RuntimeError("PR head ref is missing")

    _checkout_branch(workdir, head_ref)
    console.print(f"[green]On branch[/green] {current_branch(cwd=workdir)}")

    all_files = list_tracked_files(cwd=workdir)
    select_prompt = build_file_select_prompt(issue_ctx, all_files)

    sel_raw = llm.complete(system="Ты выбираешь файлы для чтения.", user=select_prompt, temperature=0.0)
    sel = extract_first_json(sel_raw)

    files = [p for p in sel.get("files", []) if isinstance(p, str)]
    if not files:
        files = all_files[:3]
    files = files[:8]

    if "readme" in issue_ctx.title.lower():
        files = ["README.md"]

    files_with_content = {p: _read_file(workdir, p) for p in files}
    patch_prompt = build_patch_prompt(issue_ctx, files_with_content, feedback=feedback)

    patch = None
    last_err = ""
    allow = ["README.md"] if "readme" in issue_ctx.title.lower() else None

    for attempt in range(1, 3):
        patch_raw = llm.complete(
            system="Верни ТОЛЬКО unified diff (git). Никакого текста/плана/markdown. Начинай с 'diff --git'.",
            user=patch_prompt
            + (
                f"\n\nПопытка #{attempt}. Если раньше патч не применился — исправь diff так, чтобы он применился git apply."
                if attempt > 1
                else ""
            ),
            temperature=0.0,
        )

        patch = extract_unified_diff(patch_raw) or patch_raw
        patch = _normalize_patch(patch)
        patch = _sanitize_unified_diff(patch)
        patch = _filter_diff_blocks(patch, allow_paths=allow)

        apply_res = apply_patch(patch, cwd=workdir)
        if apply_res.returncode == 0:
            last_err = ""
            break
        last_err = (apply_res.stderr or "")[-2000:]

    if last_err:
        _safe_comment(gh, pr_number, f"❌ Не смог применить патч:\n```\n{last_err}\n```")

        # ^ keep runtime simple even if type checker complains
        raise RuntimeError(last_err)

    status = git_status_short(cwd=workdir)
    if ".rej" in status:
        _safe_comment(gh, pr_number, "❌ Патч применился частично (.rej). Нужна ещё одна итерация.")
        raise RuntimeError("Patch rejected (.rej)")

    if not working_tree_dirty(cwd=workdir):
        _safe_comment(gh, pr_number, "ℹ️ Агент не внёс изменений (working tree чист).")
        _safe_remove_label(gh, pr_number, labels.fix)
        return

    add_all(cwd=workdir)
    commit_msg = _shorten(f"Agent fix: {issue_ctx.title} (#{issue_number})", 72)
    commit(commit_msg, cwd=workdir)
    push(head_ref, cwd=workdir)

    _safe_comment(
        gh,
        pr_number,
        f"🛠️ Push исправлений (итерация {next_iter}).\n\n- Branch: `{head_ref}`\n",
    )
    _safe_remove_label(gh, pr_number, labels.fix)
