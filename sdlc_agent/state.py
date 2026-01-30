from __future__ import annotations

import re
from dataclasses import dataclass

LABEL_MANAGED = "agent:managed"
LABEL_FIX = "agent:fix"
LABEL_DONE = "agent:done"
LABEL_STOPPED = "agent:stopped"
ITER_PREFIX = "agent:iter-"

_ITER_RE = re.compile(r"^agent:iter-(\d+)$")


@dataclass(frozen=True)
class AgentLabels:
    managed: str = LABEL_MANAGED
    fix: str = LABEL_FIX
    done: str = LABEL_DONE
    stopped: str = LABEL_STOPPED

    def iter_label(self, n: int) -> str:
        return f"{ITER_PREFIX}{n}"


def get_iteration(labels: list[str]) -> int:
    iters = []
    for lab in labels:
        m = _ITER_RE.match(lab)
        if m:
            iters.append(int(m.group(1)))
    return max(iters) if iters else 0


def iter_labels(labels: list[str]) -> list[str]:
    return [lab for lab in labels if _ITER_RE.match(lab)]
