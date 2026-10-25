"""Robot Framework operations exposed to AI agents through MCP.

Everything here is plain Python with no MCP dependency, so it can be unit-tested and reused by
the other agents directly. `server.py` wraps these functions as MCP tools.

Paths are always resolved inside the project root: an agent can read and run suites anywhere in
the repo, but can only *write* new suites under `tests/robot/generated/`.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

# Where the engine package lives (always importable by Robot) vs. the project the tools act on.
# They are the same in normal use; tests point ROOT at a temporary project.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
ROOT = PACKAGE_ROOT

# Keyword libraries an agent may inspect or call. Kept explicit so the server never imports an
# arbitrary module name sent by a client.
LIBRARIES = {
    "MobileUILibrary": "engine.MobileUILibrary",
    "SpeechLibrary": "engine.SpeechLibrary",
    "BuiltIn": "BuiltIn",
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def _suites_dir() -> Path:
    return ROOT / "tests" / "robot"


def _generated_dir() -> Path:
    return _suites_dir() / "generated"


def _runs_dir() -> Path:
    return ROOT / "results" / "mcp"


class ToolError(ValueError):
    """A bad request from the agent (unknown suite, path outside the project, ...)."""


# ---- paths -------------------------------------------------------------------------------------

def resolve_suite(path: str) -> Path:
    """Resolve a suite path given relative to the project root (or to tests/robot)."""
    if not path or not path.strip():
        raise ToolError("Suite path is empty")
    raw = Path(path.strip())
    candidates = [raw] if raw.is_absolute() else [ROOT / raw, _suites_dir() / raw]
    for candidate in candidates:
        resolved = candidate.resolve()
        if not _inside(resolved, ROOT):
            raise ToolError(f"'{path}' is outside the project")
        if resolved.exists():
            if resolved.is_file() and resolved.suffix != ".robot":
                raise ToolError(f"'{path}' is not a .robot file")
            return resolved
    raise ToolError(f"Suite '{path}' not found (paths are relative to the project root or tests/robot)")


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


# ---- discovery ---------------------------------------------------------------------------------

def list_suites(directory: str = "tests/robot") -> list[dict[str, Any]]:
    """Every .robot file under ``directory`` with its documentation, tests and tags."""
    from robot.api import TestSuiteBuilder

    base = resolve_suite(directory) if directory else _suites_dir()
    files = [base] if base.is_file() else sorted(base.rglob("*.robot"))
    suites = []
    for file in files:
        entry: dict[str, Any] = {"path": _rel(file)}
        try:
            suite = TestSuiteBuilder(process_curdir=False).build(str(file))
            entry["documentation"] = suite.doc
            entry["tests"] = [{"name": t.name, "tags": list(t.tags)} for t in suite.tests]
        except Exception as exc:  # a broken file shouldn't hide the others
            entry["error"] = str(exc)
        suites.append(entry)
    return suites


def list_keywords(library: str = "MobileUILibrary", name_filter: str | None = None) -> dict[str, Any]:
    """Keywords of an allowed library: name, arguments and the first line of the docs."""
    if library not in LIBRARIES:
        raise ToolError(f"Unknown library '{library}'. Available: {', '.join(LIBRARIES)}")
    from robot.libdocpkg import LibraryDocumentation

    with _stdout_to_stderr(), _on_sys_path(PACKAGE_ROOT):
        doc = LibraryDocumentation(LIBRARIES[library])
    keywords = []
    for kw in doc.keywords:
        if name_filter and name_filter.lower() not in kw.name.lower():
            continue
        keywords.append({
            "name": kw.name,
            "args": [str(arg) for arg in kw.args],
            "doc": (kw.doc or "").strip().split("\n")[0],
        })
    return {"library": library, "import": LIBRARIES[library], "keywords": keywords}


# ---- validating and writing suites ---------------------------------------------------------------

def validate_suite(content: str | None = None, path: str | None = None) -> dict[str, Any]:
    """Dry-run a suite (given as text or as a path) and report syntax and keyword errors.

    A dry run checks that every keyword exists and gets a valid number of arguments, without
    touching a device.
    """
    if (content is None) == (path is None):
        raise ToolError("Pass exactly one of 'content' or 'path'")
    if path is not None:
        return _dry_run(resolve_suite(path))
    _generated_dir().mkdir(parents=True, exist_ok=True)
    # The temp file sits next to the real suites so relative resource imports resolve the same way.
    fd, tmp = tempfile.mkstemp(suffix=".robot", prefix=".validate-", dir=_generated_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        return _dry_run(Path(tmp))
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def write_suite(name: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    """Validate ``content`` and, only if the dry run passes, save it to tests/robot/generated/."""
    stem = name[:-6] if name.endswith(".robot") else name
    if not _SAFE_NAME.match(stem):
        raise ToolError("Name may only contain letters, digits, '_', '-' and '.' (max 100 chars)")
    target = _generated_dir() / f"{stem}.robot"
    if target.exists() and not overwrite:
        raise ToolError(f"{_rel(target)} already exists; pass overwrite=true to replace it")
    validation = validate_suite(content=content)
    if not validation["valid"]:
        return {"written": False, "path": None, "validation": validation}
    _generated_dir().mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"written": True, "path": _rel(target), "validation": validation}


def _dry_run(suite: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as out:
        proc = _robot(["--dryrun", "--outputdir", out, "--output", "output.xml",
                       "--log", "NONE", "--report", "NONE", str(suite)], timeout=120)
        output = Path(out) / "output.xml"
        errors = _parse_errors(output) if output.exists() else []
        failures = []
        if output.exists():
            failures = [{"test": t["name"], "message": t["message"]}
                        for t in _summarize(output)["tests"] if t["status"] == "FAIL"]
    # Robot exits >= 250 for invalid data, no tests or framework errors; those come with no
    # parsable result, so surface the console output instead.
    if proc.returncode >= 250 and not errors and not failures:
        errors = [_last_lines(proc.stderr or proc.stdout)]
    return {"valid": not errors and not failures, "errors": errors, "failures": failures}


# ---- running suites ---------------------------------------------------------------------------

def run_suite(path: str, tests: list[str] | None = None, include_tags: list[str] | None = None,
              exclude_tags: list[str] | None = None, variables: dict[str, str] | None = None,
              device: str | None = None, dryrun: bool = False, timeout_s: float = 1800) -> dict[str, Any]:
    """Run a suite (or a whole folder) with Robot Framework and return a structured result."""
    suite = resolve_suite(path)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    out_dir = _runs_dir() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    args = ["--outputdir", str(out_dir)]
    for test in tests or []:
        args += ["--test", test]
    for tag in include_tags or []:
        args += ["--include", tag]
    for tag in exclude_tags or []:
        args += ["--exclude", tag]
    merged = dict(variables or {})
    if device:
        merged["DEVICE"] = device
    for key, value in merged.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", key):
            raise ToolError(f"Invalid variable name '{key}'")
        args += ["--variable", f"{key}:{value}"]
    if dryrun:
        args.append("--dryrun")
    args.append(str(suite))

    started = time.time()
    try:
        proc = _robot(args, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"run_id": run_id, "status": "TIMEOUT", "error": f"Run exceeded {timeout_s:.0f} s",
                "output_dir": _rel(out_dir)}
    result = get_run_result(run_id) if (out_dir / "output.xml").exists() else {
        "run_id": run_id, "status": "ERROR", "error": _last_lines(proc.stderr or proc.stdout),
        "output_dir": _rel(out_dir)}
    result["return_code"] = proc.returncode
    result["duration_s"] = round(time.time() - started, 1)
    return result


def get_run_result(run_id: str) -> dict[str, Any]:
    """Parse a previous run's output.xml: per-test status, failure message and failing step."""
    if not _RUN_ID.match(run_id or ""):
        raise ToolError(f"Invalid run id '{run_id}'")
    out_dir = _runs_dir() / run_id
    output = out_dir / "output.xml"
    if not output.exists():
        raise ToolError(f"No result for run '{run_id}'")
    summary = _summarize(output)
    summary.update({
        "run_id": run_id,
        "output_dir": _rel(out_dir),
        "log": _rel(out_dir / "log.html") if (out_dir / "log.html").exists() else None,
        "report": _rel(out_dir / "report.html") if (out_dir / "report.html").exists() else None,
        "screenshots": sorted(_rel(p) for p in out_dir.glob("*.png")),
        "errors": _parse_errors(output),
    })
    return summary


