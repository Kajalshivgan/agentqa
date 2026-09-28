"""Turn a raw gesture + the UI tree it happened on into locator fields for the action log.

Shared by the live recorder and the post-recording repair pass, so both describe steps the
same way.
"""

from __future__ import annotations

from engine.ui_tree import UITree


def resolve_slider(tree: UITree, x: int, y: int) -> dict | None:
    """A touch on a slider sets a value: record which slider and where (as % of its width)."""
    slider = tree.slider_at(x, y)
    if slider is None:
        return None
    x1, _, x2, _ = slider.bounds
    percent = round(100 * min(max(x - x1, 0), x2 - x1) / max(x2 - x1, 1))
    label = tree.slider_label(slider)
    fields = {"action": "set_slider", "percent": percent,
              "locator_type": "text" if label else "id", "locator_value": label or slider.resource_id,
              "element": {"class": slider.class_name, "resource_id": slider.resource_id, "text": "",
                          "content_desc": slider.content_desc, "window": slider.window.type or "TYPE_APPLICATION"}}
    if not label:
        same = [s for s in tree.sliders() if s.resource_id == slider.resource_id]
        if len(same) > 1:
            fields["locator_index"] = same.index(slider) + 1
    if not fields["locator_value"]:
        fields.update(locator_type="point", locator_value=f"{x},{y}")
    return fields


def resolve_tap(tree: UITree, x: int, y: int) -> dict:
    slider = resolve_slider(tree, x, y)
    if slider is not None:
        return slider
    loc = tree.locator_for_point(x, y)
    fields = {"locator_type": loc.type, "locator_value": loc.value, "unique": loc.unique}
    if loc.type != "point" and not loc.unique:
        fields["locator_index"] = loc.index
    if loc.element is not None:
        el = loc.element
        fields["element"] = {"class": el.class_name, "resource_id": el.resource_id, "text": el.text,
                             "content_desc": el.content_desc, "window": el.window.type or "TYPE_APPLICATION"}
    return fields


def swipe_direction(start: list[int] | tuple[int, int], end: list[int] | tuple[int, int]) -> str:
    """Direction the *content* moves (finger bottom->top = "up", revealing items further down)."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    if abs(dy) >= abs(dx):
        return "up" if dy < 0 else "down"
    return "left" if dx < 0 else "right"


def resolve_swipe(tree: UITree, start: list[int], end: list[int]) -> dict:
    fields = {"direction": swipe_direction(start, end)}
    box = tree.scrollable_at(*start)
    if box is not None and box.resource_id:
        same_id = [e for e in tree.app_elements if e.scrollable and e.resource_id == box.resource_id]
        fields["container"] = box.resource_id
        if len(same_id) > 1 and box in same_id:
            fields["container_index"] = same_id.index(box) + 1
    return fields
