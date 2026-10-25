"""Recorder Agent CLI.

  python -m agents.recorder_agent record   --name display_settings [--device emulator-5554]
  python -m agents.recorder_agent generate --recording recordings/display_settings.json
  python -m agents.recorder_agent repair   --recording recordings/display_settings.json
  python -m agents.recorder_agent inspect  [--device emulator-5554]   # print the current UI tree
  python -m agents.recorder_agent capture-template --name apps_grid_icon --text Applications
  python -m agents.recorder_agent capture-template --name play_button --bounds 100,200,164,264

`record` captures until Ctrl+C, saves recordings/<name>.json, then writes tests/robot/<name>.robot.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.adb_client import ADBClient, ADBError  # noqa: E402
from engine.ui_tree import UITree  # noqa: E402
from engine.image_match import find_template  # noqa: E402
from agents.recorder.generator import (generate_rule_based, generate_with_llm,  # noqa: E402
                                       validate_robot)
from agents.recorder.ollama_client import DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL  # noqa: E402
from agents.recorder.ollama_client import OllamaClient, OllamaConnectionError  # noqa: E402
from agents.recorder.repair import needs_repair, repair_recording  # noqa: E402
from agents.recorder.session import RecorderSession  # noqa: E402

RECORDINGS = ROOT / "recordings"
ROBOT_DIR = ROOT / "tests" / "robot"
TEMPLATES_DIR = ROOT / "templates"


def cmd_record(args) -> int:
    adb = ADBClient(args.device)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    RECORDINGS.mkdir(exist_ok=True)
    path = RECORDINGS / f"{args.name}.json"

    def save(session: RecorderSession) -> dict:
        actions = session.finalize()
        start_package = next((a["package"] for a in actions if a.get("package")), None)
        recording = {"name": args.name, "device": adb.serial, "screen": list(adb.screen_size()),
                     "recorded_at": started, "start_package": start_package, "actions": actions}
        path.write_text(json.dumps(recording, indent=2, ensure_ascii=False), encoding="utf-8")
        (RECORDINGS / f"{args.name}.events.txt").write_text("\n".join(session.raw_events), encoding="utf-8")
        return recording

    # Autosave after every step, so a crash or a closed terminal never loses the recording.
    session = RecorderSession(adb, on_action=save)
    try:
        session.run(duration=args.duration)
    finally:
        recording = save(session)
        if session.error:
            (RECORDINGS / f"{args.name}.error.log").write_text(session.error, encoding="utf-8")
            print(f"[recorder] stopped by an error (details in recordings/{args.name}.error.log); "
                  f"steps so far were saved")
    if not recording["actions"]:
        print("[recorder] no actions captured")
        return 1
    print(f"[recorder] {len(recording['actions'])} actions saved to {path.relative_to(ROOT)}")
    if not args.no_repair and not session.error and any(needs_repair(a) for a in recording["actions"]):
        _repair_and_save(recording, adb, path)
    return _generate(recording, args.generator, args.backend)


def _repair_and_save(recording: dict, adb: ADBClient, path: Path, everything: bool = False) -> None:
    try:
        repair_recording(recording, adb, everything=everything)
    except KeyboardInterrupt:
        print("[repair] interrupted - keeping the steps repaired so far")
    except Exception as e:  # repair is an improvement pass: never lose the recording over it
        print(f"[repair] failed ({e}); keeping the recording as captured")
    path.write_text(json.dumps(recording, indent=2, ensure_ascii=False), encoding="utf-8")


def _load(recording_path: str) -> dict:
    # utf-8-sig: tolerate a BOM, which Windows editors / PowerShell 5 often add.
    return json.loads(Path(recording_path).read_text(encoding="utf-8-sig"))


def cmd_generate(args) -> int:
    return _generate(_load(args.recording), args.generator, args.backend)


def cmd_repair(args) -> int:
    recording = _load(args.recording)
    if not args.all and not any(needs_repair(a) for a in recording["actions"]):
        print("[repair] nothing to repair: every step already has a text/id locator")
    else:
        _repair_and_save(recording, ADBClient(args.device), Path(args.recording), everything=args.all)
    return _generate(recording, args.generator, args.backend)


def _generate(recording: dict, generator: str, backend: str = "cloud") -> int:
    name, actions = recording["name"], recording["actions"]
    start_package = recording.get("start_package")
    ROBOT_DIR.mkdir(parents=True, exist_ok=True)

    if generator in ("rules", "both"):
        _write(ROBOT_DIR / f"{name}.rules.robot" if generator == "both" else ROBOT_DIR / f"{name}.robot",
               generate_rule_based(actions, name, start_package))

    if generator in ("llm", "both"):
        t0 = time.monotonic()
        if backend == "local":
            model_name = os.environ.get("OLLAMA_MODEL", OLLAMA_DEFAULT_MODEL)
            client = OllamaClient(model=model_name, host=os.environ.get("OLLAMA_HOST"))
            try:
                suite = generate_with_llm(actions, name, start_package, client=client)
            except OllamaConnectionError as e:
                print(f"[generator] {e}")
                return 1
            except ValueError as e:  # output failed validation twice
                print(f"[generator] {e}")
                return 1
            print(f"[generator] {model_name} (local) generated the suite in "
                  f"{time.monotonic() - t0:.1f}s")
        else:
            if not _has_credentials():
                print("[generator] ANTHROPIC_API_KEY not set - skipping LLM generation "
                      "(use --generator rules or --backend local)")
                return 0 if generator == "both" else 1
            import anthropic

            try:
                suite = generate_with_llm(actions, name, start_package)
            except anthropic.APIStatusError as e:
                message = e.body.get("error", {}).get("message") if isinstance(e.body, dict) else str(e)
                print(f"[generator] Claude API error ({e.status_code}): {message}")
                if "workspace" in str(message):
                    print("[generator] fix: use an API key created inside a workspace, or add "
                          "ANTHROPIC_WORKSPACE_ID=<workspace id> to .env")
                return 1
            except anthropic.APIConnectionError as e:
                print(f"[generator] could not reach the Claude API: {e}")
                return 1
            except ValueError as e:  # output failed validation twice
                print(f"[generator] {e}")
                return 1
            print(f"[generator] Claude generated the suite in {time.monotonic() - t0:.1f}s")
        _write(ROBOT_DIR / f"{name}.robot", suite)
    return 0


def _write(path: Path, suite: str) -> None:
    errors = validate_robot(suite)
    path.write_text(suite, encoding="utf-8")
    status = "valid" if not errors else "INVALID: " + "; ".join(errors)
    print(f"[generator] wrote {path.relative_to(ROOT)} ({status})")


def _has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def cmd_inspect(args) -> int:
    adb = ADBClient(args.device)
    tree = UITree(adb.dump_ui())
    for e in tree.elements:
        label = e.text or e.content_desc
        if not (label or e.resource_id or e.clickable):
            continue
        print(f"{'  ' * min(e.depth, 12)}{e.class_name.split('.')[-1]} "
              f"text={label!r} id={e.resource_id!r} clickable={e.clickable} bounds={e.bounds}")
    return 0


def cmd_capture_template(args) -> int:
    """Crop a reference image for `Assert Image Visible` / `Tap On Image` from the live screen.

    Locate the source region either by an existing locator (--text/--desc/--id, same matching
    MobileUILibrary uses) or by raw --bounds - the fallback for an element with no accessible
    label at all, which is the actual reason to reach for image verification in the first place.
    """
    adb = ADBClient(args.device)
    given = [n for n, v in (("--text", args.text), ("--desc", args.desc), ("--id", args.id),
                            ("--bounds", args.bounds)) if v]
    if len(given) != 1:
        print(f"[template] pass exactly one of --text/--desc/--id/--bounds, got: {given or 'none'}")
        return 2

    if args.bounds:
        try:
            x1, y1, x2, y2 = (int(v.strip()) for v in args.bounds.split(","))
        except ValueError:
            print(f"[template] --bounds must be 'left,top,right,bottom', got: {args.bounds!r}")
            return 2
    else:
        tree = UITree(adb.dump_ui())
        # --text matches text OR content-desc, same as the Tap On Text keyword; --desc/--id are exact.
        if args.text:
            el = tree.find(args.index, text=args.text) or tree.find(args.index, content_desc=args.text)
            description = f"text/desc='{args.text}'"
        elif args.desc:
            el = tree.find(args.index, content_desc=args.desc)
            description = f"desc='{args.desc}'"
        else:
            el = tree.find(args.index, resource_id=args.id)
            description = f"id='{args.id}'"
        if el is None:
            print(f"[template] no element matched {description} (#{args.index}) on the current screen")
            return 1
        x1, y1, x2, y2 = el.bounds
        print(f"[template] matched {el.class_name.split('.')[-1]} at {el.bounds}")

    pad = args.padding
    x1, y1, x2, y2 = x1 - pad, y1 - pad, x2 + pad, y2 + pad
    if x2 <= x1 or y2 <= y1:
        print(f"[template] empty region after padding: ({x1},{y1})-({x2},{y2})")
        return 2

    from PIL import Image
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    dest = TEMPLATES_DIR / f"{args.name}.png"
    with tempfile.TemporaryDirectory() as tmp:
        screenshot_path = adb.screenshot(os.path.join(tmp, "screen.png"))
        screen = Image.open(screenshot_path)
        sw, sh = screen.size
        crop_box = (max(x1, 0), max(y1, 0), min(x2, sw), min(y2, sh))
        if crop_box != (x1, y1, x2, y2):
            print(f"[template] clipped region {(x1, y1, x2, y2)} to the screen: {crop_box}")
        screen.crop(crop_box).save(dest)
        print(f"[template] saved {dest.relative_to(ROOT)} ({crop_box[2] - crop_box[0]}x{crop_box[3] - crop_box[1]})")
        # Self-check against the same screenshot: find_template rejects a template too flat to
        # match reliably (see MIN_TEMPLATE_STDDEV) before it ever gets used against real screens.
        try:
            match = find_template(screenshot_path, dest, threshold=0.0)
        except ValueError as e:
            print(f"[template] warning: {e}")
            print(f"[template] the file was saved, but is unlikely to work well as-is")
            return 1
    print(f"[template] self-match score: {match.score:.2f} (should read ~1.0 against this exact screenshot)")
    print(f"[template] use it as: Assert Image Visible    {args.name}    (or Tap On Image)")
    return 0


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines); variables already set in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def restore_console_input() -> None:
    """Windows: re-enable Ctrl+C handling in this console.

    If an earlier `adb shell -t` was killed while it had the console in raw mode, the console
    stays raw and Ctrl+C arrives as a plain character instead of stopping the program.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # ENABLE_PROCESSED_INPUT | ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT
            kernel32.SetConsoleMode(handle, mode.value | 0x0001 | 0x0002 | 0x0004)
    except Exception:
        pass


