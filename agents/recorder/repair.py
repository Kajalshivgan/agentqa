"""Locator repair: replace coordinate steps in a recording with text / content-desc / id locators.

Steps end up as coordinates when the user acted before the recorder had captured the new
screen (each capture takes ~2 s). This pass replays the recording on the device at a safe
pace: before each step it waits for the screen to settle, captures it, and - for steps that
need it - resolves what is under the recorded point, exactly as the live recorder would have.
It then performs the step with the recorded coordinates, so the replay follows the user's path.

If a step that already had a trusted locator no longer finds that element at its point, the
replay has diverged from the recording (different starting state, dialog, network...): the pass
stops there and leaves the remaining steps unchanged rather than guessing.
"""

from __future__ import annotations

import time

from engine.adb_client import ADBClient
from engine.ui_tree import UITree
from engine.wait import wait_for_stable
from agents.recorder.resolve import resolve_swipe, resolve_tap

STALE = "stale_snapshot"


def needs_repair(action: dict) -> bool:
    stale = STALE in action.get("warning", "")
    if action["action"] in ("tap", "long_press", "set_slider"):
        return stale or action.get("locator_type") == "point"
    if action["action"] == "swipe":
        return stale or not action.get("container")
    return False


def _locator_present(tree: UITree, action: dict) -> bool:
    kind, value = action.get("locator_type"), action.get("locator_value", "")
    if kind == "text":
        return bool(tree.find(text=value) or tree.find(content_desc=value))
    if kind == "desc":
        return bool(tree.find(content_desc=value))
    if kind == "id":
        return bool(tree.find(resource_id=value))
    return True


def _stable_tree(adb: ADBClient, timeout: float = 15) -> UITree:
    return wait_for_stable(lambda: UITree(adb.dump_ui()),
                           lambda t: tuple((e.class_name, e.text, e.content_desc, e.bounds) for e in t.app_elements),
                           timeout=timeout)


def _go_to_start(adb: ADBClient, start_package: str | None) -> None:
    adb.keyevent("KEYCODE_HOME")
    if start_package and start_package != adb.home_package():
        adb.shell(f"am force-stop {start_package}")
        adb.start_app(start_package)
    time.sleep(2)


def repair_recording(recording: dict, adb: ADBClient, log=print, everything: bool = False) -> int:
    """Repair `recording["actions"]` in place; returns the number of steps improved.

    `everything=True` re-resolves every tap/swipe with the current rules (e.g. to upgrade a
    recording made before slider support), not only coordinate/stale steps.
    """
    actions = recording["actions"]

    def todo_step(a: dict) -> bool:
        return needs_repair(a) or (everything and a["action"] in ("tap", "long_press", "set_slider", "swipe"))

    todo = [i for i, a in enumerate(actions) if todo_step(a)]
    if not todo:
        return 0
    what = "to re-check" if everything else "use screen coordinates"
    log(f"[repair] {len(todo)} step(s) {what}; replaying the flow once to find their text/id locators. "
        f"Please don't touch the device (~{3 * len(actions)} s).")
    _go_to_start(adb, recording.get("start_package"))

    repaired = 0
    for i, action in enumerate(actions):
        kind = action["action"]
        tree = _stable_tree(adb)

        if kind in ("tap", "long_press", "set_slider"):
            x, y = action["x"], action["y"]
            fresh = resolve_tap(tree, x, y)
            if kind == "long_press" and fresh.get("action") == "set_slider":
                fresh.pop("action")  # a long press stays a long press
            fresh.setdefault("action", kind)
            if todo_step(action):
                if fresh["locator_type"] != "point" and any(
                        fresh.get(k) != action.get(k) for k in ("action", "locator_type", "locator_value", "percent")):
                    old = action.get("locator_value")
                    for key in ("locator_index", "system_bar", "warning"):
                        action.pop(key, None)
                    action.update(fresh, screen_before=tree.visible_texts()[:25], repaired=True)
                    repaired += 1
                    pct = f" at {fresh['percent']}%" if "percent" in fresh else ""
                    log(f"[repair] step {i + 1}: {old!r} -> {fresh.get('action', kind)} "
                        f"{fresh['locator_type']} {fresh['locator_value']!r}{pct}")
                elif needs_repair(action) and fresh["locator_type"] != "point":
                    action.pop("warning", None)  # stale guess confirmed by the fresh capture
                    action.update(screen_before=tree.visible_texts()[:25], repaired=True)
                    repaired += 1
            elif not _locator_present(tree, action):
                log(f"[repair] step {i + 1}: {action.get('locator_value')!r} is not on screen - the replay no "
                    f"longer matches the recording; stopping here (remaining steps unchanged)")
                break
            if kind == "long_press":
                adb.long_press(x, y, action.get("duration_ms", 1000))
            elif action.get("from"):  # slider drag
                adb.swipe(*action["from"], x, y, 400)
            else:
                adb.tap(x, y)

        elif kind == "swipe":
            if todo_step(action):
                action.pop("warning", None)
                action.update(resolve_swipe(tree, action["from"], action["to"]),
                              screen_before=tree.visible_texts()[:25], repaired=True)
                repaired += 1
                log(f"[repair] step {i + 1}: swipe {action['direction']} in {action.get('container', 'screen')}")
            adb.swipe(*action["from"], *action["to"], action.get("duration_ms", 300))

        elif kind == "input_text":
            adb.input_text(action["text"])
        elif kind == "press_key":
            adb.keyevent({"back": "KEYCODE_BACK", "home": "KEYCODE_HOME"}.get(action["key"], action["key"]))
        time.sleep(0.5)

    # Screens changed for repaired steps: re-link each step's "after" screen to the next "before".
    for a, nxt in zip(actions, actions[1:]):
        if "screen_before" in nxt:
            a["screen_after"] = nxt["screen_before"]
    if actions:
        actions[-1]["screen_after"] = _stable_tree(adb).visible_texts()[:25]
    log(f"[repair] done: {repaired} of {len(todo)} step(s) now use text/id locators")
    return repaired
