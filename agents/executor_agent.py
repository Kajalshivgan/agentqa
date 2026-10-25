"""Executor Agent: runs Robot suites through the same runner the MCP server uses."""

from __future__ import annotations

from typing import Any, Callable

from agents.robot_mcp import core

Runner = Callable[..., dict[str, Any]]


def execute(suite: str, device: str | None = None, tests: list[str] | None = None,
            runner: Runner = core.run_suite) -> dict[str, Any]:
    return runner(suite, tests=tests, device=device)
