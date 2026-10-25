from agents import reporter_agent, triage_agent
from orchestrator import build_graph


def _test(name, status, message=""):
    return {"name": name, "status": status, "message": message}


class FakeRunner:
    """Returns scripted results per call; records which tests each call was asked to run."""
    def __init__(self, scripts):
        self.scripts, self.calls = scripts, []

    def __call__(self, suite, tests=None, device=None, **_):
        self.calls.append(tests)
        results = self.scripts[len(self.calls) - 1]
        return {"tests": [r for r in results if tests is None or r["name"] in tests], "log": "log.html"}


def _run(scripts, **kw):
    runner = FakeRunner(scripts)
    graph = build_graph(runner, triage_agent.FlakinessStore(":memory:"), plan_fn=lambda _c: ["s.robot"], **kw)
    return graph.invoke({"changelog": "", "device": None}), runner


def test_all_pass_has_no_retry():
    state, runner = _run([[_test("A", "PASS")]])
    assert runner.calls == [None] and state["classes"] == {}
    assert "passed: 1, failed: 0" in state["report"]


def test_failure_that_passes_on_retry_is_flaky():
    state, runner = _run([[_test("A", "FAIL", "Element not found")], [_test("A", "PASS")]])
    assert runner.calls == [None, ["A"]]
    assert state["classes"] == {"A": triage_agent.FLAKY}
    assert "Bug reports" not in state["report"]


def test_consistent_failure_is_regression_with_bug_report():
    fail = _test("A", "FAIL", "Element 'Brightness' not found")
    state, _ = _run([[fail], [fail]])
    assert state["classes"] == {"A": triage_agent.REGRESSION}
    assert "## Bug reports" in state["report"]


def test_adb_problem_is_environment_and_not_retried():
    state, runner = _run([[_test("A", "FAIL", "No devices/emulators found")]])
    assert len(runner.calls) == 1
    assert state["classes"] == {"A": triage_agent.ENVIRONMENT}


def test_flake_rate_counts_status_flips():
    store = triage_agent.FlakinessStore(":memory:")
    for status in ["PASS", "FAIL", "PASS", "PASS"]:
        store.record("A", "s", status)
    assert store.flake_rate("A") == 0.67
    assert reporter_agent.build_report({}, {}, {}).startswith("# AgentQA")
