from pathlib import Path

from engine.ui_tree import UITree

XML = (Path(__file__).parent / "fixtures" / "settings_home.xml").read_text(encoding="utf-8")


def test_parses_all_nodes_and_bounds():
    tree = UITree(XML)
    assert len(tree.elements) == 11
    display = tree.find(text="Display")
    assert display.bounds == (130, 140, 500, 200)
    assert display.center == (315, 170)


def test_find_by_short_resource_id():
    tree = UITree(XML)
    assert tree.find(resource_id="car_ui_toolbar_search").content_desc == "Search"
    assert len(tree.find_all(resource_id="android:id/title")) == 2


def test_containing_point_returns_smallest_element():
    tree = UITree(XML)
    assert tree.find_containing_point(200, 170).text == "Display"
    assert tree.find_containing_point(5000, 5000) is None


def test_locator_prefers_unique_text():
    loc = UITree(XML).locator_for_point(200, 170)
    assert (loc.type, loc.value, loc.unique) == ("text", "Display", True)


def test_tap_on_row_padding_resolves_to_child_label():
    # (1000, 170) is inside the clickable Display row but outside the TextView itself.
    loc = UITree(XML).locator_for_point(1000, 170)
    assert (loc.type, loc.value) == ("text", "Display")


def test_icon_prefers_human_label_over_id():
    loc = UITree(XML).locator_for_point(1330, 70)
    assert (loc.type, loc.value) == ("desc", "Search")


def test_unlabeled_icon_falls_back_to_id():
    loc = UITree(XML).locator_for_point(70, 170)  # Display row icon: label found via the row
    assert (loc.type, loc.value) == ("text", "Display")


# ---- real dump from the AAOS emulator (Android 15, 1408x792) ----------------

REAL = (Path(__file__).parent / "fixtures" / "aaos_settings_real.xml").read_text(encoding="utf-8")


def test_real_row_padding_resolves_to_row_label():
    loc = UITree(REAL).locator_for_point(480, 604)
    assert (loc.type, loc.value, loc.unique) == ("text", "Display", True)


def test_real_toolbar_icon_uses_label_from_wrapper():
    loc = UITree(REAL).locator_for_point(432, 124)
    assert loc.type in ("text", "desc") and loc.value == "Search"


def test_real_rotary_container_marker_is_ignored():
    texts = UITree(REAL).visible_texts()
    assert "com.android.car.ui.utils.ROTARY_CONTAINER" not in texts
    assert texts[:3] == ["Connected devices", "Network & internet", "Notifications"]


def test_edit_text_is_located_by_id_not_by_its_contents():
    loc = UITree(XML).locator_for_point(500, 370)
    assert loc.type == "id" and loc.value.endswith("name_field")


def test_visible_texts_in_document_order():
    assert UITree(XML).visible_texts() == ["Settings", "Search", "Display", "Sound"]
