"""Approach A recorder: raw getevent capture correlated with pre-touch UI snapshots.

Key idea: a UI dump taken *after* a tap shows the next screen, so it can't identify what was
tapped. A background thread keeps re-dumping the UI while the user is idle, and a snapshot
is only kept if no finger touched the screen while it was being captured. When a gesture
arrives, it is resolved against the newest such snapshot, i.e. the screen the user saw.
"""

from __future__ import annotations

import queue
import re
import signal
import threading
import time
import traceback
from dataclasses import dataclass

from engine.adb_client import ADBClient, ADBError
from engine.ui_tree import UITree
from agents.recorder.gestures import KEYS, Gesture, GestureTracker, parse_line
from agents.recorder.resolve import resolve_slider, resolve_swipe, resolve_tap, swipe_direction


@dataclass
class Snapshot:
    tree: UITree
    started: float  # host monotonic time the dump began
    touch_epoch: int  # number of touch-downs seen before the dump began
    ime_frame: tuple[int, int, int, int] | None  # on-screen keyboard bounds, if shown


class RecorderSession:
    def __init__(self, adb: ADBClient, idle_pause: float = 0.4, log=print, on_action=None):
        self.adb = adb
        self.idle_pause = idle_pause
        self.log = log
        self.on_action = on_action  # called with the session after each new step (autosave)
        self.error: str | None = None  # traceback if the capture loop died
        self.width, self.height = adb.screen_size()
        self.touch_devices = adb.touch_devices()
        if not self.touch_devices:
            raise ADBError("No multitouch input device found (getevent -pl shows no ABS_MT_POSITION_X).")
        try:
            self.system_bars = adb.system_bars()
        except ADBError:
            self.system_bars = {}

        self.actions: list[dict] = []
        self.raw_events: list[str] = []  # verbatim getevent lines, saved for debugging
        self._events: queue.Queue = queue.Queue()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._snapshot: Snapshot | None = None
        self._touch_epoch = 0
        self._finger_down = False
        self._last_gesture_end = 0.0
        self._pending_field: dict | None = None  # focused EditText being typed into
        self._first_typing: float | None = None  # keystrokes since the last gesture
        self._last_typing: float | None = None
        self._last_action_end = 0.0
        self._proc = None

    # ---- coordinates -----------------------------------------------------

    def scale(self, device: str, x: int, y: int) -> tuple[int, int]:
        d = self.touch_devices.get(device)
        if d is None:
            return x, y
        return (round(x * (self.width - 1) / max(d.max_x, 1)),
                round(y * (self.height - 1) / max(d.max_y, 1)))

    # ---- threads ---------------------------------------------------------

    def _read_events(self) -> None:
        # -tt forces a pty so getevent's stdout is line-buffered and events arrive immediately.
        self._proc = self.adb.popen("shell", "-tt", "getevent", "-lt")
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            if line.strip():
                self.raw_events.append(line.rstrip())
                self._events.put((time.monotonic(), line))
        self._events.put((time.monotonic(), None))  # stream ended: device gone or getevent killed

    def _snapshot_loop(self) -> None:
        while not self._stop.is_set():
            if self._finger_down or time.monotonic() - self._last_gesture_end < self.idle_pause:
                time.sleep(0.05)
                continue
            epoch, started = self._touch_epoch, time.monotonic()
            try:
                tree = UITree(self.adb.dump_ui(retries=1))
                ime = self._ime_frame()
            except Exception:
                time.sleep(0.2)
                continue
            # Discard the dump if a finger touched down while it was being captured.
            if epoch == self._touch_epoch and not self._finger_down:
                with self._lock:
                    first = self._snapshot is None
                    # First fresh capture since the last step: tell the user it's safe to go on.
                    announce = (not first and self.actions
                                and (self._snapshot is None or self._snapshot.started < self._last_action_end)
                                and started >= self._last_action_end)
                    self._snapshot = Snapshot(tree, started, epoch, ime)
                    try:
                        self._update_pending_field(tree, time.monotonic())
                    except Exception as e:  # never let text capture kill UI snapshots
                        self.log(f"[recorder] text capture error: {e}")
                if first:
                    self.log("[recorder] ================================================================")
                    self.log("[recorder]  READY - recording started. How to record:")
                    self.log("[recorder]   1. Do ONE step on the device (tap / swipe / type).")
                    self.log("[recorder]   2. Wait for '>> READY - do the next step' before the next one")
                    self.log("[recorder]      (the recorder needs ~2-3 s to capture each new screen).")
                    self.log("[recorder]   3. Press Ctrl+C when you are done - the test is then generated.")
                    self.log("[recorder] ================================================================")
                    self.log("[recorder] >> READY - do the first step")
                elif announce:
                    self.log("[recorder] >> READY - do the next step")

    def _ime_frame(self) -> tuple[int, int, int, int] | None:
        # input_method's mIsInputViewShown stays true after the keyboard hides, so ask the
        # window manager instead. The IME window often spans the whole screen; only its
        # touchable region is the keyboard.
        win = self.adb.shell("dumpsys window InputMethod | grep -E 'isVisible=|touchable region'")
        if "isVisible=true" not in win:
            return None
        m = re.search(r"SkRegion\(\((\d+),(\d+),(\d+),(\d+)\)", win)
        return tuple(int(v) for v in m.groups()) if m else None

    # ---- main loop -------------------------------------------------------

    def run(self, duration: float | None = None) -> list[dict]:
        tracker = GestureTracker(self.scale, set(self.touch_devices))
        threading.Thread(target=self._read_events, daemon=True).start()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()
        self.log(f"[recorder] screen {self.width}x{self.height}, touch devices: "
                 f"{', '.join(sorted(self.touch_devices))}; waiting for first UI snapshot...")
        deadline = time.monotonic() + duration if duration else None
        if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl+Break stops the recording too
            signal.signal(signal.SIGBREAK, lambda *_: self._stop.set())
        try:
            while not self._stop.is_set() and not (deadline and time.monotonic() > deadline):
                try:
                    received, line = self._events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    if not self._stop.is_set():
                        self.log("[recorder] touch event stream ended (emulator closed or device disconnected?) "
                                 "- stopping and saving")
                    break
                ev = parse_line(line)
                if ev is None:
                    continue
                if (ev.type == "EV_KEY" and ev.value == "DOWN" and ev.device not in self.touch_devices
                        and ev.code not in KEYS):
                    self._note_typing()  # hardware keyboard typing
                if ev.device in self.touch_devices and (
                        (ev.code == "BTN_TOUCH" and ev.value == "DOWN") or
                        (ev.code == "ABS_MT_TRACKING_ID" and ev.value != "ffffffff")):
                    if not self._finger_down:
                        self._finger_down = True
                        self._touch_epoch += 1
                for gesture in tracker.feed(ev):
                    self._finger_down = False
                    self._last_gesture_end = time.monotonic()
                    self._handle(gesture)
        except KeyboardInterrupt:
            pass
        except Exception:
            self.error = traceback.format_exc()
            self.log("[recorder] ERROR - capture stopped:\n" + self.error)
        finally:
            self.stop()
            # A second Ctrl+C must not interrupt saving what was captured.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        return self.actions

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        with self._lock:
            self._pending_field = None

    # ---- gesture -> action ----------------------------------------------

    def _note_typing(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._last_typing = now
            if self._first_typing is None:
                self._first_typing = now

    def _handle(self, g: Gesture) -> None:
        with self._lock:
            snap = self._snapshot
            # The snapshot is "fresh" if it began after the previous gesture finished.
            fresh = snap is not None and snap.started >= self._last_action_end

        if g.kind == "unknown":
            self.log("[recorder] (!) a touch had no position (same spot as a tap made before recording "
                     "started) and was skipped - if it mattered, tap elsewhere and repeat it")
            return
        if g.kind != "key":
            x, y = self.scale(g.device, *g.start)
            if snap and snap.ime_frame and _inside(snap.ime_frame, x, y):
                self._note_typing()  # on-screen keyboard: text is captured from the field instead
                self.log(f"[recorder] (keyboard tap at {x},{y} - captured as text, not as a step)")
                return

        # A new gesture ends any text entry in progress; flag it if keystrokes came after the
        # last snapshot (the recorded text may be missing its final characters).
        with self._lock:
            f = self._pending_field
            if f and f["action"] and self._last_typing and snap and self._last_typing > snap.started:
                f["action"]["warning"] = "text may be incomplete: typed after the last UI snapshot"
            self._pending_field = None
            self._first_typing = self._last_typing = None

        if g.kind == "key":
            self._append({"action": "press_key", "key": g.key}, snap, fresh)
            return

        if g.kind == "swipe" and snap and snap.tree.slider_at(x, y):
            # Dragging a slider's thumb: what matters is the value where the finger let go.
            x2, _ = self.scale(g.device, *g.end)
            action = {"action": "tap", "x": x2, "y": y, "from": [x, y]}
            action.update(resolve_slider(snap.tree, x2, y) or resolve_tap(snap.tree, x, y))
            self._append(action, snap, fresh)
            return

        if g.kind == "swipe":
            x2, y2 = self.scale(g.device, *g.end)
            action = {"action": "swipe", "from": [x, y], "to": [x2, y2], "duration_ms": max(g.duration_ms, 100)}
            action.update(resolve_swipe(snap.tree, [x, y], [x2, y2]) if snap else
                          {"direction": swipe_direction([x, y], [x2, y2])})
            self._append(action, snap, fresh)
            return

        action = {"action": "long_press" if g.kind == "long_press" else "tap", "x": x, "y": y}
        if g.kind == "long_press":
            action["duration_ms"] = g.duration_ms
        bar = next((name for name, frame in self.system_bars.items() if _inside(frame, x, y)), None)
        if bar is not None and not (snap and snap.tree.has_windows):
            # Device without `dump --windows`: system bars are invisible to the dump, and the app
            # UI behind them would give a wrong locator, so keep the raw coordinates.
            action.update(locator_type="point", locator_value=f"{x},{y}", system_bar=bar)
        elif snap is None:
            action.update(locator_type="point", locator_value=f"{x},{y}")
        else:
            action.update(resolve_tap(snap.tree, x, y))
        self._append(action, snap, fresh)

    def _append(self, action: dict, snap: Snapshot | None, fresh: bool) -> None:
        if snap is not None:
            action["screen_before"] = snap.tree.visible_texts()[:25]
            action["package"] = _main_package(snap.tree)
        if not fresh:
            action["warning"] = "stale_snapshot: acted before the UI was re-captured; verify this step"
        action["_ended"] = time.monotonic()
        with self._lock:
            self.actions.append(action)
            self._last_action_end = action["_ended"]
        if action["action"] == "set_slider":
            desc = f"{action.get('locator_value')} -> {action['percent']}%"
        elif action["action"] == "swipe":
            desc = f"{action.get('direction', '')} in {action.get('container') or 'screen'}"
        else:
            desc = action.get("locator_value") or action.get("key") or ""
        self.log(f"[recorder] recorded: {action['action']:<10} {action.get('locator_type', ''):<5} {desc}")
        if not fresh:
            self.log("[recorder] (!) that was too fast - the screen wasn't captured yet, so this step will "
                     "replay by screen coordinates. Wait for '>> READY' next time.")
        self.log("[recorder] .. WAIT - processing, capturing the new screen ...")
        self._autosave()

    def _autosave(self) -> None:
        if self.on_action is None:
            return
        try:
            with self._lock:  # both threads autosave; never write the files concurrently
                self.on_action(self)
        except Exception as e:  # saving must never kill the capture loop
            self.log(f"[recorder] autosave failed: {e}")

    def _update_pending_field(self, tree: UITree, finished: float) -> None:
        """Called with the lock held after each accepted snapshot: capture typed text.

        Tracks the *focused* EditText, since a field often appears only after the tap that
        opens it (e.g. a search bar). Its text in the first snapshot completed before any typing
        is the baseline; if typing started before that snapshot finished, the baseline is empty.
        (uiautomator captures near the end of its ~2 s run, so the finish time is what counts.)
        """
        el = next((e for e in tree.elements if e.is_edit_text and e.focused), None)
        if el is None:
            return
        el.text = el.text.rstrip("\r\n")  # Enter pressed in the field; the text is what matters
        f = self._pending_field
        if f is None or (el.resource_id, el.bounds) != (f["resource_id"], f["bounds"]):
            typed_before = self._first_typing is not None and self._first_typing <= finished
            f = self._pending_field = {"resource_id": el.resource_id, "bounds": el.bounds,
                                       "initial": "" if typed_before else el.text, "action": None}
        if el.text == f["initial"]:
            return
        if f["action"] is None:
            f["action"] = {"action": "input_text", "text": el.text, "resource_id": el.resource_id,
                           "_ended": time.monotonic()}
            self.actions.append(f["action"])
            self.log(f"[recorder] input_text       {el.text!r}")
            self._autosave()
        elif f["action"]["text"] != el.text:
            f["action"]["text"] = el.text
            self.log(f"[recorder] input_text       {el.text!r} (updated)")
            self._autosave()

    # ---- post-processing -------------------------------------------------

    def finalize(self) -> list[dict]:
        """Attach the screen each action led to (the next action's screen_before), drop internals."""
        with self._lock:
            actions = [dict(a) for a in self.actions]
            snap = self._snapshot
        out = []
        for i, a in enumerate(actions):
            clean = {k: v for k, v in a.items() if not k.startswith("_")}
            nxt = next((b for b in actions[i + 1:] if "screen_before" in b), None)
            if nxt is not None:
                clean["screen_after"] = nxt["screen_before"]
            elif snap is not None and snap.started >= a["_ended"]:
                clean["screen_after"] = snap.tree.visible_texts()[:25]
            out.append(clean)
        return out


def _inside(frame, x, y) -> bool:
    x1, y1, x2, y2 = frame
    return x1 <= x < x2 and y1 <= y < y2


def _main_package(tree: UITree) -> str:
    counts: dict[str, int] = {}
    for e in tree.elements:
        if e.package and "systemui" not in e.package:
            counts[e.package] = counts.get(e.package, 0) + 1
    return max(counts, key=counts.get) if counts else ""
