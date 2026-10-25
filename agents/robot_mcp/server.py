"""MCP server that lets AI agents work with AgentQA's Robot Framework suites and live devices.

Run it over stdio (what Claude Desktop, Claude Code and VS Code Copilot agent mode expect):

    python -m agents.robot_mcp

Tools, grouped by what an agent does with them:
  discover   list_suites, list_keywords, list_devices
  author     validate_suite, write_suite
  execute    run_suite, list_runs, get_run_result
  explore    inspect_screen, run_keyword   (drive the device one step at a time)
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable

import subprocess

from agents.robot_mcp import core
from engine.adb_client import ADBError

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

INSTRUCTIONS = """\
AgentQA Robot Framework tools for Android-based HMIs (ADB + UIAutomator).
Typical flow: list_keywords -> inspect_screen -> try steps with run_keyword -> write_suite
(validated by a dry run) -> run_suite -> get_run_result. Prefer text/description/id locators
(Tap On Text, Tap On Description, Tap On Id) over Tap On Point. New suites are saved under
tests/robot/generated/. A test needs `Library    engine.MobileUILibrary    device=${DEVICE}`
in its Settings and `${DEVICE}    ${None}` in its Variables."""

mcp = _Server("agentqa-robot", instructions=INSTRUCTIONS)
_device = core.DeviceSession()


# Problems an agent can act on (fix its request, start the emulator, ...). Anything else is a bug
# and is left to the MCP SDK to report.
_EXPECTED = (core.ToolError, ADBError, subprocess.TimeoutExpired, OSError)


def _describe(exc: Exception) -> str:
    return str(exc) if isinstance(exc, core.ToolError) else f"{type(exc).__name__}: {exc}"


def _errors_as_results(func: Callable) -> Callable:
    """Return bad requests and device problems as {"error": ...} so the agent can react to them."""
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except _EXPECTED as exc:
                return {"error": _describe(exc)}
        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except _EXPECTED as exc:
            return {"error": _describe(exc)}
    return wrapper


# ---- discover -------------------------------------------------------------------------------

@mcp.tool()
@_errors_as_results
def list_suites(directory: str = "tests/robot") -> dict[str, Any]:
    """List Robot Framework suites (.robot files) with their test names and tags.

    Args:
        directory: Folder or .robot file, relative to the project root. Defaults to tests/robot.
    """
    return {"suites": core.list_suites(directory)}


@mcp.tool()
@_errors_as_results
def list_keywords(library: str = "MobileUILibrary", name_filter: str | None = None) -> dict[str, Any]:
    """List keywords (name, arguments, one-line doc) of an AgentQA keyword library.

    Args:
        library: MobileUILibrary (UI automation), SpeechLibrary (voice tests) or BuiltIn.
        name_filter: Optional case-insensitive substring, e.g. "tap" or "assert".
    """
    return core.list_keywords(library, name_filter)


@mcp.tool()
@_errors_as_results
def list_devices() -> dict[str, Any]:
    """List Android devices and emulators visible to ADB (serial and state)."""
    return {"devices": core.list_devices()}


# ---- author ---------------------------------------------------------------------------------

@mcp.tool()
@_errors_as_results
def validate_suite(content: str | None = None, path: str | None = None) -> dict[str, Any]:
    """Dry-run a suite to check syntax, keyword names and argument counts without a device.

    Pass either the suite text as `content` or an existing suite as `path`.
    """
    return core.validate_suite(content=content, path=path)


@mcp.tool()
@_errors_as_results
def write_suite(name: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    """Save a new .robot suite to tests/robot/generated/<name>.robot, only if its dry run passes.

    Args:
        name: File name without extension (letters, digits, _ - .).
        content: Full Robot Framework suite text.
        overwrite: Replace an existing generated suite with the same name.
    """
    return core.write_suite(name, content, overwrite)


# ---- execute --------------------------------------------------------------------------------

@mcp.tool()
@_errors_as_results
async def run_suite(path: str, tests: list[str] | None = None, include_tags: list[str] | None = None,
                    exclude_tags: list[str] | None = None, variables: dict[str, str] | None = None,
                    device: str | None = None, dryrun: bool = False,
                    timeout_s: float = 1800) -> dict[str, Any]:
    """Run a Robot Framework suite or folder and return per-test results.

    Failed tests include the failure message and the deepest failing keyword. log.html,
    report.html and screenshots are kept under results/mcp/<run_id>/.

    Args:
        path: .robot file or folder, relative to the project root or tests/robot.
        tests: Only run tests with these names (Robot --test patterns).
        include_tags / exclude_tags: Robot tag filters.
        variables: Extra Robot variables, e.g. {"TIMEOUT": "20"}.
        device: ADB serial; sets ${DEVICE}. Omit when only one device is connected.
        dryrun: Validate only, without touching the device.
        timeout_s: Abort the run after this many seconds.
    """
    return await asyncio.to_thread(core.run_suite, path, tests, include_tags, exclude_tags,
                                   variables, device, dryrun, timeout_s)


@mcp.tool()
@_errors_as_results
def list_runs(limit: int = 10) -> dict[str, Any]:
    """List the most recent runs started through this server, newest first."""
    return {"runs": core.list_runs(limit)}


@mcp.tool()
@_errors_as_results
def get_run_result(run_id: str) -> dict[str, Any]:
    """Get the detailed result of an earlier run: tests, failure messages, failing steps, files."""
    return core.get_run_result(run_id)


# ---- explore --------------------------------------------------------------------------------

@mcp.tool()
@_errors_as_results
async def inspect_screen(device: str | None = None, include_system: bool = False,
                         max_elements: int = 150) -> dict[str, Any]:
    """Dump the current screen: each element's text, content_desc, resource_id, class and bounds.

    Use these values as locators for Tap On Text / Tap On Description / Tap On Id / Assert Visible.

    Args:
        device: ADB serial. Omit when only one device is connected.
        include_system: Also include system bars and the keyboard.
        max_elements: Cap on returned elements.
    """
    return await asyncio.to_thread(_device.inspect_screen, device, include_system, max_elements)


@mcp.tool()
@_errors_as_results
async def run_keyword(keyword: str, args: list[str] | None = None, device: str | None = None) -> dict[str, Any]:
    """Execute a single MobileUILibrary keyword on the device right now, e.g. Tap On Text ["Display"].

    Lets an agent explore a flow step by step before writing it into a suite.

    Args:
        keyword: Keyword name as in list_keywords (case and spaces are ignored).
        args: Positional arguments as strings.
        device: ADB serial. Omit when only one device is connected.
    """
    return await asyncio.to_thread(_device.run_keyword, keyword, args, device)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
