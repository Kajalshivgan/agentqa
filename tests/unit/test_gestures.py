from agents.recorder.gestures import GestureTracker, parse_line
from engine.adb_client import parse_getevent_devices

DEV = "/dev/input/event2"
GETEVENT_P = """add device 1: /dev/input/event1
  name:     "qwerty2"
  events:
    KEY (0001): KEY_BACK KEY_HOME
add device 2: /dev/input/event2
  name:     "virtio_input_multi_touch_1"
  events:
    ABS (0003): ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_X     : value 0, min 0, max 32767, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 32767, fuzz 0, flat 0, resolution 0
"""


def lines(*events):
    out, t = [], 100.0
    for dt, typ, code, value in events:
        t += dt
        out.append(f"[{t:14.6f}] {DEV}: {typ:<12} {code:<20} {value}")
    return out


def tap_events(x, y, hold=0.08, move_to=None):
    ev = [(0, "EV_ABS", "ABS_MT_TRACKING_ID", "00000001"),
          (0, "EV_ABS", "ABS_MT_POSITION_X", f"{x:08x}"),
          (0, "EV_ABS", "ABS_MT_POSITION_Y", f"{y:08x}"),
          (0, "EV_KEY", "BTN_TOUCH", "DOWN"),
          (0, "EV_SYN", "SYN_REPORT", "00000000")]
    if move_to:
        ev += [(hold / 2, "EV_ABS", "ABS_MT_POSITION_X", f"{move_to[0]:08x}"),
               (0, "EV_ABS", "ABS_MT_POSITION_Y", f"{move_to[1]:08x}"),
               (0, "EV_SYN", "SYN_REPORT", "00000000")]
    ev += [(hold, "EV_ABS", "ABS_MT_TRACKING_ID", "ffffffff"),
           (0, "EV_KEY", "BTN_TOUCH", "UP"),
           (0, "EV_SYN", "SYN_REPORT", "00000000")]
    return ev


def run(events):
    devices = parse_getevent_devices(GETEVENT_P)
    scale = lambda d, x, y: (x * 1407 // 32767, y * 791 // 32767)
    tracker = GestureTracker(scale, set(devices))
    gestures = []
    for line in lines(*events):
        gestures += tracker.feed(parse_line(line))
    return gestures


def test_parse_devices_finds_only_touchscreen_with_ranges():
    devices = parse_getevent_devices(GETEVENT_P)
    assert list(devices) == [DEV]
    assert (devices[DEV].max_x, devices[DEV].max_y, devices[DEV].name) == (32767, 32767, "virtio_input_multi_touch_1")


def test_parse_line():
    ev = parse_line("[   5102.183921] /dev/input/event2: EV_ABS       ABS_MT_POSITION_X    00004000")
    assert (ev.device, ev.code, ev.int_value) == (DEV, "ABS_MT_POSITION_X", 0x4000)
    assert parse_line("add device 1: /dev/input/event1") is None


def test_tap():
    (g,) = run(tap_events(16000, 8000))
    assert g.kind == "tap" and g.start == (16000, 8000)


def test_second_tap_at_same_spot_without_resent_coordinates():
    # The kernel omits unchanged axes, so the 2nd tap has no POSITION events at all.
    second = [e for e in tap_events(16000, 8000) if "POSITION" not in e[2]]
    gestures = run(tap_events(16000, 8000) + second)
    assert [g.kind for g in gestures] == ["tap", "tap"]
    assert gestures[1].start == (16000, 8000)


def test_touch_without_any_position_still_reports_finger_up():
    # First tap after the recorder starts, at the same raw spot as the tap before it started:
    # the kernel sends no POSITION events. It must not leave the tracker "finger down".
    positionless = [e for e in tap_events(16000, 8000) if "POSITION" not in e[2]]
    gestures = run(positionless + tap_events(20000, 9000))
    assert [g.kind for g in gestures] == ["unknown", "tap"]
    assert gestures[1].start == (20000, 9000)


def test_parse_system_bars_from_dumpsys():
    from engine.adb_client import parse_system_bars
    # Excerpt of `dumpsys window windows` on the AAOS emulator.
    text = """  Window #1 Window{92660ee u0 ScreenDecorOverlay}:
    Frames: parent=[0,0][1408,792] display=[0,0][1408,792] frame=[0,0][1408,10] last=[0,0][1408,10]
  Window #2 Window{52f8867 u0 BottomCarSystemBar}:
    Frames: parent=[0,0][1408,792] display=[0,0][1408,792] frame=[0,696][1408,792] last=[0,696][1408,792]
  Window #5 Window{db7637c u0 TopCarSystemBar}:
    Frames: parent=[0,0][1408,792] display=[0,0][1408,792] frame=[0,0][1408,76] last=[0,0][1408,76]
  Window #8 Window{8dd0494 u10 com.android.car.settings/com.android.car.settings.Home}:
    Frames: parent=[0,0][1408,792] display=[0,0][1408,792] frame=[0,0][1408,792] last=[0,0][1408,792]
"""
    assert parse_system_bars(text) == {"BottomCarSystemBar": (0, 696, 1408, 792),
                                       "TopCarSystemBar": (0, 0, 1408, 76)}


def test_long_press():
    (g,) = run(tap_events(16000, 8000, hold=0.9))
    assert g.kind == "long_press" and g.duration_ms >= 600


def test_swipe():
    (g,) = run(tap_events(16000, 25000, hold=0.3, move_to=(16000, 5000)))
    assert g.kind == "swipe" and g.end == (16000, 5000)


def test_key_back_from_other_device():
    tracker = GestureTracker(lambda d, x, y: (x, y), {DEV})
    down = parse_line("[ 1.0] /dev/input/event1: EV_KEY       KEY_BACK             DOWN")
    up = parse_line("[ 1.1] /dev/input/event1: EV_KEY       KEY_BACK             UP")
    assert tracker.feed(down) == []
    (g,) = tracker.feed(up)
    assert (g.kind, g.key) == ("key", "back")
