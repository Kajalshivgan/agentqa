"""Multi-window dumps (system bars), repeated labels, scrolling and the repair decision."""

from pathlib import Path

from agents.recorder.generator import generate_rule_based, validate_robot
from agents.recorder.repair import needs_repair
from agents.recorder.resolve import swipe_direction
from engine.ui_tree import UITree

# Real `uiautomator dump --windows` from the AAOS emulator (app grid open).
WINDOWS = UITree((Path(__file__).parent / "fixtures" / "aaos_windows_dump.xml").read_text(encoding="utf-8"))


def locate(x, y):
    loc = WINDOWS.locator_for_point(x, y)
    return loc.type, loc.value, loc.index


def test_windows_are_parsed_with_layers():
    assert [(w.type, w.layer) for w in WINDOWS.windows] == [
        ("TYPE_SYSTEM", 2), ("TYPE_SYSTEM", 1), ("TYPE_APPLICATION", 0)]
    assert WINDOWS.has_windows


def test_system_bar_button_gets_its_label():
    assert locate(693, 725) == ("desc", "Applications", 1)


def test_near_miss_beside_a_bar_button_snaps_to_it():
    # (730, 763) is in the bar's padding, just right of the Applications button.
    assert locate(730, 763) == ("desc", "Applications", 1)


def test_app_grid_icon_uses_sibling_label_not_id_index():
    # Regression: the icon and its container are both clickable, but the icon's own text/desc
    # are empty and its label is a *sibling* TextView, not a descendant of the icon itself.
    icon = next(e for e in WINDOWS.elements if e.resource_id.endswith("app_icon")
               and e.text == "" and e.content_desc == "")
    assert locate(*icon.center) == ("text", "Appium Settings", 1)


def test_repeated_label_uses_occurrence_index_not_a_wrapper():
    # Driver and passenger both have "Lower temperature"; the driver_hvac panel wraps 3 buttons.
    assert locate(300, 745) == ("desc", "Lower temperature", 1)
    assert locate(1000, 745)[1:] == ("63", 2)


def test_app_texts_exclude_system_bars():
    texts = WINDOWS.visible_texts()
    assert "Applications" not in texts and "Driver" not in texts
    assert texts[0] == "Appium Settings"


def test_swipe_direction_is_content_movement():
    assert swipe_direction([700, 600], [700, 200]) == "up"
    assert swipe_direction([700, 200], [700, 600]) == "down"
    assert swipe_direction([900, 400], [300, 420]) == "left"


def test_swipes_before_a_tap_become_scroll_to_text():
    actions = [
        {"action": "swipe", "from": [300, 600], "to": [300, 250], "direction": "up",
         "container": "com.x:id/list", "container_index": 2},
        {"action": "swipe", "from": [300, 600], "to": [300, 250], "direction": "up",
         "warning": "stale_snapshot: ..."},
        {"action": "tap", "x": 312, "y": 441, "locator_type": "text", "locator_value": "System"},
    ]
    suite = generate_rule_based(actions, "t")
    assert "Scroll To Text    System    direction=up    container=com.x:id/list    container_index=2" in suite
    assert "Tap On Text    System" in suite
    assert "Swipe    " not in suite and validate_robot(suite) == []


def test_lone_swipe_becomes_scroll_and_index_is_emitted():
    actions = [{"action": "swipe", "from": [300, 600], "to": [300, 250], "direction": "up"},
               {"action": "tap", "x": 1, "y": 1, "locator_type": "desc", "locator_value": "Lower temperature",
                "locator_index": 2, "warning": "stale_snapshot: x"}]
    suite = generate_rule_based(actions, "t")
    assert "Scroll    up" in suite


def test_needs_repair():
    assert needs_repair({"action": "tap", "locator_type": "point"})
    assert needs_repair({"action": "tap", "locator_type": "text", "warning": "stale_snapshot: x"})
    assert not needs_repair({"action": "tap", "locator_type": "text", "locator_value": "Wi-Fi"})
    assert needs_repair({"action": "swipe", "direction": "up"})
    assert not needs_repair({"action": "swipe", "direction": "up", "container": "com.x:id/list"})
    assert not needs_repair({"action": "input_text", "text": "hi"})
