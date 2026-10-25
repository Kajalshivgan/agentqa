"""Crawl every reachable screen of an Android app and save a text/id/content-desc inventory.

Why: the LLM test generator can only assert on texts that were actually seen during a *recorded*
flow - it has no way to know whether some other text it's tempted to invent genuinely exists
somewhere in the app. A one-time crawl of the whole app builds a ground-truth database of every
screen's real texts/ids, keyed by a fingerprint of that screen, plus how each screen was reached.
That database can then validate a generated `Assert Visible <text>` against "does this text
exist anywhere in this app at all" - a much stronger check than keyword-syntax validation alone -
and it's reusable by any generator backend (cloud or offline).

Policy (deliberately conservative - this taps real UI on a real device/emulator):
  * Only taps elements with a human-readable label (text/content-desc) or a resource-id; never
    raw coordinates, since a point can't be safely re-identified or skipped by name.
  * Never types into an EditText (it only taps).
  * Skips any element whose label/resource-id contains a denylisted word (reset, delete, forget,
    sign out, factory, format, ... - see DEFAULT_DENYLIST) so it can't trigger a destructive
    action. Extend with --deny, or narrow with --allow.
  * Skips Switch/CheckBox/ToggleButton-class elements by default (flipping a setting blind is a
    side effect, not navigation) - opt in with --include-toggles.
  * Assumes `Press Back` always returns to the exact parent screen (true for standard Settings-
    style navigation). If a tap leaves the target package entirely, it backs out immediately
    instead of exploring the other app.
  * Bounded by --max-depth / --max-screens / --max-actions so a bug (or a wrongly-missed
    denylist word) can't run forever.

Usage:
  python tools/crawl_app.py --package com.android.car.settings
  python tools/crawl_app.py --package com.android.car.settings --max-depth 4 --include-toggles
  python tools/crawl_app.py --package com.android.car.settings --deny "pair,bluetooth"

Output: recordings/ui_inventory/<package>.json (override with --out), keyed by screen
fingerprint, e.g.:
  {
    "package": "com.android.car.settings", "crawled_at": "...",
    "screens": {"<fp>": {"depth": 1, "texts": ["Display", "Brightness level", ...],
                          "resource_ids": ["android:id/title", ...]}},
    "edges": [{"from": "<fp>", "to": "<fp2>", "via": {"type": "text", "value": "Display"}}],
    "stats": {"screens": 12, "taps": 34, "skipped_denied": 2, "skipped_toggle": 5}
  }
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.adb_client import ADBClient  # noqa: E402
from engine.ui_tree import Element, UITree, is_human_label  # noqa: E402

DEFAULT_DENYLIST = {
    "reset", "erase", "delete", "forget", "remove device", "uninstall", "format", "wipe",
    "factory", "sign out", "log out", "logout", "power off", "shut down", "restart device",
    "unpair", "clear data", "clear storage", "clear cache", "deregister", "unlink",
}
TOGGLE_CLASSES = ("Switch", "CheckBox", "ToggleButton", "RadioButton")


class Locator:
    def __init__(self, type_: str, value: str, index: int = 1):
        self.type, self.value, self.index = type_, value, index

    def as_dict(self) -> dict:
        d = {"type": self.type, "value": self.value}
        if self.index > 1:
            d["index"] = self.index
        return d

    def __repr__(self) -> str:
        return f"{self.type}={self.value!r}" + (f"[{self.index}]" if self.index > 1 else "")


def fingerprint(tree: UITree) -> str:
    """A screen's identity: its sorted set of visible texts. Two screens with identical text
    content are treated as the same screen - a documented limitation, not a bug."""
    texts = tuple(sorted(tree.visible_texts()))
    return str(abs(hash(texts)) % (10**12)) + f":{len(texts)}"


def dump_stable(adb: ADBClient, settle: float, tries: int = 3) -> UITree:
    """Dump the UI, re-dumping until two consecutive captures agree (or `tries` is used up) -
    the same idea as `Wait Until Screen Is Stable`, standalone so this script has no Robot
    Framework dependency."""
    previous = None
    tree = UITree(adb.dump_ui())
    for _ in range(tries):
        current = tuple(sorted(tree.visible_texts()))
        if current == previous:
            return tree
        previous = current
        time.sleep(settle)
        tree = UITree(adb.dump_ui())
    return tree


def is_denied(label: str, resource_id: str, denylist: set[str]) -> bool:
    haystack = f"{label} {resource_id}".lower()
    return any(word in haystack for word in denylist)


def candidates_of(tree: UITree, package: str, denylist: set[str], include_toggles: bool,
                   stats: Counter) -> list[tuple[Locator, Element]]:
    """Every safely re-findable, clickable element on the current screen, paired with the
    locator that will be used to re-find it just before tapping."""
    out: list[tuple[Locator, Element]] = []
    for e in tree.app_elements:
        if not (e.clickable and e.enabled and e.area > 0) or e.package != package or e.is_edit_text:
            continue
        # The clickable element is often a row wrapper (e.g. RelativeLayout) around the actual
        # Switch widget, not the Switch itself - checking descendants is what actually catches it.
        if not include_toggles and any(k in d.class_name for d in (e, *e.descendants())
                                       for k in TOGGLE_CLASSES):
            stats["skipped_toggle"] += 1
            continue
        if is_human_label(e.text):
            loc_type, value = "text", e.text
        elif is_human_label(e.content_desc):
            loc_type, value = "desc", e.content_desc
        elif e.resource_id:
            loc_type, value = "id", e.resource_id
        else:
            stats["skipped_no_locator"] += 1
            continue
        if is_denied(e.text or e.content_desc, e.resource_id, denylist):
            stats["skipped_denied"] += 1
            continue
        # A repeated label/id is still safe to tap - just give each occurrence its own index.
        index = sum(1 for k, _ in out if (k.type, k.value) == (loc_type, value)) + 1
        out.append((Locator(loc_type, value, index), e))
    return out


def resolve(tree: UITree, locator: Locator) -> Element | None:
    kwargs = {"text": locator.value} if locator.type == "text" else \
             {"content_desc": locator.value} if locator.type == "desc" else {"resource_id": locator.value}
    return tree.find(index=locator.index, **kwargs)


def in_scope(tree: UITree, package: str) -> bool:
    packages = [e.package for e in tree.app_elements if e.package]
    if not packages:
        return False
    top_package, _ = Counter(packages).most_common(1)[0]
    return top_package == package


def crawl(adb: ADBClient, package: str, max_depth: int, max_screens: int, max_actions: int,
          settle: float, denylist: set[str], include_toggles: bool) -> dict:
    screens: dict[str, dict] = {}
    edges: list[dict] = []
    visited: set[str] = set()
    stats = Counter()

    def record_screen(tree: UITree, depth: int) -> str:
        fp = fingerprint(tree)
        if fp not in screens:
            screens[fp] = {
                "depth": depth,
                "texts": sorted(tree.visible_texts()),
                "resource_ids": sorted({e.resource_id for e in tree.app_elements
                                        if e.resource_id and e.package == package}),
            }
        return fp

    def explore(depth: int) -> str:
        tree = dump_stable(adb, settle)
        fp = record_screen(tree, depth)
        if fp in visited or depth >= max_depth or len(screens) > max_screens:
            return fp
        visited.add(fp)
        current = tree  # most recent known on-device state, refreshed after every action
        for locator, _ in candidates_of(tree, package, denylist, include_toggles, stats):
            if stats["taps"] >= max_actions:
                break
            elem = resolve(current, locator)
            if elem is None:
                stats["skipped_stale"] += 1
                continue
            adb.tap(*elem.center)
            stats["taps"] += 1
            after = dump_stable(adb, settle)
            after_fp = fingerprint(after)
            if after_fp == fp:
                current = after  # no observable navigation (disabled-looking control, a toast, ...)
                continue
            if not in_scope(after, package):
                adb.keyevent("KEYCODE_BACK")
                stats["skipped_left_scope"] += 1
                current = dump_stable(adb, settle)
                continue
            edges.append({"from": fp, "to": after_fp, "via": locator.as_dict()})
            explore(depth + 1)
            adb.keyevent("KEYCODE_BACK")
            current = dump_stable(adb, settle)  # re-sync in case Back didn't land exactly on `fp`
        return fp

    explore(0)
    return {"package": package, "crawled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "screens": screens, "edges": edges, "stats": dict(stats) | {"screens": len(screens)}}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--package", required=True, help="app to crawl, e.g. com.android.car.settings")
    p.add_argument("--device", help="adb serial (default: the only connected device)")
    p.add_argument("--max-depth", type=int, default=6)
    p.add_argument("--max-screens", type=int, default=150)
    p.add_argument("--max-actions", type=int, default=800, help="total taps across the whole crawl")
    p.add_argument("--settle", type=float, default=1.0, help="seconds between stability-check dumps")
    p.add_argument("--include-toggles", action="store_true",
                   help="also tap Switch/CheckBox/ToggleButton/RadioButton elements (off by "
                        "default: flipping a setting is a side effect, not navigation)")
    p.add_argument("--deny", help="comma-separated extra words to add to the denylist")
    p.add_argument("--allow", help="comma-separated words to remove from the default denylist")
    p.add_argument("--out", help="output path (default recordings/ui_inventory/<package>.json)")
    args = p.parse_args(argv)

    denylist = set(DEFAULT_DENYLIST)
    if args.deny:
        denylist |= {w.strip().lower() for w in args.deny.split(",") if w.strip()}
    if args.allow:
        denylist -= {w.strip().lower() for w in args.allow.split(",") if w.strip()}

    adb = ADBClient(args.device)
    print(f"[crawler] launching {args.package} ...")
    adb.keyevent("KEYCODE_HOME")
    if args.package != adb.home_package():
        adb.shell(f"am force-stop {args.package}")
        adb.start_app(args.package)
    time.sleep(2.0)

    print(f"[crawler] crawling (max depth={args.max_depth}, max screens={args.max_screens}, "
          f"max taps={args.max_actions}) - this will tap through the app; watch the device")
    t0 = time.monotonic()
    result = crawl(adb, args.package, args.max_depth, args.max_screens, args.max_actions,
                   args.settle, denylist, args.include_toggles)
    elapsed = time.monotonic() - t0

    out_path = Path(args.out) if args.out else ROOT / "recordings" / "ui_inventory" / f"{args.package}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    s = result["stats"]
    print(f"[crawler] done in {elapsed:.1f}s: {s.get('screens', 0)} screens, {len(result['edges'])} edges, "
          f"{s.get('taps', 0)} taps, skipped: {s.get('skipped_denied', 0)} denied / "
          f"{s.get('skipped_toggle', 0)} toggles / {s.get('skipped_left_scope', 0)} left-scope")
    print(f"[crawler] wrote {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
