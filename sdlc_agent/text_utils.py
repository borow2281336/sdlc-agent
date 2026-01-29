from __future__ import annotations

import json
import re
from typing import Any


_JSON_CODEBLOCK_RE = re.compile(r"```json\s*(?P<body>\{.*?\})\s*```", re.DOTALL)
_ANY_CODEBLOCK_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_-]+)?\s*(?P<body>.*?)\s*```", re.DOTALL
)


def extract_codeblock(text: str, *, lang: str) -> str | None:
    """Return the first fenced code block body for a given language."""
    for match in _ANY_CODEBLOCK_RE.finditer(text):
        if (match.group("lang") or "").strip().lower() == lang.lower():
            return match.group("body").strip()
    return None


def extract_first_json(text: str) -> Any:
    """Best-effort JSON extraction from LLM output.

    Supports:
    - ```json ... ``` fenced blocks
    - raw JSON object anywhere in the text

    Raises ValueError if nothing usable found.
    """
    m = _JSON_CODEBLOCK_RE.search(text)
    if m:
        return json.loads(m.group("body"))

    # fallback: find first {...} that parses
    candidates = []
    # Greedy find all {...} spans (simple heuristic)
    for m2 in re.finditer(r"\{", text):
        start = m2.start()
        for end in range(len(text) - 1, start, -1):
            if text[end] != "}":
                continue
            snippet = text[start : end + 1]
            if len(snippet) > 200_000:
                continue
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                continue

    raise ValueError("Could not extract valid JSON from text")


def extract_unified_diff(text: str) -> str:
    """Extract unified diff from LLM output.

    Accepted formats:
    - ```diff ... ```
    - raw text containing 'diff --git'
    """
    block = extract_codeblock(text, lang="diff")
    if block and "diff --git" in block:
        return block.strip()

    # raw
    idx = text.find("diff --git")
    if idx != -1:
        return text[idx:].strip()

    raise ValueError("Could not extract unified diff (expected 'diff --git ...')")
