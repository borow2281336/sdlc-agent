from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .github_api import GitHubREST, normalize_repo
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
from .llm import get_llm
from .prompts import IssueContext, build_file_select_prompt, build_patch_prompt
from .state import AgentLabels, get_iteration, iter_labels
from .settings import Settings
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
    except Exception:  # noqa: BLE001
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
    comments = gh.list_issue_comments(pr_number)
    for c in reversed(comments):
        body = c.get("body", "")
        if AGENT_REVIEW_MARKER in body:
            # try to parse JSON block at the end
            try:
                data = extract_first_json(body)
                return json.dumps(data, ensure_ascii=False, indent=2)[:8_000]
            except Exception:  # noqa: BLE001
                return body[-8_000:]
    return None


def _safe_remove_label(gh: GitHubREST, number: int, label: str) -> None:
    try:
        gh.remove_label(number, label)
    except Exception:  # noqa: BLE001
        return


def _checkout_branch(workdir: Path, branch: str) -> None:
    res = git(["checkout", branch], cwd=workdir, check=False)
    if res.returncode == 0:
        return
    # Try remote
    git(["checkout", "-B", branch, f"origin/{branch}"], cwd=workdir, check=False)



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

    # checkout branch
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
    files_with_content = {p: _read_file(workdir, p) for p in files}

    patch_prompt = build_patch_prompt(issue_ctx, files_with_content, feedback=None)

    # a couple of attempts, because git apply can fail if model outputs slightly wrong paths
    patch = None
    last_err = ""
    for attempt in range(1, 3):
        patch_raw = llm.complete(
            system="Ты генерируешь патч unified diff.",
            user=patch_prompt + (f"\n\nПопытка #{attempt}. Если раньше патч не применился, исправь пути/контекст." if attempt > 1 else ""),
            temperature=0.2,
        )
        patch = extract_unified_diff(patch_raw)
        apply_res = apply_patch(patch, cwd=workdir)
        if apply_res.returncode == 0:
            last_err = ""
            break
        last_err = apply_res.stderr[-2000:]
    if last_err:
        msg = (
            f"❌ Не смог применить патч (git apply).\n\n"
            f"stderr:\n```\n{last_err}\n```"
        )
        gh.create_issue_comment(issue_number, msg)
        raise RuntimeError(msg)

    status = git_status_short(cwd=workdir)
    if ".rej" in status:
        msg = (
            "❌ Патч применился частично, появились .rej файлы. "
            "Нужна следующая итерация с корректным diff."
        )
        gh.create_issue_comment(issue_number, msg)
        raise RuntimeError(msg)

    if not working_tree_dirty(cwd=workdir):
        gh.create_issue_comment(issue_number, "ℹ️ Агент не внёс изменений (working tree чист).")
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

        gh.create_issue_comment(
            issue_number,
            f"✅ PR создан: {pr.get('html_url')}\n\n<!--sdlc-agent:pr={pr_number}-->",
        )

    labels = AgentLabels()
    gh.add_labels(pr_number, [labels.managed, labels.iter_label(1)])
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
    labels_list = [l["name"] for l in pr_issue.get("labels", [])]

    labels = AgentLabels()
    cur_iter = get_iteration(labels_list)
    if cur_iter >= settings.max_iters:
        gh.create_issue_comment(
            pr_number,
            f"🛑 Достигнут лимит итераций ({settings.max_iters}). Останавливаюсь. "
            "Нужно вмешательство человека.",
        )
        gh.add_labels(pr_number, [labels.stopped])
        _safe_remove_label(gh, pr_number, labels.fix)
        return

    next_iter = cur_iter + 1 if cur_iter else 2  # first fix is iter=2
    # update iteration labels
    for old in iter_labels(labels_list):
        _safe_remove_label(gh, pr_number, old)
    gh.add_labels(pr_number, [labels.iter_label(next_iter)])

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
    files_with_content = {p: _read_file(workdir, p) for p in files}

    patch_prompt = build_patch_prompt(issue_ctx, files_with_content, feedback=feedback)

    patch = None
    last_err = ""
    for attempt in range(1, 3):
        patch_raw = llm.complete(
            system="Ты генерируешь патч unified diff.",
            user=patch_prompt + (f"\n\nПопытка #{attempt}. Исправь проблемы из feedback." if attempt > 1 else ""),
            temperature=0.2,
        )
        patch = extract_unified_diff(patch_raw)
        apply_res = apply_patch(patch, cwd=workdir)
        if apply_res.returncode == 0:
            last_err = ""
            break
        last_err = apply_res.stderr[-2000:]
    if last_err:
        gh.create_issue_comment(pr_number, f"❌ Не смог применить патч: ```\n{last_err}\n```")
        raise RuntimeError(last_err)

    status = git_status_short(cwd=workdir)
    if ".rej" in status:
        gh.create_issue_comment(
            pr_number, "❌ Патч применился частично (.rej). Нужна ещё одна итерация."
        )
        raise RuntimeError("Patch rejected (.rej)")

    if not working_tree_dirty(cwd=workdir):
        gh.create_issue_comment(pr_number, "ℹ️ Агент не внёс изменений (working tree чист).")
        _safe_remove_label(gh, pr_number, labels.fix)
        return

    add_all(cwd=workdir)
    commit_msg = _shorten(f"Agent fix: {issue_ctx.title} (#{issue_number})", 72)
    commit(commit_msg, cwd=workdir)
    push(head_ref, cwd=workdir)

    gh.create_issue_comment(
        pr_number,
        f"🛠️ Push исправлений (итерация {next_iter}).\n\n"
        f"- Branch: `{head_ref}`\n",
    )
    _safe_remove_label(gh, pr_number, labels.fix)
