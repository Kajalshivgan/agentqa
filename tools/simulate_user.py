"""Drive the *emulator* with real kernel-level input (as a human would), for testing the recorder.

`adb shell input tap` injects above the kernel and is invisible to getevent, so this uses the
emulator console (`adb emu event ...`), which produces the same events as a mouse click on the
emulator window.

  python tools/simulate_user.py demo_display_flow
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.adb_client import find_adb  # noqa: E402

ADB = find_adb()


class _Console:
    """One persistent connection to the emulator console (localhost:5554).

    A separate `adb emu` process per event takes ~0.3 s, so a tap's down->up gap became long
    enough to look like a long press. Over one socket a tap takes about as long as a real one.
    """

    def __init__(self, port: int = 5554):
        import socket
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self._read_until_ok()
        token_file = Path.home() / ".emulator_console_auth_token"
        if token_file.exists():
            self.command("auth " + token_file.read_text().strip())

    def _read_until_ok(self) -> str:
        data = b""
        while not data.rstrip().endswith((b"OK", b"KO")) and b"KO:" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.decode(errors="replace")

    def command(self, line: str) -> str:
        self.sock.sendall(line.encode() + b"\n")
        return self._read_until_ok()


_console: _Console | None = None


def emu(*args: str) -> None:
    global _console
    try:
        if _console is None:
            _console = _Console()
        _console.command(" ".join(args))
    except OSError:  # console unavailable: fall back to one adb process per command
        _console = None
        subprocess.run([ADB, "emu", *args], capture_output=True, check=True)


def tap(x: int, y: int, hold: float = 0.1) -> None:
    emu("event", "mouse", str(x), str(y), "0", "1")
    time.sleep(hold)
    emu("event", "mouse", str(x), str(y), "0", "0")


def swipe(x1: int, y1: int, x2: int, y2: int, steps: int = 8, duration: float = 0.4) -> None:
    emu("event", "mouse", str(x1), str(y1), "0", "1")
    for i in range(1, steps + 1):
        time.sleep(duration / steps)
        x, y = x1 + (x2 - x1) * i // steps, y1 + (y2 - y1) * i // steps
        emu("event", "mouse", str(x), str(y), "0", "1")
    emu("event", "mouse", str(x2), str(y2), "0", "0")


def type_text(text: str) -> None:
    emu("event", "text", text)


def back() -> None:
    # Note: on the AAOS emulator these key events are accepted by the console but do not reach
    # the guest's input devices, so a hardware Back can't be simulated there.
    emu("event", "send", "EV_KEY:KEY_BACK:1", "EV_SYN:0:0")
    emu("event", "send", "EV_KEY:KEY_BACK:0", "EV_SYN:0:0")


def tap_text(text: str) -> None:
    """Tap wherever `text` currently is, like a person reading the screen."""
    from engine import ADBClient, UITree
    el = UITree(ADBClient().dump_ui()).find(text=text)
    if el is None:
        raise SystemExit(f"'{text}' is not on screen")
    tap(*el.center)


def demo_display_flow(pause: float = 4.0) -> None:
    """Settings (AAOS, 1408x792): Display -> Sound -> Display (row padding) -> search 'brightness'."""
    steps = [
        ("tap 'Display' label", lambda: tap(250, 604)),
        ("tap 'Sound' label", lambda: tap(244, 508)),
        ("tap 'Display' row padding (no text under finger)", lambda: tap(480, 604)),
        ("tap Search icon", lambda: tap(432, 124)),
        ("type 'brightness'", lambda: type_text("brightness")),
        ("tap search result 'Adaptive brightness'", lambda: tap_text("Adaptive brightness")),
    ]
    for label, action in steps:
        print(f"[user] {label}")
        action()
        time.sleep(pause)


if __name__ == "__main__":
    globals()[sys.argv[1] if len(sys.argv) > 1 else "demo_display_flow"]()
