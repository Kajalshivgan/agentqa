"""Reproduce a manual session: Home -> app grid (system bar) -> Settings -> Display, then stop the
recorder with a console break (like pressing Ctrl+Break / Ctrl+C) and check it saved.

  python tools/repro_app_grid.py
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.simulate_user import tap, tap_text  # noqa: E402
from engine import ADBClient  # noqa: E402

NAME = "repro_app_grid"
adb = ADBClient()
adb.keyevent("KEYCODE_HOME")
time.sleep(2)

log = open(ROOT / "recordings" / f"{NAME}.console.txt", "w", encoding="utf-8")
env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
rec = subprocess.Popen([sys.executable, "-u", "-m", "agents.recorder_agent", "record", "--name", NAME,
                        "--generator", "rules"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env,
                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
console = ROOT / "recordings" / f"{NAME}.console.txt"
for _ in range(60):
    if "ready" in console.read_text(encoding="utf-8").lower():
        break
    time.sleep(0.5)

steps = [("app grid button (bottom system bar)", lambda: tap(525, 744)),
         ("Settings icon in grid", lambda: tap_text("Settings")),
         ("Display", lambda: tap_text("Display"))]
for label, action in steps:
    print("[user]", label)
    action()
    time.sleep(6)

rec.send_signal(signal.CTRL_BREAK_EVENT)
rec.wait(timeout=60)
log.close()
print(console.read_text(encoding="utf-8"))
data = json.loads((ROOT / "recordings" / f"{NAME}.json").read_text(encoding="utf-8"))
for a in data["actions"]:
    print(a["action"], a.get("locator_type"), a.get("locator_value"), a.get("element"), a.get("warning", ""))
