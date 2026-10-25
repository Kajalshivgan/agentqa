"""Robot Framework MCP tools: discovery, validation, writing, running and live-device steps.

No device or emulator needed: suites use BuiltIn keywords or are dry-run, and the device tests
use a fake ADB client fed with a saved UI dump.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agents.robot_mcp import core

FIXTURES = Path(__file__).parent / "fixtures"

PASSING = """*** Test Cases ***
Adds Up
    Should Be Equal As Integers    ${2}    2
"""

MIXED = """*** Settings ***
Documentation    Two tests, one fails.

*** Test Cases ***
Good
    [Tags]    smoke
    Log    fine

Bad
    [Tags]    regression
    Outer Step

*** Keywords ***
Outer Step
    Should Be Equal    actual    expected    msg=Value differs
"""

UI_SUITE = """*** Settings ***
Library    engine.MobileUILibrary    device=${DEVICE}

*** Variables ***
${DEVICE}    ${None}

*** Test Cases ***
Open Display
    Tap On Text       Display
    Assert Visible    Brightness level
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throwaway project root so tests never touch the real suites or results."""
    (tmp_path / "tests" / "robot").mkdir(parents=True)
    monkeypatch.setattr(core, "ROOT", tmp_path)
    return tmp_path


def write(project: Path, rel: str, text: str) -> Path:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---- paths ----

def test_resolve_suite_relative_to_root_or_suites_dir(project):
    suite = write(project, "tests/robot/a.robot", PASSING)
    assert core.resolve_suite("tests/robot/a.robot") == suite.resolve()
    assert core.resolve_suite("a.robot") == suite.resolve()


@pytest.mark.parametrize("bad", ["../outside.robot", "/etc/passwd", "tests/../../x.robot"])
def test_resolve_suite_rejects_paths_outside_project(project, bad):
    with pytest.raises(core.ToolError):
        core.resolve_suite(bad)


def test_resolve_suite_rejects_non_robot_files(project):
    write(project, "notes.txt", "hi")
    with pytest.raises(core.ToolError, match="not a .robot file"):
        core.resolve_suite("notes.txt")


# ---- discovery ----

def test_list_suites_reports_tests_and_tags(project):
    write(project, "tests/robot/mixed.robot", MIXED)
    write(project, "tests/robot/sub/pass.robot", PASSING)
    suites = {s["path"]: s for s in core.list_suites()}
    assert set(suites) == {"tests/robot/mixed.robot", "tests/robot/sub/pass.robot"}
    mixed = suites["tests/robot/mixed.robot"]
    assert mixed["documentation"] == "Two tests, one fails."
    assert mixed["tests"] == [{"name": "Good", "tags": ["smoke"]}, {"name": "Bad", "tags": ["regression"]}]


def test_list_keywords_of_mobile_library():
    result = core.list_keywords("MobileUILibrary")
    names = {k["name"] for k in result["keywords"]}
    assert {"Tap On Text", "Assert Visible", "Scroll To Text", "Restart App"} <= names
    tap = next(k for k in result["keywords"] if k["name"] == "Tap On Text")
    assert tap["args"][0].startswith("text")


def test_list_keywords_filter_and_unknown_library():
    assert all("assert" in k["name"].lower() for k in core.list_keywords("MobileUILibrary", "assert")["keywords"])
    with pytest.raises(core.ToolError, match="Unknown library"):
        core.list_keywords("os")


# ---- validate / write ----

def test_validate_accepts_ui_suite_without_device(project):
    result = core.validate_suite(content=UI_SUITE)
    assert result == {"valid": True, "errors": [], "failures": []}
    assert not list((project / "tests/robot/generated").glob("*.robot"))  # temp file cleaned up


def test_validate_reports_unknown_keyword(project):
    result = core.validate_suite(content=UI_SUITE.replace("Tap On Text", "Tapp On Txt"))
    assert not result["valid"]
    assert "Tapp On Txt" in json.dumps(result)


def test_validate_reports_wrong_argument_count(project):
    result = core.validate_suite(content=UI_SUITE.replace("Assert Visible    Brightness level", "Assert Visible"))
    assert not result["valid"]


def test_write_suite_saves_only_valid_content(project):
    ok = core.write_suite("display_check", UI_SUITE)
    assert ok["written"] and ok["path"] == "tests/robot/generated/display_check.robot"
    assert (project / ok["path"]).read_text(encoding="utf-8") == UI_SUITE

    bad = core.write_suite("broken", UI_SUITE.replace("Tap On Text", "No Such Keyword"))
    assert not bad["written"] and not (project / "tests/robot/generated/broken.robot").exists()


def test_write_suite_guards_name_and_overwrite(project):
    core.write_suite("once", PASSING)
    with pytest.raises(core.ToolError, match="already exists"):
        core.write_suite("once", PASSING)
    assert core.write_suite("once", PASSING, overwrite=True)["written"]
    for bad_name in ("../escape", "a/b", "", ".hidden"):
        with pytest.raises(core.ToolError):
            core.write_suite(bad_name, PASSING)


# ---- run ----

def test_run_suite_reports_pass_fail_and_failing_step(project):
    write(project, "tests/robot/mixed.robot", MIXED)
    result = core.run_suite("mixed.robot")
    assert (result["passed"], result["failed"], result["status"]) == (1, 1, "FAIL")
    bad = next(t for t in result["tests"] if t["name"] == "Bad")
    assert bad["message"] == "Value differs: actual != expected"
    assert bad["failed_step"]["keyword"] == "Should Be Equal"
    assert result["log"].endswith("log.html") and (project / result["log"]).exists()

    again = core.get_run_result(result["run_id"])
    assert again["tests"] == result["tests"]
    assert core.list_runs()[0]["run_id"] == result["run_id"]


def test_run_suite_filters_by_test_and_tag(project):
    write(project, "tests/robot/mixed.robot", MIXED)
    assert core.run_suite("mixed.robot", tests=["Good"])["status"] == "PASS"
    only = core.run_suite("mixed.robot", include_tags=["regression"])
    assert [t["name"] for t in only["tests"]] == ["Bad"]


def test_run_suite_passes_variables_and_device(project):
    write(project, "tests/robot/vars.robot", """*** Test Cases ***
