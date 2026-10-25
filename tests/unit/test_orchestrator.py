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


def _run(scripts, store=None, **kw):
    runner = FakeRunner(scripts)
    graph = build_graph(runner, store or triage_agent.FlakinessStore(":memory:"), **kw)
    return graph.invoke({"suites": ["s.robot"], "device": None}), runner


class FakeLLM:
    """anthropic-shaped client returning a canned reply."""
    def __init__(self, reply):
        self.reply, self.prompts = reply, []
        self.messages = type("M", (), {"create": self._create})()

    def _create(self, *, messages, **_):
        self.prompts.append(messages[0]["content"])
        block = type("B", (), {"type": "text", "text": self.reply})()
        return type("R", (), {"stop_reason": "end_turn", "content": [block]})()


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


def test_similar_failures_ignore_numbers_and_current_run():
    store = triage_agent.FlakinessStore(":memory:")
    store.record("Old", "s", "FAIL", "Element 'Brightness level' not found after 10 s")
    store.record("Other", "s", "FAIL", "adb: device 'emulator-5554' offline")
    found = store.similar_failures("Element 'Brightness level' not found after 15 s",
                                   before=float("inf"))
    assert [f["tests"] for f in found] == [["Old"]] and found[0]["similarity"] > 0.9
    assert store.similar_failures("Element 'Brightness level' not found", before=0) == []


def test_old_database_without_message_column_is_upgraded(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE outcomes (test TEXT, suite TEXT, status TEXT, ts REAL)")
    db.commit()
    db.close()
    store = triage_agent.FlakinessStore(path)
    store.record("A", "s", "FAIL", "boom")
    assert store.similar_failures("boom") [0]["message"] == "boom"


def test_llm_analysis_uses_history_and_lands_in_report():
    store = triage_agent.FlakinessStore(":memory:")
    store.record("Earlier", "s", "FAIL", "Element 'Brightness' not found")   # from a previous run
    import time
    time.sleep(0.01)
    llm = FakeLLM('{"category": "flaky", "probable_cause": "Slow UI dump after theme change.",'
                  ' "suggested_action": "Add Wait Until Screen Is Stable.", "similar_to": "Earlier",'
                  ' "confidence": "medium"}')
    fail = _test("A", "FAIL", "Element 'Brightness' not found")
    state, _ = _run([[fail], [fail]], store=store, llm_client=llm)
    assert state["classes"] == {"A": triage_agent.REGRESSION}
    assert len(llm.prompts) == 1                                  # analyzed once, after retries
    assert "Earlier" in llm.prompts[0]                            # similar past failure was sent
    report = state["report"]
    assert "Slow UI dump" in report and "would classify this as **flaky**" in report
    assert "Seen before" in report


def test_broken_llm_does_not_stop_the_run():
    class Boom:
        messages = type("M", (), {"create": staticmethod(lambda **_: 1 / 0)})()
    fail = _test("A", "FAIL", "Element 'X' not found")
    state, _ = _run([[fail], [fail]], llm_client=Boom())
    assert state["classes"] == {"A": triage_agent.REGRESSION}
    assert "LLM analysis unavailable" in state["report"]
