"""Thin wrapper around the adb binary: UI dumps, input injection, screenshots, device info."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

DUMP_PATH = "/sdcard/window_dump.xml"
DUMP_TIMEOUT_S = 12


class ADBError(RuntimeError):
    pass


def find_adb() -> str:
    """Prefer the Android SDK's adb so it matches the emulator; fall back to PATH."""
    candidates = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            candidates.append(os.path.join(os.environ[var], "platform-tools"))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(os.path.join(os.environ["LOCALAPPDATA"], "Android", "Sdk", "platform-tools"))
    candidates.append(os.path.expanduser("~/Library/Android/sdk/platform-tools"))
    candidates.append(os.path.expanduser("~/Android/Sdk/platform-tools"))
    for folder in candidates:
        for name in ("adb.exe", "adb"):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    path = shutil.which("adb")
    if not path:
        raise ADBError("adb not found. Install Android SDK platform-tools or set ANDROID_HOME.")
    return path


@dataclass
class TouchDevice:
    path: str
    name: str
    max_x: int
    max_y: int


class ADBClient:
    def __init__(self, serial: str | None = None, adb_path: str | None = None, timeout: float = 30):
        self.adb = adb_path or find_adb()
        self.serial = serial or self._single_device()
        self.timeout = timeout
        self._screen_size: tuple[int, int] | None = None
        self._windows_dump: bool | None = None  # does `uiautomator dump --windows` work here?

    # ---- low level -------------------------------------------------------

    def _base(self) -> list[str]:
        return [self.adb, "-s", self.serial]

    def _single_device(self) -> str:
        out = subprocess.run([self.adb, "devices"], capture_output=True, stdin=subprocess.DEVNULL, text=True,
                             timeout=30).stdout
        devices = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
        if not devices:
            raise ADBError("No device connected. Start the emulator (or plug in the device) and check `adb devices`.")
        if len(devices) > 1:
            raise ADBError(f"Several devices connected ({', '.join(devices)}); choose one with --device <serial>.")
        return devices[0]

    # stdin=DEVNULL everywhere: adb must never get the user's terminal as input. `adb shell -t`
    # with a real console switches that console to raw mode, after which Ctrl+C is sent to the
    # device as a keystroke instead of stopping our process.

    def run(self, *args: str, timeout: float | None = None) -> bytes:
        proc = subprocess.run(self._base() + list(args), capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=timeout or self.timeout)
        if proc.returncode != 0:
            raise ADBError(f"adb {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
        return proc.stdout

    def shell(self, command: str, timeout: float | None = None) -> str:
        return self.run("shell", command, timeout=timeout).decode(errors="replace")

    def exec_out(self, command: str, timeout: float | None = None) -> bytes:
        return self.run("exec-out", command, timeout=timeout)

    def popen(self, *args: str) -> subprocess.Popen:
        return subprocess.Popen(self._base() + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, bufsize=1, errors="replace")

    # ---- UI hierarchy ----------------------------------------------------

    def dump_ui(self, retries: int = 5) -> str:
        """Dump the current UI hierarchy as XML (v1: dump to file, then exec-out cat; no adb pull).

        Uses `--windows` (undocumented, Android 13+) so system bars and the keyboard are included;
        devices that don't support it get the plain focused-window dump.
        """
        last_err = ""
        for attempt in range(retries):
            flag = "--windows " if self._windows_dump is not False else ""
            try:
                # A normal dump takes ~2 s; it can hang while the system UI reloads (e.g. after a
                # language change), so cap it and clear the stuck process before retrying.
                out = self.shell(f"uiautomator dump {flag}{DUMP_PATH}", timeout=DUMP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                out, last_err = "", f"dump took longer than {DUMP_TIMEOUT_S}s"
                self._kill_stuck_uiautomator()
            except ADBError as e:  # e.g. another uiautomator dump is already running
                out, last_err = "", str(e)
            if "dumped to" in out.lower():
                xml = self.exec_out(f"cat {DUMP_PATH}").decode("utf-8", errors="replace")
                if xml.lstrip().startswith("<?xml"):
                    if self._windows_dump is None and flag:
                        # Older uiautomator ignores unknown flags and dumps one window.
                        self._windows_dump = "<window " in xml
                    return xml
                last_err = "empty dump"
            elif out:
                # Typically "ERROR: could not get idle state" while animations run.
                last_err = out.strip()
            # Back off long enough to outlast another client's in-flight dump (~2 s).
            time.sleep(0.5 * (attempt + 1))
        raise ADBError(f"uiautomator dump failed: {last_err}")

    def _kill_stuck_uiautomator(self) -> None:
        try:
            self.shell("pkill -f uiautomator", timeout=5)
        except (ADBError, subprocess.TimeoutExpired):
            pass

    # ---- input -----------------------------------------------------------

    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        self.swipe(x, y, x, y, duration_ms)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")

    def input_text(self, text: str) -> None:
        # `input text` treats spaces as separators; %s is its escape for a space.
        escaped = re.sub(r"([\\\"'`$&|;<>()*?~#!])", r"\\\1", text).replace(" ", "%s")
        self.shell(f"input text {escaped}")

    def keyevent(self, key: str | int) -> None:
        self.shell(f"input keyevent {key}")

    def start_app(self, package: str) -> None:
        self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")

    def home_package(self) -> str | None:
        out = self.shell("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                         "-c android.intent.category.HOME")
        component = out.strip().splitlines()[-1] if out.strip() else ""
        return component.split("/")[0] if "/" in component else None

    def screenshot(self, path: str) -> str:
        # Multi-display devices (AAOS with instrument-cluster displays) print a warning ahead of
        # the PNG unless a display id is given, so target the main display explicitly.
        display = self.main_display_id()
        png = self.exec_out(f"screencap -d {display} -p" if display else "screencap -p")
        start = png.find(b"\x89PNG")
        if start < 0:
            raise ADBError(f"screencap returned no PNG: {png[:200]!r}")
        with open(path, "wb") as f:
            f.write(png[start:])
        return path

    def main_display_id(self) -> str | None:
        if not hasattr(self, "_display_id"):
            out = self.shell("dumpsys SurfaceFlinger --display-id")
            m = re.search(r"Display (\d+) \(HWC display 0\)", out) or re.search(r"Display (\d+)", out)
            self._display_id = m.group(1) if m else None
        return self._display_id

    # ---- device info -----------------------------------------------------

    def screen_size(self) -> tuple[int, int]:
        if self._screen_size is None:
            out = self.shell("wm size")
            sizes = dict(re.findall(r"(Physical|Override) size: (\d+x\d+)", out))
            w, h = (sizes.get("Override") or sizes["Physical"]).split("x")
            self._screen_size = (int(w), int(h))
        return self._screen_size

    def touch_devices(self) -> dict[str, TouchDevice]:
        """All input devices reporting multitouch X/Y, keyed by /dev/input path."""
        return parse_getevent_devices(self.shell("getevent -pl"))

    def system_bars(self) -> dict[str, tuple[int, int, int, int]]:
        """Frames of system bar windows on the main display (not visible to uiautomator dump)."""
        return parse_system_bars(self.shell("dumpsys window windows"))


_BAR_NAME = re.compile(r"(SystemBar|NavigationBar|StatusBar)")
_WINDOW_HEADER = re.compile(r"Window #\d+ Window\{\S+ \S+ ([^}]+)\}")
_FRAME = re.compile(r"\bframe=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_system_bars(text: str) -> dict[str, tuple[int, int, int, int]]:
    """From `dumpsys window windows`: each `Window #N Window{hash user Name}:` header is
    followed by a `Frames: ... frame=[l,t][r,b]` line."""
    bars, current = {}, None
    for line in text.splitlines():
        header = _WINDOW_HEADER.search(line)
        if header:
            current = header.group(1) if _BAR_NAME.search(header.group(1)) else None
            continue
        frame = _FRAME.search(line)
        if current and frame and current not in bars:
            l, t, r, b = (int(v) for v in frame.groups())
            if r > l and b > t:
                bars[current] = (l, t, r, b)
    return bars


def parse_getevent_devices(text: str) -> dict[str, TouchDevice]:
    devices: dict[str, TouchDevice] = {}
    path = name = None
    max_x = max_y = None

    def flush():
        if path and max_x is not None and max_y is not None:
            devices[path] = TouchDevice(path, name or "", max_x, max_y)

    for line in text.splitlines():
        m = re.match(r"add device \d+: (\S+)", line)
        if m:
            flush()
            path, name, max_x, max_y = m.group(1), None, None, None
            continue
        m = re.match(r'\s*name:\s*"(.*)"', line)
        if m:
            name = m.group(1)
            continue
        m = re.search(r"ABS_MT_POSITION_([XY])\s*:.*\bmax (\d+)", line)
        if m:
            if m.group(1) == "X":
                max_x = int(m.group(2))
            else:
                max_y = int(m.group(2))
    flush()
    return devices
