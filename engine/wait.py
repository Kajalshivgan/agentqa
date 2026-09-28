"""Polling helpers that absorb UI timing jitter."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


class WaitTimeout(AssertionError):
    pass


def wait_for_stable(capture: Callable[[], T], signature: Callable[[T], object], timeout: float = 10,
                    interval: float = 0.3) -> T:
    """Capture repeatedly until two consecutive captures have the same signature; return the last."""
    last = [capture()]

    def settled():
        current = capture()
        same = signature(current) == signature(last[0])
        last[0] = current
        return same

    wait_until(settled, timeout, interval, "screen did not settle")
    return last[0]


def wait_until(condition: Callable[[], T], timeout: float = 10, interval: float = 0.5,
               message: str = "condition not met") -> T:
    """Call `condition` until it returns something truthy or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while True:
        try:
            result = condition()
            if result:
                return result
        except Exception as e:  # a failed dump mid-animation shouldn't abort the wait
            last_error = e
        if time.monotonic() >= deadline:
            suffix = f" (last error: {last_error})" if last_error else ""
            raise WaitTimeout(f"{message} after {timeout}s{suffix}")
        time.sleep(interval)
