from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable
from urllib.parse import urlparse

import requests


class GitHubAPIError(RuntimeError):
    pass


_REPO_RE = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/]+)$")


def normalize_repo(repo: str) -> str:
    """Accept 'owner/repo' or full GitHub URL and return 'owner/repo'."""
    repo = repo.strip()
    if repo.startswith("http://") or repo.startswith("https://"):
        u = urlparse(repo)
        # /owner/repo or /owner/repo.git
        parts = [p for p in u.path.split("/") if p]
        if len(parts) < 2:
            raise ValueError(f"Cannot parse repo from URL: {repo}")
        owner, name = parts[0], parts[1]
        if name.endswith(".git"):
            name = name[: -len(".git")]
        return f"{owner}/{name}"

    m = _REPO_RE.match(repo)
    if not m:
        raise ValueError(f"Repo must be 'owner/repo' or https://github.com/owner/repo, got: {repo}")
    return repo


@dataclass(frozen=True)
class GitHubREST:
    token: str
    repo_full_name: str
    api_base: str = "https://api.github.com"
    timeout_s: int = 30

    @property
    def owner(self) -> str:
        return self.repo_full_name.split("/")[0]

    @property
    def repo(self) -> str:
        return self.repo_full_name.split("/")[1]

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, *, json_body: Any | None = None, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.api_base.rstrip('/')}{path}"
        resp = requests.request(
            method,
            url,
            headers=self._headers(),
            json=json_body,
            params=params,
            timeout=self.timeout_s,
        )
        if resp.status_code >= 400:
            raise GitHubAPIError(f"GitHub API error {resp.status_code} for {method} {path}: {resp.text}")
        if resp.status_code == 204:
            return None
        return resp.json()

    # Repo
    def get_repo(self) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.owner}/{self.repo}")

    def default_branch(self) -> str:
        return self.get_repo().get("default_branch", "main")

    # Issues
    def get_issue(self, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.owner}/{self.repo}/issues/{number}")

    def add_labels(self, number: int, labels: Iterable[str]) -> None:
        labs = [l for l in labels if l]
        if not labs:
            return
        self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/issues/{number}/labels",
            json_body={"labels": labs},
        )

    def remove_label(self, number: int, label: str) -> None:
        # label must be URL encoded; requests handles in URL? safer to replace spaces
        safe = label.replace(" ", "%20")
        self._request("DELETE", f"/repos/{self.owner}/{self.repo}/issues/{number}/labels/{safe}")

    def list_issue_comments(self, number: int, *, per_page: int = 100) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/issues/{number}/comments",
            params={"per_page": per_page},
        )

    def create_issue_comment(self, number: int, body: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/issues/{number}/comments",
            json_body={"body": body},
        )

    # Pull requests
    def get_pull(self, pr_number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}")

    def create_pull(self, *, title: str, body: str, head: str, base: str, draft: bool = False) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/pulls",
            json_body={"title": title, "body": body, "head": head, "base": base, "draft": draft},
        )

    def update_pull(self, pr_number: int, *, title: str | None = None, body: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        return self._request(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
            json_body=payload,
        )


    def list_pulls(self, *, state: str = "open", head: str | None = None, base: str | None = None, per_page: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"state": state, "per_page": per_page}
        if head:
            params["head"] = head
        if base:
            params["base"] = base
        return self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/pulls",
            params=params,
        )

    def create_pull_review(self, pr_number: int, *, body: str, event: str) -> dict[str, Any]:
        # event: APPROVE | REQUEST_CHANGES | COMMENT
        return self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/reviews",
            json_body={"body": body, "event": event},
        )
