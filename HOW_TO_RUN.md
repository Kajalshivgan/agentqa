# How to Run — AgentQA

End-to-end steps to go from a cold machine to a passing (or failing) test result. See the main [README.md](README.md) for architecture/background — this file is just the runbook.

---

## 1. One-time setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Put your Claude API key in `.env` (git-ignored, loaded automatically) — only needed for LLM-based test generation:
```
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_WORKSPACE_ID=wrkspc_...   # only if the key isn't created inside a workspace
```

---

## 2. Start the emulator

```powershell
emulator -avd Automotive_1408p_landscape
```

Confirm ADB sees it — wait until the second column says `device`, not `offline`:
```powershell
adb devices
# emulator-5554   device
```

If it gets stuck `offline` / the window says "Not Responding":
```powershell
emulator -avd Automotive_1408p_landscape -gpu swiftshader_indirect -no-snapshot
```

---

## 3. Get a `.robot` test file

There are two ways to end up with a runnable `.robot` file. Pick one.

### Option A — You already have a `.robot` file
Skip to [Step 4](#4-run-the-test). Existing tests live in `tests/robot/` (e.g. `AAOS-101_display_brightness.robot`, `smoke_settings.robot`).

### Option B — Record a new flow from a test case
`testcases/*.json` (Jira-style test cases, e.g. `AAOS-101_display_brightness.json`) are **not** auto-converted yet — there's no Planner Agent. Use the JSON's `testSteps` as your script and record yourself performing it:

```powershell
python -m agents.recorder_agent record --name AAOS-101_display_brightness
```
- Wait for `[recorder] ready`
- Perform the flow from the test case's `testSteps` on the device/emulator, pausing ~3 s between actions (each step needs a fresh UI dump)
- Press `Ctrl+C` to stop

This produces:
| File | What it is |
|---|---|
| `recordings/AAOS-101_display_brightness.json` | structured action log |
| `recordings/AAOS-101_display_brightness.events.txt` | raw `getevent` stream |
| `tests/robot/AAOS-101_display_brightness.robot` | Claude-generated test |
| `tests/robot/AAOS-101_display_brightness.rules.robot` | rule-based baseline test |

**Useful `record` flags:**
| Flag | Meaning |
|---|---|
| `--name NAME` | required — recording/test name |
| `--device DEVICE` | adb serial (default: the only connected device) |
| `--duration N` | stop automatically after N seconds |
| `--generator {llm,rules,both}` | which generator(s) to run (default `both`) |
| `--backend {cloud,local}` | which LLM the `llm` generator talks to (default `cloud`) — see below |
| `--no-repair` | skip the automatic locator-repair replay |

**Cloud vs. local LLM backend (`--backend`):** both `record` and `generate` accept this flag.
- `cloud` (default) — Claude API, needs `ANTHROPIC_API_KEY` in `.env`. Highest quality; costs ~$0.01–0.02/test.
- `local` — a fully offline Ollama model, zero per-use cost. One-time setup:
  ```powershell
  # install Ollama (https://ollama.com), then:
  ollama pull qwen2.5-coder:7b   # or :3b / :14b - see README's System Requirements table
  ollama serve                   # leave running in its own terminal
  ```
  Then generate with:
  ```powershell
  python -m agents.recorder_agent generate --recording recordings/AAOS-101_display_brightness.json --generator llm --backend local
  ```
  Override the model/host with env vars if needed: `OLLAMA_MODEL` (default `qwen2.5-coder:7b`), `OLLAMA_HOST` (default `http://localhost:11434`). The same prompt, keyword whitelist, and `validate_robot()` retry loop run either way — only which model answers changes. Expect more validation retries and weaker assertion choices from a small local model than from Claude; see the README's Roadmap/notes on this tradeoff.

**Regenerate** a `.robot` file from an existing recording (e.g. after editing `generator.py`, or to try a different `--generator`) without re-recording:
```powershell
python -m agents.recorder_agent generate --recording recordings/AAOS-101_display_brightness.json --generator both
```

**Repair** a recording whose steps fell back to raw coordinates (replays it once on the device to swap `Tap On Point` for text/id locators):
```powershell
python -m agents.recorder_agent repair --recording recordings/AAOS-101_display_brightness.json
# --all forces re-resolution of every step, not just the flagged ones
```

**Inspect** the current screen's UI tree (handy for writing/checking locators by hand):
```powershell
python -m agents.recorder_agent inspect
```

**Capture a template image** (only needed if the test uses `Assert Image Visible` / `Tap On Image`, e.g. `AAOS-110_applications_grid.json`):
```powershell
# by locator
python -m agents.recorder_agent capture-template --name apps_grid_icon --text Applications

# by raw pixel bounds - for elements with no text/id/desc at all
python -m agents.recorder_agent capture-template --name play_button --bounds 100,200,164,264
```

---

## 4. Run the test

```powershell
robot --pythonpath . --outputdir results tests/robot/AAOS-101_display_brightness.robot
```
- `--pythonpath .` lets Robot Framework find `engine/MobileUILibrary.py`
- `--outputdir results` writes `results/log.html`, `results/report.html`, `results/output.xml`
- Add `--variable DEVICE:emulator-5554` if more than one device is attached

Open `results/report.html` to see pass/fail. On failure, `results/log.html` has an embedded screenshot at the failing step.

**Dry-run only** (syntax/keyword check, no device needed):
```powershell
robot --dryrun --pythonpath . tests/robot/AAOS-101_display_brightness.robot
```

**Run every suite** in `tests/robot/`:
```powershell
robot --pythonpath . --outputdir results tests/robot
```

---

## 5. Unit tests (no device/emulator needed)

```powershell
pytest
```
Runs against saved UI dumps, `getevent` traces, and synthetic images — no ADB connection required.

---

## 6. End-to-end recorder sanity check

Replays a scripted flow with real kernel-level touch events, records it, generates a test, and runs it — used to verify the whole pipeline after a change:
```powershell
powershell -ExecutionPolicy Bypass -File tools\verify_recorder.ps1 -Runs 2
```
Every run must pass.

---

## 7. Use the tests from an AI agent (MCP server)

```powershell
pip install -r requirements.txt          # includes the `mcp` package
python -m agents.robot_mcp               # stdio server; normally started by the MCP client, not by hand
```

- **VS Code Copilot agent mode:** open the project folder; `.vscode/mcp.json` registers the `agentqa-robot` server. Start it from the MCP view (or the "Start" link above the entry in `mcp.json`), then pick its tools in agent mode.
- **Claude Desktop / Claude Code:** see "MCP Server" in [README.md](README.md) for the config.

Runs started through the server land in `results/mcp/<run_id>/` (log.html, report.html, screenshots). Suites written by an agent land in `tests/robot/generated/` and are dry-run validated first.

Check it without a client: `pytest tests/unit/test_robot_mcp.py`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Recorder ignores `Ctrl+C` | Fixed in current version — update if you see this |
| `Claude API error (400) ... not scoped to a workspace` | Create the API key inside a workspace in the Claude Console, or add `ANTHROPIC_WORKSPACE_ID` to `.env` |
| Emulator stuck `offline` / "Not Responding" | `emulator -avd Automotive_1408p_landscape -gpu swiftshader_indirect -no-snapshot` |
| More than one `adb` installed, device stuck `offline` | Use the Android SDK's copy (`<sdk>/platform-tools`); the engine prefers it automatically |
| `robot` fails: `unknown encoding: utf-8:surrogateescape` | `Remove-Item Env:PYTHONIOENCODING` (or `unset PYTHONIOENCODING`), then rerun |
| MCP tool returns `ADBError: adb not found` / no devices | Start the emulator (step 2) and check `adb devices`; set `ANDROID_HOME` if adb isn't on PATH |
| `ModuleNotFoundError: No module named 'cv2'` | The venv isn't activated / `pip install -r requirements.txt` wasn't run in it — activate `.venv` first |