Uses Variables
    Should Be Equal    ${DEVICE}    emulator-5554
    Should Be Equal    ${LANG}      de
""")
    assert core.run_suite("vars.robot", variables={"LANG": "de"}, device="emulator-5554")["status"] == "PASS"
    with pytest.raises(core.ToolError, match="Invalid variable"):
        core.run_suite("vars.robot", variables={"A;B": "x"})


def test_get_run_result_rejects_bad_ids(project):
    for bad in ("../../etc", "nope", ""):
        with pytest.raises(core.ToolError):
            core.get_run_result(bad)


# ---- live device (fake ADB) ----

class FakeADB:
    def __init__(self, xml: str):
        self.xml = xml
        self.taps: list[tuple[int, int]] = []

    def dump_ui(self):
        return self.xml

    def tap(self, x, y):
        self.taps.append((x, y))


@pytest.fixture
def session():
    from engine.MobileUILibrary import MobileUILibrary

    lib = MobileUILibrary(timeout=0.5, poll_interval=0.05)
    lib._adb = FakeADB((FIXTURES / "settings_home.xml").read_text(encoding="utf-8"))
    s = core.DeviceSession()
    s._libs[""] = lib
    return s, lib


def test_inspect_screen_lists_locators(session):
    s, _ = session
    screen = s.inspect_screen()
    texts = {e.get("text") for e in screen["elements"]}
    assert "Display" in texts
    assert all("bounds" in e for e in screen["elements"])


def test_run_keyword_taps_and_reports(session):
    s, lib = session
    ok = s.run_keyword("tap on text", ["Display"])
    assert ok["status"] == "PASS" and len(lib.adb.taps) == 1
    missing = s.run_keyword("Tap On Text", ["Does Not Exist"])
    assert missing["status"] == "FAIL" and "not found" in missing["message"]
    with pytest.raises(core.ToolError, match="Unknown MobileUILibrary keyword"):
        s.run_keyword("Delete Everything")


# ---- MCP layer ----

def test_mcp_server_exposes_tools_and_returns_errors(project):
    pytest.importorskip("mcp")
    from agents.robot_mcp import server

    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"list_suites", "list_keywords", "list_devices", "validate_suite", "write_suite",
                     "run_suite", "list_runs", "get_run_result", "inspect_screen", "run_keyword"}
    # A bad request comes back as data the agent can read, not as a crash.
    assert server.get_run_result("../../x") == {"error": "Invalid run id '../../x'"}
