"""Planner Agent: picks which Robot suites to run for a requirements / changelog text.

Deterministic keyword overlap between the changelog and each suite's name, documentation, test
names and tags. With no changelog every suite is selected (full regression).
"""

from __future__ import annotations

import re

from agents.robot_mcp import core

_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "when", "have", "has", "was",
         "are", "not", "fix", "fixed", "add", "added", "update", "updated", "changed", "test", "tests"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def plan(changelog: str | None, directory: str = "tests/robot") -> list[str]:
    """Return suite paths (relative to the project) relevant to the changelog."""
    suites = [s for s in core.list_suites(directory) if not s["path"].endswith(".rules.robot")]
    if not changelog or not changelog.strip():
        return [s["path"] for s in suites]
    wanted = _words(changelog)
    picked = []
    for suite in suites:
        haystack = " ".join([suite["path"], suite.get("documentation") or "",
                             *suite.get("tests", []), *suite.get("tags", [])])
        if wanted & _words(haystack):
            picked.append(suite["path"])
    return picked