def main(argv=None) -> int:
    restore_console_input()
    load_dotenv()
    # Never crash on printing a UI label the console code page can't encode (e.g. "…").
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="AgentQA Recorder Agent (Approach A: getevent capture)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="record a live session and generate a .robot test")
    r.add_argument("--name", required=True, help="test/recording name, e.g. display_settings")
    r.add_argument("--device", help="adb serial (default: the only connected device)")
    r.add_argument("--duration", type=float, help="stop automatically after N seconds")
    r.add_argument("--generator", choices=["llm", "rules", "both"], default="both")
    r.add_argument("--backend", choices=["cloud", "local"], default="cloud",
                   help="cloud=Claude API (needs ANTHROPIC_API_KEY); "
                        f"local=Ollama (needs `ollama serve`, default model {OLLAMA_DEFAULT_MODEL})")
    r.add_argument("--no-repair", action="store_true",
                   help="skip the automatic replay that replaces coordinate steps with text/id locators")
    r.set_defaults(func=cmd_record)

    rp = sub.add_parser("repair", help="replay a recording to replace coordinate steps with text/id "
                                       "locators, then regenerate the test")
    rp.add_argument("--recording", required=True)
    rp.add_argument("--device")
    rp.add_argument("--all", action="store_true",
                    help="re-resolve every step with the current rules (upgrade an older recording)")
    rp.add_argument("--generator", choices=["llm", "rules", "both"], default="both")
    rp.add_argument("--backend", choices=["cloud", "local"], default="cloud")
    rp.set_defaults(func=cmd_repair)

    g = sub.add_parser("generate", help="(re)generate a .robot test from a saved recording")
    g.add_argument("--recording", required=True)
    g.add_argument("--generator", choices=["llm", "rules", "both"], default="both")
    g.add_argument("--backend", choices=["cloud", "local"], default="cloud",
                   help="cloud=Claude API (needs ANTHROPIC_API_KEY); "
                        f"local=Ollama (needs `ollama serve`, default model {OLLAMA_DEFAULT_MODEL})")
    g.set_defaults(func=cmd_generate)

    i = sub.add_parser("inspect", help="print the current UI hierarchy")
    i.add_argument("--device")
    i.set_defaults(func=cmd_inspect)

    ct = sub.add_parser("capture-template", help="crop a reference PNG for Assert Image Visible / "
                                                 "Tap On Image from the live screen")
    ct.add_argument("--name", required=True, help="saved as templates/<name>.png")
    ct.add_argument("--device")
    ct.add_argument("--text", help="locate by text/content-desc, same as Tap On Text")
    ct.add_argument("--desc", help="locate by content-desc only")
    ct.add_argument("--id", help="locate by resource-id (full or short form)")
    ct.add_argument("--index", type=int, default=1, help="n-th match when the locator isn't unique")
    ct.add_argument("--bounds", help="left,top,right,bottom in screen pixels - use this when the "
                                     "element has no text/id/desc at all (the actual use case for "
                                     "image verification); get coordinates from `inspect`")
    ct.add_argument("--padding", type=int, default=0, help="pixels to grow the crop on each side")
    ct.set_defaults(func=cmd_capture_template)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ADBError as e:  # device problems are user-facing, not bugs: no traceback
        print(f"[agentqa] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
