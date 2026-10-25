"""Triage Agent: classifies each failed test as regression, flaky or environment issue.

Evidence used, in order:
1. The failure message: adb/emulator/connection problems are environment issues, never retried.
2. Retry outcome: a test that failed, then passed on re-run, is flaky.
3. A test that fails again on every retry is a genuine regression.
Every outcome is stored in SQLite (`results/flakiness.db`) so the report can show a per-test
flake rate over time.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

REGRESSION, FLAKY, ENVIRONMENT, UNCONFIRMED = "regression", "flaky", "environment", "unconfirmed"

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


class FlakinessStore:
    def __init__(self, path: str | Path = "results/flakiness.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS outcomes "
                        "(test TEXT, suite TEXT, status TEXT, ts REAL)")

    def record(self, test: str, suite: str, status: str) -> None:
        self.db.execute("INSERT INTO outcomes VALUES (?,?,?,?)", (test, suite, status, time.time()))
        self.db.commit()

    def flake_rate(self, test: str, window: int = 20) -> float:
        """Share of status flips (PASS<->FAIL) among the last `window` outcomes of a test."""
        rows = [r[0] for r in self.db.execute(
            "SELECT status FROM outcomes WHERE test=? ORDER BY ts DESC LIMIT ?", (test, window))]
        if len(rows) < 2:
            return 0.0
        flips = sum(1 for a, b in zip(rows, rows[1:]) if a != b)
        return round(flips / (len(rows) - 1), 2)
