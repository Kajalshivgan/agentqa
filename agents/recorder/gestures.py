"""Turn `adb shell getevent -lt` output into taps, long presses, swipes and key presses.

Pure logic with no device access, so it is unit-testable against captured getevent text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# [   1234.567890] /dev/input/event2: EV_ABS       ABS_MT_POSITION_X    000002d0
_LINE = re.compile(r"\[\s*([\d.]+)\]\s+(/dev/input/\S+):\s+(\S+)\s+(\S+)\s+(\S+)")

TRACKING_ID_NONE = 0xFFFFFFFF
TAP_MAX_MOVE_PX = 30
LONG_PRESS_MS = 600
KEYS = {"KEY_BACK": "back", "KEY_HOME": "home", "KEY_HOMEPAGE": "home", "KEY_APPSELECT": "recents"}


@dataclass
class RawEvent:
    time: float
    device: str
    type: str
    code: str
    value: str

    @property
    def int_value(self) -> int:
        try:
            return int(self.value, 16)
        except ValueError:
            return -1


def parse_line(line: str) -> RawEvent | None:
    m = _LINE.search(line)
    if not m:
        return None
    return RawEvent(float(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5))


@dataclass
class Gesture:
    kind: str  # "tap" | "long_press" | "swipe" | "key" | "unknown" (touch without coordinates)
    device: str
    t_down: float
    t_up: float
    points: list[tuple[int, int]] = field(default_factory=list)  # raw touch-panel units
    key: str | None = None

    @property
    def duration_ms(self) -> int:
        return int((self.t_up - self.t_down) * 1000)

    @property
    def start(self) -> tuple[int, int]:
        return self.points[0]

    @property
    def end(self) -> tuple[int, int]:
        return self.points[-1]


class _TouchState:
    def __init__(self):
        self.x: int | None = None  # kept across gestures: the kernel only re-sends changed axes
        self.y: int | None = None
        self.down = False
        self.pending_down = False
        self.pending_up = False
        self.t_down = 0.0
        self.points: list[tuple[int, int]] = []


class GestureTracker:
    """Feed RawEvents in order; collect finished Gestures from `feed`'s return value.

    `scale(device, x, y) -> (px, py)` converts raw panel units to screen pixels; the tap/swipe
    threshold is applied in pixels.
    """

    def __init__(self, scale, touch_devices: set[str]):
        self.scale = scale
        self.touch_devices = touch_devices
        self._state: dict[str, _TouchState] = {}
        self._key_down: dict[tuple[str, str], float] = {}

    def feed(self, ev: RawEvent) -> list[Gesture]:
        if ev.type == "EV_KEY" and ev.code in KEYS:
            return self._key(ev)
        if ev.device not in self.touch_devices:
            return []
        st = self._state.setdefault(ev.device, _TouchState())

        if ev.type == "EV_ABS":
            if ev.code == "ABS_MT_POSITION_X":
                st.x = ev.int_value
            elif ev.code == "ABS_MT_POSITION_Y":
                st.y = ev.int_value
            elif ev.code == "ABS_MT_TRACKING_ID":
                if ev.int_value == TRACKING_ID_NONE:
                    st.pending_up = st.down or st.pending_down
                elif not st.down:
                    st.pending_down, st.t_down = True, ev.time
        elif ev.type == "EV_KEY" and ev.code == "BTN_TOUCH":
            if ev.value == "DOWN" and not st.down:
                st.pending_down, st.t_down = True, ev.time
            elif ev.value == "UP":
                st.pending_up = st.down or st.pending_down
        elif ev.type == "EV_SYN" and ev.code == "SYN_REPORT":
            return self._sync(ev, st)
        return []

    def _sync(self, ev: RawEvent, st: _TouchState) -> list[Gesture]:
        if st.pending_down:
            st.pending_down, st.down, st.points = False, True, []
        if st.down and st.x is not None and st.y is not None:
            if not st.points or st.points[-1] != (st.x, st.y):
                st.points.append((st.x, st.y))
        if not st.pending_up:
            return []
        st.pending_up, st.down = False, False
        if not st.points:
            # Touch with no position ever reported (the kernel only re-sends changed axes, so a
            # first tap at the exact spot of a tap made before recording started has none).
            # Still emit it so callers know the finger lifted.
            return [Gesture("unknown", ev.device, st.t_down, ev.time)]
        g = Gesture("tap", ev.device, st.t_down, ev.time, st.points)
        (sx, sy), (ex, ey) = self.scale(ev.device, *g.start), self.scale(ev.device, *g.end)
        if abs(ex - sx) > TAP_MAX_MOVE_PX or abs(ey - sy) > TAP_MAX_MOVE_PX:
            g.kind = "swipe"
        elif g.duration_ms >= LONG_PRESS_MS:
            g.kind = "long_press"
        return [g]

    def _key(self, ev: RawEvent) -> list[Gesture]:
        k = (ev.device, ev.code)
        if ev.value == "DOWN":
            self._key_down[k] = ev.time
            return []
        if ev.value == "UP" and k in self._key_down:
            t_down = self._key_down.pop(k)
            return [Gesture("key", ev.device, t_down, ev.time, key=KEYS[ev.code])]
        return []
