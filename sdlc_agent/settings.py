from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables."""

    # GitHub
    github_token: str
    github_api_base: str = "https://api.github.com"

    # LLM (OpenAI by default)
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    openai_model: str = "gpt-4o-mini"

    # Agent behavior
    max_iters: int = 3
    base_branch: str | None = None

    # Git identity for automated commits
    git_user_name: str = "sdlc-agent[bot]"
    git_user_email: str = "sdlc-agent[bot]@users.noreply.github.com"

    @classmethod
    def from_env(cls, *, actor: Literal["code", "reviewer"] = "code") -> Settings:
        if actor == "code":
            token = os.getenv("CODE_AGENT_GITHUB_TOKEN") or os.getenv("AGENT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
        else:
            token = os.getenv("REVIEWER_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or os.getenv("AGENT_GITHUB_TOKEN")

        max_iters = int(os.getenv("AGENT_MAX_ITERS", "3"))

        return cls(
            github_token=token,
            github_api_base=os.getenv("GITHUB_API_BASE", "https://api.github.com"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            max_iters=max_iters,
            base_branch=os.getenv("AGENT_BASE_BRANCH"),
            git_user_name=os.getenv("AGENT_GIT_NAME", "sdlc-agent[bot]"),
            git_user_email=os.getenv(
                "AGENT_GIT_EMAIL", "sdlc-agent[bot]@users.noreply.github.com"
            ),
        )
