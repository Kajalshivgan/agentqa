"""Sliders (SeekBar): recorded as `Set Slider <label> <percent>`, not as a tap on the row label."""

from pathlib import Path

from agents.recorder.generator import generate_rule_based, validate_robot
from agents.recorder.resolve import resolve_tap
from engine.ui_tree import UITree

# Real AAOS Settings > Sound page: three SeekBars sharing id com.android.car.settings:id/seekbar.
SOUND = UITree((Path(__file__).parent / "fixtures" / "aaos_sound_page.xml").read_text(encoding="utf-8"))


def test_three_sliders_each_named_by_its_row():
    labels = [SOUND.slider_label(s) for s in SOUND.sliders()]
    assert len(labels) == 3 and labels[0] == "Media volume"
    assert len(set(labels)) == 3  # each slider gets its own row's label


def test_tap_on_slider_records_value_not_label_tap():
    step = resolve_tap(SOUND, 1123, 395)  # the user's step 5 in my_flow4
    assert step["action"] == "set_slider"
    assert (step["locator_type"], step["locator_value"]) == ("text", "Media volume")
    assert step["percent"] == 72  # (1123 - 747) / (1272 - 747)


def test_tap_on_slider_label_is_still_a_normal_tap():
    step = resolve_tap(SOUND, 856, 382 - 40)  # above the slider track, on the title text
    assert step.get("action") != "set_slider"


def test_generator_emits_set_slider():
    actions = [{"action": "set_slider", "x": 1123, "y": 395, "percent": 72,
                "locator_type": "text", "locator_value": "Media volume"}]
    suite = generate_rule_based(actions, "t")
    assert "Set Slider    Media volume    72" in suite and validate_robot(suite) == []
