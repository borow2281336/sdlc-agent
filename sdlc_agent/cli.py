from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console

from .code_agent import run_fix, run_issue
from .reviewer import run_pr_review
from .settings import Settings

console = Console()

code_app = typer.Typer(add_completion=False, help="Code Agent CLI (Issue -> PR, PR fix loop)")
review_app = typer.Typer(add_completion=False, help="Reviewer Agent CLI (runs in CI on PR)")


def _default_repo_dir() -> Path:
    return Path(os.getenv("GITHUB_WORKSPACE", ".")).resolve()
DEFAULT_REPO_DIR = _default_repo_dir()

@code_app.command("issue")
def issue_cmd(
    repo: str = typer.Option(..., help="Repo in form owner/repo or URL"),
    issue: int = typer.Option(..., "--issue", help="Issue number"),
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR, help="Local path to repo (checked out). If missing .git, repo will be cloned."),
) -> None:
    """Create / update PR for an Issue."""
    settings = Settings.from_env(actor="code")
    run_issue(repo=repo, issue_number=issue, repo_dir=repo_dir, settings=settings)


@code_app.command("fix")
def fix_cmd(
    repo: str = typer.Option(..., help="Repo in form owner/repo or URL"),
    pr: int = typer.Option(..., "--pr", help="Pull Request number"),
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR, help="Local path to repo (checked out). If missing .git, repo will be cloned."),
) -> None:
    """Push next fix commit to PR (triggered by label agent:fix)."""
    settings = Settings.from_env(actor="code")
    run_fix(repo=repo, pr_number=pr, repo_dir=repo_dir, settings=settings)


@review_app.command("pr")
def pr_cmd(
    repo: str = typer.Option(..., help="Repo in form owner/repo or URL"),
    pr: int = typer.Option(..., "--pr", help="Pull Request number"),
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR, help="Local path to repo (checked out)."),
    ci_results: Path | None = typer.Option(None, help="Path to ci_results.json"),
) -> None:
    """Run AI review and publish comment + review + labels."""
    settings = Settings.from_env(actor="reviewer")
    run_pr_review(repo=repo, pr_number=pr, repo_dir=repo_dir, settings=settings, ci_results_path=ci_results)


def main() -> None:
    code_app()


if __name__ == "__main__":
    main()
