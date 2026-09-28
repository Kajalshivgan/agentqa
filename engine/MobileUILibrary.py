"""Robot Framework keyword library for Android UIs, driven purely over ADB + UIAutomator.

Usage in a suite:
    Library    engine.MobileUILibrary    device=emulator-5554    timeout=10
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from robot.api import logger
from robot.api.deco import keyword, library

from engine.adb_client import ADBClient
from engine.image_match import compare_images, find_template, parse_region
from engine.ui_tree import Element, UITree
from engine.wait import WaitTimeout, wait_until

ROOT = Path(__file__).resolve().parent.parent


@library(scope="SUITE", auto_keywords=False)
class MobileUILibrary:
    def __init__(self, device: str | None = None, timeout: float = 10, poll_interval: float = 0.3,
                templates_dir: str = "templates", baselines_dir: str = "baselines",
                update_baselines: bool = False):
        self._device = device or None
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self._adb: ADBClient | None = None
        self._screenshot_count = 0
        self.templates_dir = Path(templates_dir) if os.path.isabs(templates_dir) else ROOT / templates_dir
        self.baselines_dir = Path(baselines_dir) if os.path.isabs(baselines_dir) else ROOT / baselines_dir
        # Robot passes library args as strings; "false"/"False"/"" must not become truthy.
        self.update_baselines = str(update_baselines).strip().lower() in ("true", "1", "yes")

    @property
    def adb(self) -> ADBClient:
        if self._adb is None:  # connect lazily so `robot --dryrun` works without a device
            self._adb = ADBClient(self._device)
        return self._adb

    # ---- helpers ---------------------------------------------------------

    def _tree(self) -> UITree:
        return UITree(self.adb.dump_ui())

    def _wait_for(self, description: str, timeout: float | None, index: int = 1, **query) -> Element:
        def lookup():
            return self._tree().find(index, **query)
        try:
            return wait_until(lookup, timeout or self.timeout, self.poll_interval,
                              f"Element {description} not found")
        except WaitTimeout:
            self.take_screenshot()
            raise

    def _find_text_or_desc(self, text: str, index: int = 1) -> Element | None:
        tree = self._tree()
        return tree.find(index, text=text) or tree.find(index, content_desc=text)

    # ---- tap keywords ----------------------------------------------------

    @keyword("Tap On Text")
    def tap_on_text(self, text: str, index: int = 1, timeout: float | None = None):
        """Wait for an element whose text (or content-desc) equals ``text`` and tap its center.

        ``index`` picks the n-th match (1-based, screen order) when the text appears more than once.
        """
        el = wait_until(lambda: self._find_text_or_desc(text, int(index)), float(timeout or self.timeout),
                        self.poll_interval, f"Text '{text}' (#{index}) not found")
        self.adb.tap(*el.center)

    @keyword("Tap On Id")
    def tap_on_id(self, resource_id: str, index: int = 1, timeout: float | None = None):
        """Tap the element with this resource-id (full ``pkg:id/name`` or just ``name``)."""
        el = self._wait_for(f"id='{resource_id}' (#{index})", timeout and float(timeout), int(index),
                            resource_id=resource_id)
        self.adb.tap(*el.center)

    @keyword("Tap On Description")
    def tap_on_description(self, content_desc: str, index: int = 1, timeout: float | None = None):
        """Tap the element whose content-desc (accessibility label) equals ``content_desc``."""
        el = self._wait_for(f"desc='{content_desc}' (#{index})", timeout and float(timeout), int(index),
                            content_desc=content_desc)
        self.adb.tap(*el.center)

    @keyword("Set Slider")
    def set_slider(self, slider: str, percent: float, index: int = 1, timeout: float | None = None):
        """Touch the slider named ``slider`` at ``percent`` (0-100) of its width.

        ``slider`` is the label shown with the slider (e.g. ``Media volume``) or its resource-id;
        ``index`` picks the n-th slider when several share that id.
        """
        def find():
            tree = self._tree()
            by_label = [s for s in tree.sliders() if tree.slider_label(s) == slider]
            by_id = [s for s in tree.sliders()
                     if s.resource_id == slider or s.resource_id.endswith(":id/" + slider)]
            found = by_label or by_id
            return found[int(index) - 1] if len(found) >= int(index) else None

        el = wait_until(find, float(timeout or self.timeout), self.poll_interval, f"Slider '{slider}' not found")
        x1, y1, x2, y2 = el.bounds
        x = x1 + round((x2 - x1 - 1) * min(max(float(percent), 0), 100) / 100)
        self.adb.tap(x, (y1 + y2) // 2)

    @keyword("Scroll To Text")
    def scroll_to_text(self, text: str, direction: str = "up", container: str | None = None,
                       container_index: int = 1, max_swipes: int = 10):
        """Scroll until an element with this text (or content-desc) is visible; no-op if it already is.

        ``direction`` is the way the content moves: ``up`` reveals items further down a list.
        ``container`` (resource-id) and ``container_index`` pick the list when several share an id
        (e.g. the two panes of AAOS Settings). Without ``container``, every scrollable list is
        tried in turn, moving on when one reaches its end.
        """
        def visible(tree):
            return tree.find(text=text) or tree.find(content_desc=text)

        tree = self._tree()
        if visible(tree):
            return
        swipes = 0
        for target in self._scroll_candidates(tree, container, int(container_index)):
            before = None
            while swipes < int(max_swipes):
                box = self._find_same_container(tree, target)
                if box is None:
                    break
                self._swipe_in(box, direction)
                swipes += 1
                tree = self._tree()
                if visible(tree):
                    return
                content = tuple(e.text for e in tree.app_elements if e.text)
                if content == before:  # this list can't scroll any further
                    break
                before = content
        self.take_screenshot()
        raise AssertionError(f"'{text}' not found after {swipes} swipes {direction}")

    @keyword("Scroll")
    def scroll(self, direction: str = "up", container: str | None = None, container_index: int = 1):
        """Scroll one page in ``direction`` (content movement) inside ``container`` (resource-id)."""
        tree = self._tree()
        candidates = self._scroll_candidates(tree, container, int(container_index))
        if candidates:
            self._swipe_in(candidates[0], direction)
        else:
            w, h = self.adb.screen_size()
            self._swipe_box((0, 0, w, h), direction)

    @staticmethod
    def _scroll_candidates(tree: UITree, container: str | None, index: int = 1) -> list[Element]:
        scrollables = sorted((e for e in tree.app_elements if e.scrollable and e.area > 0),
                             key=lambda e: e.area, reverse=True)
        if container:
            named = [e for e in tree.app_elements if e.scrollable and
                     (e.resource_id == container or e.resource_id.endswith(":id/" + container))]
            if len(named) >= index:
                return [named[index - 1]]
        return scrollables

    @staticmethod
    def _find_same_container(tree: UITree, old: Element) -> Element | None:
        """The same list in a fresh dump (matched by id and position)."""
        return next((e for e in tree.app_elements if e.scrollable and e.resource_id == old.resource_id
                     and e.bounds == old.bounds), None) or old

    def _swipe_in(self, box: Element, direction: str) -> None:
        self._swipe_box(box.bounds, direction)

    def _swipe_box(self, bounds, direction: str) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        dx, dy = (x2 - x1) // 3, (y2 - y1) // 3
        start, end = {"up": ((cx, cy + dy), (cx, cy - dy)), "down": ((cx, cy - dy), (cx, cy + dy)),
                      "left": ((cx + dx, cy), (cx - dx, cy)), "right": ((cx - dx, cy), (cx + dx, cy))}[
            direction.lower()]
        self.adb.swipe(*start, *end, 400)
        time.sleep(0.5)

    @keyword("Tap On Point")
    def tap_on_point(self, x: int, y: int):
        """Tap raw screen coordinates. Last resort when an element has no usable locator.

        Unlike the locator keywords this can't wait for its target, so generated tests put
        `Wait Until Screen Is Stable` before it.
        """
        self.adb.tap(int(x), int(y))

    @keyword("Wait Until Screen Is Stable")
    def wait_until_screen_is_stable(self, timeout: float | None = None):
        """Wait until two consecutive UI captures show the same elements (screen finished loading).

        Compares each element's class, text, content-desc and bounds, so a page transition or a
        list still filling in counts as "not stable".
        """
        def signature():
            # App windows only: system bars contain a ticking clock.
            return tuple((e.class_name, e.text, e.content_desc, e.bounds) for e in self._tree().app_elements)

        previous = [signature()]

        def settled():
            current = signature()
            same, previous[0] = current == previous[0], current
            return same

        wait_until(settled, float(timeout or self.timeout), self.poll_interval, "Screen did not settle")

    @keyword("Long Press On Text")
    def long_press_on_text(self, text: str, duration_ms: int = 1000):
        el = wait_until(lambda: self._find_text_or_desc(text), self.timeout, self.poll_interval,
                        f"Text '{text}' not found")
        self.adb.long_press(*el.center, int(duration_ms))

    # ---- input / navigation ---------------------------------------------

    @keyword("Input Text")
    def input_text(self, text: str, resource_id: str | None = None):
        """Type ``text`` into the focused field, or tap the field with ``resource_id`` first."""
        if resource_id:
            self.tap_on_id(resource_id)
            time.sleep(0.3)
        self.adb.input_text(text)

    @keyword("Swipe")
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        self.adb.swipe(int(x1), int(y1), int(x2), int(y2), int(duration_ms))

    @keyword("Swipe Up")
    def swipe_up(self):
        """Scroll content up (finger moves bottom -> top) in the middle of the screen."""
        w, h = self.adb.screen_size()
        self.adb.swipe(w // 2, int(h * 0.75), w // 2, int(h * 0.25), 400)

    @keyword("Swipe Down")
    def swipe_down(self):
        w, h = self.adb.screen_size()
        self.adb.swipe(w // 2, int(h * 0.25), w // 2, int(h * 0.75), 400)

    @keyword("Press Back")
    def press_back(self):
        self.adb.keyevent("KEYCODE_BACK")

    @keyword("Press Home")
    def press_home(self):
        self.adb.keyevent("KEYCODE_HOME")

    @keyword("Launch App")
    def launch_app(self, package: str):
        self.adb.start_app(package)

    @keyword("Restart App")
    def restart_app(self, package: str, settle_s: float = 2):
        """Force-stop and relaunch ``package`` so the test starts from its launch screen.

        If ``package`` is the home launcher (the flow was recorded from the home screen), this
        just goes Home: the launcher can't be force-stopped and started like a normal app.
        """
        self.adb.keyevent("KEYCODE_HOME")
        if package != self.adb.home_package():
            self.adb.shell(f"am force-stop {package}")
            self.adb.start_app(package)
        time.sleep(float(settle_s))

    # ---- assertions ------------------------------------------------------

    @keyword("Assert Visible")
    def assert_visible(self, text: str, timeout: float | None = None):
        """Pass once an element with this text or content-desc is on screen."""
        try:
            wait_until(lambda: self._find_text_or_desc(text), float(timeout or self.timeout),
                       self.poll_interval, f"'{text}' is not visible")
        except WaitTimeout:
            self.take_screenshot()
            raise

    @keyword("Assert Not Visible")
    def assert_not_visible(self, text: str, timeout: float | None = None):
        try:
            wait_until(lambda: self._find_text_or_desc(text) is None, float(timeout or self.timeout),
                       self.poll_interval, f"'{text}' is still visible")
        except WaitTimeout:
            self.take_screenshot()
            raise

    @keyword("Assert Id Visible")
    def assert_id_visible(self, resource_id: str, timeout: float | None = None):
        self._wait_for(f"id='{resource_id}'", timeout and float(timeout), resource_id=resource_id)

    # ---- evidence --------------------------------------------------------

    @keyword("Take Screenshot")
    def take_screenshot(self, name: str | None = None) -> str:
        """Save a PNG next to the Robot log and embed it."""
        self._screenshot_count += 1
        out_dir = _output_dir()
        filename = f"{name or 'screenshot'}-{self._screenshot_count}.png"
        path = self.adb.screenshot(os.path.join(out_dir, filename))
        logger.info(f'<img src="{filename}" width="480">', html=True)
        return path

    # ---- image verification: template matching ---------------------------
    # For elements the UI dump can't describe at all - a map preview, a video thumbnail, a
    # custom-drawn icon with no text/resource-id. `templates_dir` holds one small reference PNG
    # per named element (crop it once from a screenshot); matching survives layout shifts that
    # would break a hardcoded `Tap On Point`.

    def _template_path(self, template: str) -> Path:
        path = Path(template)
        if not path.is_absolute():
            path = self.templates_dir / (template if template.endswith(".png") else f"{template}.png")
        if not path.is_file():
            raise FileNotFoundError(f"Template image not found: {path} (looked under {self.templates_dir})")
        return path

    def _find_image(self, template: str, threshold: float):
        template_path = self._template_path(template)
        with tempfile.TemporaryDirectory() as tmp:
            screenshot_path = self.adb.screenshot(os.path.join(tmp, "screen.png"))
            return find_template(screenshot_path, template_path, float(threshold))

    @keyword("Assert Image Visible")
    def assert_image_visible(self, template: str, threshold: float = 0.8, timeout: float | None = None):
        """Wait until `template` (a PNG under `templates_dir`) is found on screen.

        `threshold` is the match confidence (0-1; 0.8 is a reasonable default). Returns the
        match so `Tap On Image` can reuse it without a second screenshot.
        """
        match = wait_until(lambda: self._find_image(template, threshold), float(timeout or self.timeout),
                           self.poll_interval, f"Image '{template}' not found (threshold={threshold})")
        return match

    @keyword("Tap On Image")
    def tap_on_image(self, template: str, threshold: float = 0.8, timeout: float | None = None):
        """Tap the center of `template` wherever it's found on screen."""
        match = self.assert_image_visible(template, threshold, timeout)
        self.adb.tap(*match.center)

    # ---- image verification: visual regression ----------------------------
    # Whole-screen baseline diff - catches rendering bugs (wrong color, broken layout, a blank
    # render) that text/id checks can't see. Baselines live under `baselines_dir` and should be
    # committed to the repo; re-approve one deliberately by re-running with `update_baselines=true`
    # (Library import arg, or `--variable UPDATE_BASELINES:true`) rather than deleting it by hand.

    @keyword("Assert Screenshot Matches")
    def assert_screenshot_matches(self, name: str, threshold: float = 98.0, region: str | None = None):
        """Compare the current screen against the saved baseline `name`.

        `threshold` is the minimum similarity percent to pass. `region` is "left,top,right,bottom"
        to compare only part of the screen (e.g. excluding a ticking clock). With no baseline yet,
        or when `update_baselines` is set on the library, saves the current screenshot as the
        baseline instead of comparing, and the step passes.
        """
        self.baselines_dir.mkdir(parents=True, exist_ok=True)
        baseline_path = self.baselines_dir / f"{name}.png"
        out_dir = _output_dir()
        actual_path = self.adb.screenshot(os.path.join(out_dir, f"{name}-actual.png"))

        if self.update_baselines or not baseline_path.exists():
            import shutil
            shutil.copy(actual_path, baseline_path)
            reason = "update_baselines is set" if self.update_baselines else "no baseline yet"
            logger.warn(f"Saved '{name}' as the new baseline ({reason}). Re-run to actually verify it.")
            return

        parsed_region = parse_region(region)
        diff = compare_images(actual_path, baseline_path, region=parsed_region)
        logger.info(f'baseline: <img src="{baseline_path.name}" width="360"> '
                    f'actual: <img src="{os.path.basename(actual_path)}" width="360">', html=True)
        if diff.similarity_percent < float(threshold):
            diff_path = os.path.join(out_dir, f"{name}-diff.png")
            diff.diff_image.save(diff_path)
            logger.info(f'diff: <img src="{os.path.basename(diff_path)}" width="360">', html=True)
            raise AssertionError(f"Screenshot '{name}' is only {diff.similarity_percent:.1f}% similar to "
                                 f"the baseline (need >= {threshold}%)")


def _output_dir() -> str:
    try:
        from robot.libraries.BuiltIn import BuiltIn
        return BuiltIn().get_variable_value("${OUTPUT DIR}") or os.getcwd()
    except Exception:
        return os.getcwd()
