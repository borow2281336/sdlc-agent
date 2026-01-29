from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable


@dataclass(frozen=True)
class CmdResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    def __init__(self, result: CmdResult):
        super().__init__(
            f"Command failed (exit={result.returncode}): {' '.join(result.cmd)}\n{result.stderr}"
        )
        self.result = result


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path,
    check: bool = True,
    input_text: str | None = None,
) -> CmdResult:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    res = CmdResult(cmd=cmd, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    if check and proc.returncode != 0:
        raise CommandError(res)
    return res


def git(
    args: Iterable[str],
    *,
    cwd: Path,
    check: bool = True,
    input_text: str | None = None,
) -> CmdResult:
    return run_cmd(["git", *list(args)], cwd=cwd, check=check, input_text=input_text)


def clone_repo(repo_full_name: str, *, token: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"
    run_cmd(["git", "clone", url, str(dest)], cwd=dest.parent)


def ensure_git_identity(*, cwd: Path, name: str, email: str) -> None:
    git(["config", "user.name", name], cwd=cwd)
    git(["config", "user.email", email], cwd=cwd)


def set_origin_with_token(repo_full_name: str, *, token: str, cwd: Path) -> None:
    url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"
    git(["remote", "set-url", "origin", url], cwd=cwd)


def checkout(branch: str, *, cwd: Path) -> None:
    git(["checkout", branch], cwd=cwd)


def checkout_new(branch: str, *, cwd: Path) -> None:
    git(["checkout", "-b", branch], cwd=cwd)


def fetch_all(*, cwd: Path) -> None:
    git(["fetch", "--all", "--prune"], cwd=cwd)


def pull(*, cwd: Path) -> None:
    git(["pull", "--ff-only"], cwd=cwd)


def add_all(*, cwd: Path) -> None:
    git(["add", "-A"], cwd=cwd)


def commit(message: str, *, cwd: Path) -> None:
    git(["commit", "-m", message], cwd=cwd)


def push(branch: str, *, cwd: Path) -> None:
    git(["push", "--set-upstream", "origin", branch], cwd=cwd)


def list_tracked_files(*, cwd: Path) -> list[str]:
    res = git(["ls-files"], cwd=cwd)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def working_tree_dirty(*, cwd: Path) -> bool:
    res = git(["status", "--porcelain"], cwd=cwd)
    return bool(res.stdout.strip())


def apply_patch(patch_text: str, *, cwd: Path) -> CmdResult:
    """Apply unified diff to repo. Returns CmdResult (check=False)."""
    return git(
        ["apply", "--reject", "--whitespace=fix", "-"],
        cwd=cwd,
        check=False,
        input_text=patch_text,
    )


def current_branch(*, cwd: Path) -> str:
    res = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return res.stdout.strip()


def git_status_short(*, cwd: Path) -> str:
    res = git(["status", "--porcelain"], cwd=cwd)
    return res.stdout.strip()
