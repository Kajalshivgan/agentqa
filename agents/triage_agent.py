"""Triage Agent: classifies each failed test as regression, flaky or environment issue, then asks
an LLM for the probable cause.

Classification (rules, authoritative because it drives the retry loop):
1. The failure message: adb/emulator/connection problems are environment issues, never retried.
2. Retry outcome: a test that failed, then passed on re-run, is flaky.
3. A test that fails again on every retry is a genuine regression.

Analysis (LLM, advisory): for every failed test the agent looks up similar failure messages from
earlier runs in SQLite (`results/flakiness.db`) and sends them, together with the failed step and
the retry history, to the LLM. The LLM returns a probable cause, a suggested action and its own
opinion on the category. The rules keep the final say on the category; a disagreement is reported
instead of silently overriding them. With no LLM the similar-failure lookup still runs.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from difflib import SequenceMatcher
from pathlib import Path

REGRESSION, FLAKY, ENVIRONMENT, UNCONFIRMED = "regression", "flaky", "environment", "unconfirmed"
CATEGORIES = (REGRESSION, FLAKY, ENVIRONMENT)

_ENV = re.compile(r"no devices?|device .*(not found|offline)|adb|emulator|connection (refused|reset)|"
                  r"could not get idle state|timed out waiting for device|no such host", re.I)


def classify_message(message: str) -> str:
    return ENVIRONMENT if _ENV.search(message or "") else UNCONFIRMED


def classify(test: dict, attempts: list[str]) -> str:
    """`attempts` = statuses of this test across runs, oldest first (e.g. ["FAIL", "PASS"])."""
    if "PASS" in attempts and "FAIL" in attempts:
        return FLAKY
    if attempts and attempts[-1] == "FAIL":
        kind = classify_message(test.get("message", ""))
        if kind == ENVIRONMENT:
            return ENVIRONMENT
        return REGRESSION if len(attempts) > 1 else UNCONFIRMED
    return ""


# ---- failure history ---------------------------------------------------------

def normalize(message: str) -> str:
    """Strip the parts of an error message that vary between runs (numbers, ids, paths, quotes)."""
    text = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+|/[\w.\-/]+", "<path>", message or "")
    text = re.sub(r"\b[0-9a-f]{8,}\b|\d+(\.\d+)?", "<n>", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip().lower()


class FlakinessStore:
    def __init__(self, path: str | Path = "results/flakiness.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS outcomes "
                        "(test TEXT, suite TEXT, status TEXT, ts REAL)")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(outcomes)")}
        for col in ("message", "failed_step"):   # added after the first release of the table
            if col not in columns:
                self.db.execute(f"ALTER TABLE outcomes ADD COLUMN {col} TEXT")

    def record(self, test: str, suite: str, status: str, message: str = "", failed_step: str = "") -> None:
        self.db.execute("INSERT INTO outcomes (test, suite, status, ts, message, failed_step) "
                        "VALUES (?,?,?,?,?,?)", (test, suite, status, time.time(), message, failed_step))
        self.db.commit()

    def flake_rate(self, test: str, window: int = 20) -> float:
        """Share of status flips (PASS<->FAIL) among the last `window` outcomes of a test."""
        rows = [r[0] for r in self.db.execute(
            "SELECT status FROM outcomes WHERE test=? ORDER BY ts DESC LIMIT ?", (test, window))]
        if len(rows) < 2:
            return 0.0
        flips = sum(1 for a, b in zip(rows, rows[1:]) if a != b)
        return round(flips / (len(rows) - 1), 2)

    def similar_failures(self, message: str, before: float | None = None, limit: int = 5,
                         min_similarity: float = 0.6, scan: int = 500) -> list[dict]:
        """Earlier failures whose normalized message resembles `message`, most similar first.
        Identical messages are folded into one entry with a count. `before` (a timestamp)
        leaves out the current run's own records."""
        target = normalize(message)
        if not target:
            return []
        rows = self.db.execute(
            "SELECT test, suite, message, failed_step, ts FROM outcomes "
            "WHERE status='FAIL' AND message IS NOT NULL AND message != '' AND ts < ? "
            "ORDER BY ts DESC LIMIT ?",
            (before if before is not None else float("inf"), scan)).fetchall()
        groups: dict[str, dict] = {}
        for t, suite, msg, step, ts in rows:
            key = normalize(msg)
            if key in groups:
                groups[key]["count"] += 1
                groups[key]["tests"].add(t)
                continue
            score = SequenceMatcher(None, target, key).ratio()
            if score >= min_similarity:
                groups[key] = {"message": msg, "failed_step": step, "similarity": round(score, 2),
                               "count": 1, "tests": {t}, "last_seen": ts}
        found = sorted(groups.values(), key=lambda g: (-g["similarity"], -g["last_seen"]))[:limit]
        for g in found:
            g["tests"] = sorted(g["tests"])
        return found