def list_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Most recent MCP runs, newest first."""
    if not _runs_dir().exists():
        return []
    runs = []
    for out_dir in sorted((d for d in _runs_dir().iterdir() if _RUN_ID.match(d.name)), reverse=True)[:max(1, limit)]:
        output = out_dir / "output.xml"
        if output.exists():
            s = _summarize(output)
            runs.append({"run_id": out_dir.name, "suite": s["suite"], "status": s["status"],
                         "passed": s["passed"], "failed": s["failed"], "skipped": s["skipped"]})
        else:
            runs.append({"run_id": out_dir.name, "status": "NO_OUTPUT"})
    return runs


def _summarize(output: Path) -> dict[str, Any]:
    from robot.api import ExecutionResult

    result = ExecutionResult(str(output))
    stats = result.statistics.total
    tests = []
    for test in result.suite.all_tests:
        item = {"name": test.name, "suite": test.parent.name if test.parent else "", "status": test.status,
                "message": test.message, "tags": list(test.tags),
                "elapsed_s": round(test.elapsed_time.total_seconds(), 2)}
        if test.status == "FAIL":
            item["failed_step"] = _failed_step(test)
        tests.append(item)
    return {"suite": result.suite.name, "status": result.suite.status,
            "passed": stats.passed, "failed": stats.failed, "skipped": stats.skipped, "tests": tests}


def _failed_step(item) -> dict[str, Any] | None:
    """Deepest failing keyword, which is usually the real cause (e.g. `Tap On Text`)."""
    deepest = None
    for child in getattr(item, "body", []):
        if getattr(child, "status", None) == "FAIL":
            if getattr(child, "type", "") in ("KEYWORD", "SETUP", "TEARDOWN"):
                deepest = {"keyword": child.name, "args": [str(a) for a in getattr(child, "args", ())],
                           "message": getattr(child, "message", "") or ""}
            nested = _failed_step(child)
            if nested:
                deepest = nested
            break
    if deepest is None:
        for fixture in ("setup", "teardown"):
            kw = getattr(item, fixture, None)
            if kw and getattr(kw, "status", None) == "FAIL":
                return _failed_step(kw) or {"keyword": kw.name, "args": [str(a) for a in kw.args],
                                            "message": getattr(kw, "message", "") or ""}
    return deepest


def _parse_errors(output: Path) -> list[str]:
    from robot.api import ExecutionResult

    try:
        return [f"{m.level}: {m.message}" for m in ExecutionResult(str(output)).errors.messages]
    except Exception as exc:
        return [f"Could not read {output.name}: {exc}"]


def _robot(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PACKAGE_ROOT), env.get("PYTHONPATH")]))
    # A user-level PYTHONIOENCODING like "utf-8:surrogateescape" breaks robot on Windows.
    env["PYTHONIOENCODING"] = "utf-8"
    # stdout is captured: under MCP stdio, anything printed to the real stdout corrupts the protocol.
    return subprocess.run([sys.executable, "-m", "robot", "--pythonpath", str(PACKAGE_ROOT), "--consolecolors", "off",
                           *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def _last_lines(text: str, n: int = 15) -> str:
    return "\n".join((text or "").strip().splitlines()[-n:])


# ---- live device ------------------------------------------------------------------------------

def list_devices() -> list[dict[str, str]]:
    """Devices/emulators visible to ADB."""
    from engine.adb_client import find_adb

    out = subprocess.run([find_adb(), "devices"], capture_output=True, text=True, timeout=15).stdout
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1]})
    return devices


class DeviceSession:
    """Keeps one MobileUILibrary per device so an agent can drive the UI step by step."""

    def __init__(self):
        self._libs: dict[str, Any] = {}

    def library(self, device: str | None = None):
        key = device or ""
        if key not in self._libs:
            from engine.MobileUILibrary import MobileUILibrary
            self._libs[key] = MobileUILibrary(device=device, timeout=10)
        return self._libs[key]

    def inspect_screen(self, device: str | None = None, include_system: bool = False,
                       max_elements: int = 150) -> dict[str, Any]:
        """Elements on screen with the locators the keywords accept (text, resource-id, content-desc)."""
        from engine.ui_tree import UITree

        lib = self.library(device)
        with _stdout_to_stderr():
            tree = UITree(lib.adb.dump_ui())
        source = tree.elements if include_system else tree.app_elements
        elements = []
        for el in source:
            if not (el.text or el.content_desc or el.resource_id or el.clickable or el.scrollable):
                continue
            elements.append({k: v for k, v in {
                "text": el.text, "content_desc": el.content_desc, "resource_id": el.resource_id,
                "class": el.class_name.rsplit(".", 1)[-1], "clickable": el.clickable or None,
                "scrollable": el.scrollable or None, "bounds": list(el.bounds)}.items() if v})
        return {"count": len(elements), "truncated": len(elements) > max_elements,
                "elements": elements[:max_elements]}

    def run_keyword(self, keyword: str, args: list[str] | None = None, device: str | None = None) -> dict[str, Any]:
        """Execute one MobileUILibrary keyword against the device, outside a Robot run."""
        lib = self.library(device)
        method = _keyword_method(lib, keyword)
        started = time.time()
        try:
            with _stdout_to_stderr():
                value = method(*(args or []))
        except Exception as exc:
            return {"status": "FAIL", "keyword": keyword, "message": f"{type(exc).__name__}: {exc}",
                    "elapsed_s": round(time.time() - started, 2)}
        return {"status": "PASS", "keyword": keyword, "return": None if value is None else str(value),
                "elapsed_s": round(time.time() - started, 2)}


def _keyword_method(lib, keyword: str):
    wanted = _normalize(keyword)
    for attr in dir(type(lib)):
        member = getattr(type(lib), attr, None)
        robot_name = getattr(member, "robot_name", None)
        if robot_name and _normalize(robot_name) == wanted:
            return getattr(lib, attr)
    raise ToolError(f"Unknown MobileUILibrary keyword '{keyword}' (use list_keywords to see them)")


def _normalize(name: str) -> str:
    return re.sub(r"[\s_]", "", name).lower()


# ---- helpers ----------------------------------------------------------------------------------

@contextlib.contextmanager
def _stdout_to_stderr():
    """Library code may print or use robot's console logger; keep that off the MCP stdout channel."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


@contextlib.contextmanager
def _on_sys_path(path: Path):
    entry = str(path)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        yield
    finally:
        if added:
            with contextlib.suppress(ValueError):
                sys.path.remove(entry)
