"""Parse a UIAutomator XML dump into a queryable element tree.

Handles both dump formats:
  * `uiautomator dump`           -> one <hierarchy> for the focused app window
  * `uiautomator dump --windows` -> several <window> elements (app + system bars, keyboard...),
                                    each wrapping its own <hierarchy>
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
# Framework markers such as AAOS's "com.android.car.ui.utils.ROTARY_CONTAINER" content-desc.
_INTERNAL_MARKER = re.compile(r"^[a-z][a-z0-9_]*(\.[A-Za-z0-9_]+){2,}$")
_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")


def is_human_label(value: str) -> bool:
    value = value.strip()
    return bool(value) and not _INTERNAL_MARKER.match(value)


def _bounds(value: str) -> tuple[int, int, int, int]:
    m = _BOUNDS.match(value or "")
    return tuple(int(v) for v in m.groups()) if m else (0, 0, 0, 0)  # type: ignore[return-value]


@dataclass(frozen=True, eq=False)  # identity equality: two identical-looking windows stay distinct
class Window:
    title: str
    type: str  # e.g. TYPE_APPLICATION, TYPE_SYSTEM, TYPE_INPUT_METHOD
    layer: int  # higher = drawn on top
    bounds: tuple[int, int, int, int]
    active: bool

    @property
    def is_app(self) -> bool:
        return self.type == "TYPE_APPLICATION"


APP_WINDOW = Window("", "TYPE_APPLICATION", 0, (0, 0, 0, 0), True)  # plain single-window dumps


@dataclass
class Element:
    text: str
    resource_id: str
    content_desc: str
    class_name: str
    package: str
    bounds: tuple[int, int, int, int]
    clickable: bool
    enabled: bool
    focused: bool
    checked: bool
    scrollable: bool
    depth: int
    window: Window = field(default=APP_WINDOW, repr=False)
    parent: "Element | None" = field(default=None, repr=False)
    children: list["Element"] = field(default_factory=list, repr=False)

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bounds
        return max(0, x2 - x1) * max(0, y2 - y1)

    @property
    def is_edit_text(self) -> bool:
        return "EditText" in self.class_name

    def contains(self, x: int, y: int) -> bool:
        x1, y1, x2, y2 = self.bounds
        return x1 <= x < x2 and y1 <= y < y2

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def get(self, key: str, default=None):
        """dict-style access using UIAutomator attribute names."""
        mapping = {"text": self.text, "resource-id": self.resource_id, "content-desc": self.content_desc,
                   "class": self.class_name, "package": self.package}
        return mapping.get(key, default) or default


@dataclass
class Locator:
    type: str  # "text" | "id" | "desc" | "point"
    value: str
    element: Element | None
    unique: bool
    index: int = 1  # which occurrence (1-based, document order) when the value isn't unique


class UITree:
    def __init__(self, xml: str):
        self.xml = xml
        self.elements: list[Element] = []
        self.windows: list[Window] = []
        # Wrap in case of several top-level elements, then look for <window>s at any depth
        # (Android 15: <displays><display id="0"><window>...). Only the main display counts.
        root = ET.fromstring("<dump>" + _XML_DECL.sub("", xml, count=1) + "</dump>")
        displays = list(root.iter("display"))
        scope = next((d for d in displays if d.get("id") == "0"), displays[0]) if displays else root
        window_nodes = list(scope.iter("window"))
        for top in window_nodes:
            win = Window(top.get("title", ""), top.get("type", ""), int(top.get("layer", "0") or 0),
                         _bounds(top.get("bounds", "")), top.get("active") == "true")
            self.windows.append(win)
            for hierarchy in top.iter("hierarchy"):
                for node in hierarchy:
                    self._walk(node, None, 0, win)
        if not window_nodes:
            for hierarchy in root.iter("hierarchy"):
                for node in hierarchy:
                    self._walk(node, None, 0, APP_WINDOW)

    def _walk(self, node: ET.Element, parent: Element | None, depth: int, window: Window) -> None:
        if node.tag != "node":
            return
        el = Element(
            text=node.get("text", ""),
            resource_id=node.get("resource-id", ""),
            content_desc=node.get("content-desc", ""),
            class_name=node.get("class", ""),
            package=node.get("package", ""),
            bounds=_bounds(node.get("bounds", "")),
            clickable=node.get("clickable") == "true",
            enabled=node.get("enabled", "true") == "true",
            focused=node.get("focused") == "true",
            checked=node.get("checked") == "true",
            scrollable=node.get("scrollable") == "true",
            depth=depth,
            window=window,
            parent=parent,
        )
        if parent is not None:
            parent.children.append(el)
        self.elements.append(el)
        for child in node:
            self._walk(child, el, depth + 1, window)

    @property
    def has_windows(self) -> bool:
        """True for a --windows dump, which also covers system bars / keyboard."""
        return bool(self.windows)

    @property
    def app_elements(self) -> list[Element]:
        """Elements of application windows only (no system bars: their clock etc. always change)."""
        return [e for e in self.elements if e.window.is_app]

    # ---- queries ---------------------------------------------------------

    def find_all(self, *, text: str | None = None, resource_id: str | None = None,
                 content_desc: str | None = None, partial: bool = False) -> list[Element]:
        def match(actual: str, wanted: str | None) -> bool:
            if wanted is None:
                return True
            return wanted in actual if partial else actual == wanted

        return [e for e in self.elements
                if e.area > 0
                and match(e.text, text)
                and match(e.content_desc, content_desc)
                and (resource_id is None or e.resource_id == resource_id
                     or e.resource_id.endswith(":id/" + resource_id))]

    def find(self, index: int = 1, **kwargs) -> Element | None:
        found = self.find_all(**kwargs)
        return found[index - 1] if len(found) >= index else None

    def visible_texts(self) -> list[str]:
        """Human-readable texts of the app window(s), in document order."""
        seen, texts = set(), []
        for e in self.app_elements:
            for value in (e.text, e.content_desc):
                value = value.strip()
                if is_human_label(value) and value not in seen and e.area > 0:
                    seen.add(value)
                    texts.append(value)
        return texts

    def window_at(self, x: int, y: int) -> Window | None:
        """Topmost window containing the point (the one that receives a tap there)."""
        hits = [w for w in self.windows if w.bounds[0] <= x < w.bounds[2] and w.bounds[1] <= y < w.bounds[3]]
        return max(hits, key=lambda w: w.layer) if hits else None

    def find_containing_point(self, x: int, y: int) -> Element | None:
        """Smallest (then deepest) element under the point, in the window that receives the tap."""
        target = self.window_at(x, y)
        hits = [e for e in self.elements if e.area > 0 and e.contains(x, y)
                and (target is None or e.window is target)]
        if not hits:
            return None
        return min(hits, key=lambda e: (e.area, -e.depth))

    def scrollable_at(self, x: int, y: int) -> Element | None:
        """Innermost scrollable container under the point (what a swipe there scrolls)."""
        hit = self.find_containing_point(x, y)
        chain = [hit, *hit.ancestors()] if hit else []
        return next((e for e in chain if e.scrollable), None)

    # ---- sliders -----------------------------------------------------------

    @staticmethod
    def is_slider(el: Element) -> bool:
        return any(k in el.class_name for k in ("SeekBar", "Slider", "RangeSlider"))

    def slider_at(self, x: int, y: int) -> Element | None:
        hit = self.find_containing_point(x, y)
        chain = [hit, *hit.ancestors()] if hit else []
        return next((e for e in chain if self.is_slider(e)), None)

    def slider_label(self, slider: Element) -> str:
        """The text naming a slider: the first human label in the closest enclosing row."""
        for i, a in enumerate(slider.ancestors()):
            if i >= 3:
                break
            if sum(1 for d in a.descendants() if self.is_slider(d)) > 1:
                break  # this container holds several sliders: its labels aren't specific
            label = next((d.text for d in a.descendants() if is_human_label(d.text) and not self.is_slider(d)), "")
            if label:
                return label
        return ""

    def sliders(self) -> list[Element]:
        return [e for e in self.elements if self.is_slider(e) and e.area > 0]

    def locator_for_point(self, x: int, y: int) -> Locator:
        """Pick the most stable, human-readable locator for whatever sits under (x, y).

        Candidates, nearest first: the hit element, its clickable ancestor and that ancestor's
        subtree (e.g. a row whose label is a child TextView), then a few ancestors up (e.g. an
        icon whose label sits on a wrapper). A unique human label (text / content-desc) on any
        candidate wins over a unique resource-id. A repeated label/id is still usable with its
        occurrence index (e.g. the 2nd "Lower temperature"); raw coordinates are the last resort.
        """
        hit = self.find_containing_point(x, y)
        if hit is None:
            return Locator("point", f"{x},{y}", None, False)
        hit = self._snap_near_miss(hit, x, y)

        candidates: list[Element] = [hit]
        clickable = hit if hit.clickable else next((a for a in hit.ancestors() if a.clickable), None)
        if clickable is not None and clickable is not hit:
            candidates.append(clickable)
        if clickable is not None:
            candidates.extend(d for d in clickable.descendants()
                              if d not in candidates and (d.contains(x, y) or d.text))
        # A wrapper's label/id describes this control only if it wraps no *other* clickable control
        # (an icon inside a labelled frame: yes; a panel holding -, 63, + buttons: no).
        for i, a in enumerate(hit.ancestors()):
            if i >= 3:
                break
            if a in candidates or not a.contains(x, y):
                continue
            controls = [e for e in (a, *a.descendants()) if e.clickable]
            if len(controls) <= 1 or (clickable is not None and all(
                    e is clickable or e in clickable.descendants() or e in clickable.ancestors() for e in controls)):
                candidates.append(a)
                # `a` wraps only this one control, so a sibling label under `a` (e.g. the
                # "Appium Settings" TextView next to its icon) names *this* control, not a's
                # own (usually empty) text/id.
                candidates.extend(d for d in a.descendants() if d not in candidates and d.text)

        # Unique label > unique id > n-th occurrence of a label > n-th occurrence of an id.
        indexed: dict[str, Locator] = {}
        for kinds in (("text", "desc"), ("id",)):
            for el in candidates:
                for loc_type, value, matches in self._locators_of(el):
                    if loc_type not in kinds:
                        continue
                    if len(matches) == 1:
                        return Locator(loc_type, value, el, True)
                    if kinds[0] not in indexed and el in matches:
                        indexed[kinds[0]] = Locator(loc_type, value, el, False, matches.index(el) + 1)
        return indexed.get("text") or indexed.get("id") or Locator("point", f"{x},{y}", hit, False)

    def _snap_near_miss(self, hit: Element, x: int, y: int, radius: int = 40) -> Element:
        """A finger landing just beside a button (in a bar's padding) hits only a big container.

        If nothing button-sized is clickable at the point, use the nearest clickable element
        within `radius` px instead - replaying the container would tap its center, which is
        usually a different button.
        """
        own = hit if hit.clickable else next((a for a in hit.ancestors() if a.clickable), None)
        win = hit.window.bounds if hit.window.bounds != (0, 0, 0, 0) else None
        win_area = max(1, (win[2] - win[0]) * (win[3] - win[1])) if win else None
        if own is not None and (win_area is None or own.area < win_area * 0.25):
            return hit  # already on a real button / row

        def distance(e: Element) -> float:
            x1, y1, x2, y2 = e.bounds
            dx, dy = max(x1 - x, 0, x - x2), max(y1 - y, 0, y - y2)
            return (dx * dx + dy * dy) ** 0.5

        near = [e for e in self.elements if e.clickable and e.window is hit.window and e.area > 0
                and (win_area is None or e.area < win_area * 0.25) and distance(e) <= radius]
        return min(near, key=lambda e: (distance(e), e.area)) if near else hit

    def _locators_of(self, el: Element):
        if is_human_label(el.text) and not el.is_edit_text:
            yield "text", el.text, self.find_all(text=el.text)
        if is_human_label(el.content_desc):
            yield "desc", el.content_desc, self.find_all(content_desc=el.content_desc)
        if el.resource_id:
            yield "id", el.resource_id, self.find_all(resource_id=el.resource_id)