# ---- LLM analysis ------------------------------------------------------------

SYSTEM_PROMPT = """You are the triage engineer of an Android HMI (infotainment / touchscreen) regression
test system. Tests are Robot Framework suites that drive the device through ADB and UIAutomator
(keywords such as Tap On Text, Assert Visible, Scroll To Text, Set Slider).

You get one failed test: its error message, the failing keyword and arguments, its pass/fail
history in this run, its historical flake rate, and similar failures seen in earlier runs.
Work out the most probable cause and say what to do next.

Categories:
- regression: the product behaves differently (screen, label or control missing, wrong state).
- flaky: the test is unreliable (timing, animation, slow UI dump, stale view); the product is fine.
- environment: the device, emulator, ADB, network or test host is the problem.

Use the similar past failures as evidence (same message seen before, in which tests, how often),
but do not assume they share the cause. If the evidence is thin, say so and lower the confidence.

Reply with one JSON object and nothing else:
{"category": "regression|flaky|environment",
 "probable_cause": "<one or two sentences>",
 "suggested_action": "<one sentence a tester can act on>",
 "similar_to": "<which past failure this resembles, or empty>",
 "confidence": "low|medium|high"}"""


def make_client(backend: str = "auto"):
    """`cloud` = Anthropic, `local` = Ollama, `none` = no LLM, `auto` = cloud when a key is set."""
    import os
    if backend == "auto":
        backend = "cloud" if os.environ.get("ANTHROPIC_API_KEY") else "none"
    if backend == "none":
        return None
    if backend == "local":
        from agents.recorder.ollama_client import OllamaClient
        return OllamaClient(model=os.environ.get("OLLAMA_MODEL"), host=os.environ.get("OLLAMA_HOST"))
    import anthropic
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no JSON object in LLM reply")
    return json.loads(match.group())


def analyze(test: dict, kind: str, attempts: list[str], similar: list[dict], flake_rate: float,
            client) -> dict:
    """Ask the LLM for a probable cause. Never raises: a failed call returns {"error": ...} so a
    broken LLM backend cannot stop a test run."""
    from agents.recorder.generator import MODEL
    step = test.get("failed_step") or {}
    prompt = json.dumps({
        "test": test.get("name", ""), "suite": test.get("suite", ""),
        "rule_based_category": kind, "error_message": test.get("message", ""),
        "failed_step": f"{step.get('keyword', '?')} {' '.join(step.get('args', []))}".strip(),
        "attempts_this_run": attempts, "historical_flake_rate": flake_rate,
        "similar_past_failures": similar}, indent=1, ensure_ascii=False)
    try:
        response = client.messages.create(model=MODEL, max_tokens=1000, system=SYSTEM_PROMPT,
                                          messages=[{"role": "user", "content": prompt}])
        if response.stop_reason == "refusal":
            return {"error": "LLM declined to analyze this failure"}
        result = _parse_json("".join(b.text for b in response.content if b.type == "text"))
    except Exception as e:   # network, auth, bad JSON: triage must still finish
        return {"error": f"{type(e).__name__}: {e}"}
    if result.get("category") not in CATEGORIES:
        result["category"] = ""
    return result
