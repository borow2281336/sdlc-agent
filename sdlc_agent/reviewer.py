from __future__ import annotations

import json
import os
import re
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .git_utils import git
from .github_api import GitHubREST, normalize_repo
from .llm import get_llm
from .prompts import IssueContext, build_review_prompt
from .settings import Settings
from .state import AgentLabels, get_iteration
from .text_utils import extract_first_json

console = Console()

AGENT_REVIEW_MARKER = "<!--sdlc-agent-review-->"


def _find_issue_number_in_pr_body(pr_body: str) -> int | None:
    m = re.search(r"Closes\s+#(\d+)", pr_body, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _load_ci_results(ci_results_path: Path | None) -> dict:
    if not ci_results_path:
        return {}
    if not ci_results_path.exists():
        return {}
    return json.loads(ci_results_path.read_text(encoding="utf-8"))


def _summarize_ci(ci: dict) -> tuple[str, str, bool]:
    """Return (summary, logs_tail, is_green)."""
    if not ci:
        return ("(нет данных)", "", True)

    lines = []
    tails = []
    green = True
    for name, info in ci.items():
        code = info.get("exit_code")
        outcome = "✅" if code == 0 else "❌"
        if code != 0:
            green = False
        lines.append(f"- {outcome} **{name}** (exit={code})")
        tail = info.get("log_tail") or ""
        if tail:
            tails.append(f"## {name}\n```\n{tail[-2000:]}\n```")
    return ("\n".join(lines), "\n\n".join(tails), green)


def run_pr_review(
    *,
    repo: str,
    pr_number: int,
    repo_dir: Path,
    settings: Settings,
    ci_results_path: Path | None = None,
) -> None:
    repo_full_name = normalize_repo(repo)
    gh = GitHubREST(token=settings.github_token, repo_full_name=repo_full_name, api_base=settings.github_api_base)

    pr = gh.get_pull(pr_number)
    pr_body = pr.get("body") or ""
    pr_title = pr.get("title") or f"PR #{pr_number}"

    pr_author = str((pr.get("user") or {}).get("login") or "")
    reviewer_login = gh.viewer_login()

    if pr_author and reviewer_login and pr_author.lower() == reviewer_login.lower():
        labels = AgentLabels()
        msg = (
            " **Self-review запрещён** (Reviewer и автор PR — один и тот же аккаунт).\n\n"
            f"- PR author: `{pr_author}`\n"
            f"- Reviewer token user: `{reviewer_login}`\n\n"
            "Нужно настроить `REVIEWER_GITHUB_TOKEN` от другого аккаунта. "
            "Останавливаю авто-цикл."
        )
        gh.create_issue_comment(pr_number, msg)
        gh.add_labels(pr_number, [labels.stopped])
        _safe_remove_label(gh, pr_number, labels.fix)

        step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
        if step_summary_path:
            Path(step_summary_path).write_text(msg + "\n", encoding="utf-8")

        return



    issue_number = _find_issue_number_in_pr_body(pr_body)
    if not issue_number:
        raise RuntimeError("Cannot find Issue number in PR body (expected 'Closes #<n>')")

    issue = gh.get_issue(issue_number)
    issue_ctx = IssueContext(number=issue_number, title=issue.get("title", ""), body=issue.get("body") or "")

    # CI results
    ci = _load_ci_results(ci_results_path)
    ci_summary, ci_logs_tail, ci_green = _summarize_ci(ci)

    # Diff from git (requires fetch-depth 0 in Actions)
    base_sha = pr.get("base", {}).get("sha")
    head_sha = pr.get("head", {}).get("sha")
    if not base_sha or not head_sha:
        raise RuntimeError("Missing base/head SHA in PR payload")

    diff_res = git(["diff", f"{base_sha}...{head_sha}"], cwd=repo_dir, check=False)
    diff = diff_res.stdout.strip()
    if not diff:
        diff = "(diff пуст)"


    llm = get_llm(settings)

    prompt = build_review_prompt(
        issue=issue_ctx,
        pr_title=pr_title,
        pr_body=pr_body,
        diff=diff[:120_000],
        ci_summary=ci_summary,
        ci_logs_tail=ci_logs_tail[:60_000],
    )

    console.print(
        Panel.fit(
            f"[bold]Repo[/bold]: {repo_full_name}\n[bold]PR[/bold]: #{pr_number}\n[bold]Issue[/bold]: #{issue_number}",
            title="Reviewer Agent",
        )
    )

    raw = llm.complete(system="Ты пишешь строго JSON review.", user=prompt, temperature=0.1)
    data = extract_first_json(raw)

    needs_changes = bool(data.get("needs_changes", False))
    summary_md = str(data.get("summary_md", "")).strip()
    review_md = str(data.get("review_md", "")).strip()
    action_items = data.get("action_items", [])
    confidence = float(data.get("confidence", 0.5))

    # Enforce CI truth
    if not ci_green:
        needs_changes = True
        if "CI" not in review_md:
            review_md = f"### CI\n{ci_summary}\n\n" + review_md

    event = "REQUEST_CHANGES" if needs_changes else "APPROVE"

    # Labels / iteration limit
    labels = AgentLabels()
    pr_issue = gh.get_issue(pr_number)
    pr_labels = [label["name"] for label in pr_issue.get("labels", [])]
    cur_iter = get_iteration(pr_labels)
    max_iters = settings.max_iters

    if needs_changes and cur_iter >= max_iters:
        needs_changes = False  # stop auto loop
        event = "COMMENT"
        gh.add_labels(pr_number, [labels.stopped])
        review_md = (
            f" Лимит итераций достигнут ({max_iters}). Авто-исправления остановлены.\n\n"
            + review_md
        )

    # 1) Job summary
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        Path(step_summary_path).write_text(
            f"## Reviewer summary\n\n{summary_md}\n\n### CI\n{ci_summary}\n",
            encoding="utf-8",
        )

    # 2) PR issue comment (machine readable JSON at end)
    comment_body = (
        f"## 🤖 AI Reviewer\n\n"
        f"**needs_changes:** `{needs_changes}`\n\n"
        f"{summary_md}\n\n"
        f"{review_md}\n\n"
        f"### Action items\n"
        + "\n".join(f"- {x}" for x in (action_items if isinstance(action_items, list) else []))
        + f"\n\nConfidence: `{confidence}`\n\n"
        f"{AGENT_REVIEW_MARKER}\n"
        f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```\n"
    )
    gh.create_issue_comment(pr_number, comment_body)

    # 3) PR review object (Approve / Request changes)
    gh.create_pull_review(pr_number, body=review_md or summary_md or "AI review", event=event)


    if needs_changes:
        gh.add_labels(pr_number, [labels.fix])

        _safe_remove_label(gh, pr_number, labels.done)
    else:
        gh.add_labels(pr_number, [labels.done])
        _safe_remove_label(gh, pr_number, labels.fix)

    console.print(f"[green]Review submitted[/green]. Event={event}, needs_changes={needs_changes}")


def _safe_remove_label(gh: GitHubREST, number: int, label: str) -> None:
    try:
        gh.remove_label(number, label)
    except Exception:
        return
