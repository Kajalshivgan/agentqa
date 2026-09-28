from agents.recorder.generator import generate_rule_based, robot_escape, strip_fences, validate_robot

ACTIONS = [
    {"action": "tap", "locator_type": "text", "locator_value": "Display",
     "screen_before": ["Settings", "Display", "Sound"], "screen_after": ["Display", "Brightness level", "Adaptive brightness"]},
    {"action": "tap", "locator_type": "text", "locator_value": "Brightness level",
     "screen_before": ["Display", "Brightness level", "Adaptive brightness"],
     "screen_after": ["Brightness level", "Display"]},
    {"action": "tap", "locator_type": "point", "locator_value": "700,400",
     "screen_before": ["Display"], "screen_after": ["Display"], "warning": "stale_snapshot: verify"},
    {"action": "input_text", "text": "My car #1"},
    {"action": "press_key", "key": "back", "screen_before": ["Display"], "screen_after": ["Settings", "Display"]},
]


def test_rule_based_output_is_valid_robot():
    suite = generate_rule_based(ACTIONS, "display_settings")
    assert validate_robot(suite) == []
    assert "Display Settings\n" in suite
    assert "    Tap On Text    Display\n    Assert Visible    Brightness level\n" in suite
    assert "Tap On Point    700    400" in suite
    assert "# NOTE: tapped before the screen was captured" in suite
    assert "Input Text    My car \\#1" in suite
    assert "Press Back\n    Assert Visible    Settings" in suite


def test_stale_tap_replays_coordinates_not_the_guessed_label():
    stale = {"action": "tap", "x": 856, "y": 209, "locator_type": "text", "locator_value": "Brightness level",
             "warning": "stale_snapshot: acted before the UI was re-captured; verify this step"}
    suite = generate_rule_based([stale], "t")
    assert "Wait Until Screen Is Stable\n    Tap On Point    856    209" in suite
    assert "Tap On Text    Brightness level" not in suite
    assert "possibly 'Brightness level', unverified" in suite


def test_validate_flags_unknown_keywords_and_missing_library():
    bad = "*** Test Cases ***\nT\n    Click Element    foo\n"
    errors = validate_robot(bad)
    assert any("unknown keyword 'Click Element'" in e for e in errors)
    assert any("MobileUILibrary" in e for e in errors)


def test_robot_escape():
    assert robot_escape("a  b") == "a \\ b"
    assert robot_escape("${x}") == "\\${x}"
    assert robot_escape("") == "${EMPTY}"


def test_strip_fences():
    assert strip_fences("Here:\n```robot\n*** Test Cases ***\n```\n") == "*** Test Cases ***\n"
