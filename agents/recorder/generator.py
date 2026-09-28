"""Convert a recorded action log into a Robot Framework suite.

Two generators share one output contract:
  * rule_based - deterministic, offline, used as the baseline and fallback
  * llm        - Claude (claude-sonnet-5) cleans up the flow: names, merged steps, assertions
Both results pass through `validate_robot` before being written.
"""

from __future__ import annotations

import json
import re

from robot.api import get_model
from robot.api.parsing import KeywordCall, ModelVisitor

MODEL = "claude-sonnet-5"

LIBRARY_KEYWORDS = {
    "Tap On Text", "Tap On Id", "Tap On Description", "Tap On Point", "Long Press On Text",
    "Wait Until Screen Is Stable", "Scroll To Text", "Scroll", "Set Slider",
    "Input Text", "Swipe", "Swipe Up", "Swipe Down", "Press Back", "Press Home", "Launch App", "Restart App",
    "Assert Visible", "Assert Not Visible", "Assert Id Visible", "Take Screenshot",
    "Assert Image Visible", "Tap On Image", "Assert Screenshot Matches",
}
BUILTIN_KEYWORDS = {"Sleep", "Log", "Set Test Variable", "Should Be Equal", "Run Keyword And Ignore Error"}

HEADER = """*** Settings ***
Documentation     {doc}
Library           engine.MobileUILibrary    device=${{DEVICE}}    timeout=10

*** Variables ***
${{DEVICE}}         ${{None}}

*** Test Cases ***
"""


# ---- shared helpers --------------------------------------------------------

def robot_escape(value: str) -> str:
    """Escape a literal so Robot doesn't treat it as a variable, separator or comment."""
    value = value.replace("\\", "\\\\")
    value = re.sub(r"([$@&%])\{", r"\\\1{", value)
    value = value.replace("#", "\\#")
    value = re.sub(r" {2,}", lambda m: " " + "\\ " * (len(m.group()) - 1), value)
    if value.startswith(" ") or value.endswith(" ") or value == "":
        value = "${EMPTY}" if value == "" else value.replace(" ", "${SPACE}")
    return value


def test_name_from(name: str) -> str:
    words = re.split(r"[_\-\s]+", name.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or "Recorded Flow"


# Toolbar/navigation chrome: present on many screens, so a poor signal that navigation worked.
CHROME_LABELS = {"back", "navigate up", "search", "scroll up", "scroll down", "more options", "close", "menu"}


def new_texts(action: dict) -> list[str]:
    before = set(action.get("screen_before") or [])
    return [t for t in action.get("screen_after") or []
            if t not in before and t.lower() not in CHROME_LABELS]


# ---- rule-based -----------------------------------------------------------

def is_stale(action: dict) -> bool:
    return "stale_snapshot" in action.get("warning", "")


def _index_arg(action: dict) -> str:
    idx = action.get("locator_index")
    return f"    index={idx}" if idx and idx > 1 else ""


def _container_args(swipe: dict) -> str:
    if is_stale(swipe) or not swipe.get("container"):
        return ""  # a stale swipe's container came from an outdated screen
    args = f"    container={swipe['container']}"
    if swipe.get("container_index", 1) > 1:
        args += f"    container_index={swipe['container_index']}"
    return args


def _step(action: dict) -> str | None:
    a = action["action"]
    if a in ("tap", "long_press"):
        t, v = action.get("locator_type"), action.get("locator_value", "")
        if is_stale(action) and "x" in action:
            # The locator came from an outdated screen and may name the wrong element; the raw
            # coordinates are what the user actually touched.
            t, v = "point", f"{action['x']},{action['y']}"
        if a == "long_press":
            if t == "text":
                return f"Long Press On Text    {robot_escape(v)}    {action.get('duration_ms', 1000)}"
            x, y = action["x"], action["y"]
            return f"Swipe    {x}    {y}    {x}    {y}    {action.get('duration_ms', 1000)}"
        if t == "text":
            return f"Tap On Text    {robot_escape(v)}{_index_arg(action)}"
        if t == "id":
            return f"Tap On Id    {robot_escape(v)}{_index_arg(action)}"
        if t == "desc":
            return f"Tap On Description    {robot_escape(v)}{_index_arg(action)}"
        x, y = v.split(",")
        return f"Tap On Point    {x}    {y}"
    if a == "swipe":
        if action.get("direction"):
            return f"Scroll    {action['direction']}{_container_args(action)}"
        (x1, y1), (x2, y2) = action["from"], action["to"]
        return f"Swipe    {x1}    {y1}    {x2}    {y2}    {action.get('duration_ms', 300)}"
    if a == "set_slider" and action.get("locator_type") != "point" and not is_stale(action):
        return f"Set Slider    {robot_escape(action['locator_value'])}    {action['percent']}{_index_arg(action)}"
    if a == "set_slider":  # no usable locator: touch the recorded point
        return f"Tap On Point    {action['x']}    {action['y']}"
    if a == "input_text":
        return f"Input Text    {robot_escape(action['text'])}"
    if a == "press_key":
        return {"back": "Press Back", "home": "Press Home"}.get(action["key"])
    return None


def _tap_target_text(action: dict) -> str | None:
    """The label a tap reliably targets (for Scroll To Text), if any."""
    if action.get("action") == "tap" and not is_stale(action) and action.get("locator_type") in ("text", "desc"):
        return action.get("locator_value")
    return None


def generate_rule_based(actions: list[dict], test_name: str, start_package: str | None = None) -> str:
    lines = [f"Restart App    {start_package}"] if start_package else []
    typed = {a["text"] for a in actions if a["action"] == "input_text"}
    i = 0
    while i < len(actions):
        action = actions[i]
        # Swipes that just scroll to the item tapped next -> one `Scroll To Text <item>`.
        if action["action"] == "swipe":
            j = i
            while j < len(actions) and actions[j]["action"] == "swipe":
                j += 1
            target = _tap_target_text(actions[j]) if j < len(actions) else None
            if target and all(a.get("direction") == action.get("direction") for a in actions[i:j]):
                first_known = next((a for a in actions[i:j] if not is_stale(a) and a.get("container")), None)
                lines.append(f"Scroll To Text    {robot_escape(target)}    direction={action.get('direction', 'up')}"
                             + (_container_args(first_known) if first_known else ""))
                i = j
                continue

        if is_stale(action) and action["action"] != "swipe":
            guess = action.get("locator_value") if action.get("locator_type") != "point" else None
            lines.append("# NOTE: tapped before the screen was captured - replaying raw coordinates"
                         + (f" (possibly '{guess}', unverified)" if guess else ""))
        elif action.get("warning") and not is_stale(action):
            lines.append(f"# NOTE: {action['warning']}")
        if action.get("system_bar"):
            lines.append(f"# system bar ({action['system_bar']}): outside the app UI dump, raw coordinates")
        elif action.get("locator_type") == "point":
            lines.append("# NOTE: no stable locator under this tap; using raw coordinates")
        step = _step(action)
        i += 1
        if step is None:
            continue
        if step.startswith(("Tap On Point", "Swipe    ")):
            # Coordinate steps can't wait for their target; let the previous screen finish loading.
            lines.append("Wait Until Screen Is Stable")
        lines.append(step)
        # Assert the first text that the action made appear, unless the next step targets it anyway.
        nxt = actions[i] if i < len(actions) else {}
        target = nxt.get("locator_value") if nxt.get("locator_type") == "text" else None
        appeared = [t for t in new_texts(action) if len(t) <= 60 and t not in typed]
        # A stale step's screens are outdated, and so is the "after" screen of the step before it.
        trustworthy = not is_stale(action) and not is_stale(nxt) and action["action"] != "swipe"
        if appeared and trustworthy and action["action"] != "input_text":
            pick = target if target in appeared else appeared[0]
            lines.append(f"Assert Visible    {robot_escape(pick)}")

    body = "\n".join(f"    {l}" for l in lines) or "    No Operation"
    doc = f"Recorded flow '{test_name}' (rule-based conversion)."
    return HEADER.format(doc=doc) + f"{test_name_from(test_name)}\n{body}\n"


# ---- LLM ------------------------------------------------------------------

SYSTEM_PROMPT = """You convert recorded Android UI interactions into a Robot Framework test suite.

The suite must use this exact Settings/Variables header (fill in Documentation):
*** Settings ***
Documentation     <one sentence describing what the flow verifies>
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

Available keywords (arguments separated by 4 spaces):
  Tap On Text    <text>    [index=<n>]   - tap element whose text or content-desc equals <text>
  Tap On Id    <resource-id>    [index=<n>]
  Tap On Description    <content-desc>    [index=<n>]
                                    - index=<n>: n-th match on screen; use the recorded
                                      locator_index when present (omit when 1)
  Scroll To Text    <text>    direction=<up|down>    [container=<id>]    [container_index=<n>]
                                    - scroll a list until <text> is visible
  Scroll    <up|down|left|right>    [container=<id>]    [container_index=<n>]  - one page
  Set Slider    <slider label or id>    <percent 0-100>    [index=<n>]
                                    - set a slider (volume, brightness...); use for set_slider steps
  Tap On Point    <x>    <y>        - only when the recording has no locator
  Wait Until Screen Is Stable       - waits until the UI stops changing; put it before every
                                      Tap On Point and coordinate Swipe (they can't wait themselves)
  Long Press On Text    <text>    <duration_ms>
  Input Text    <text>              - types into the focused field
  Swipe    <x1>    <y1>    <x2>    <y2>    <duration_ms>
  Swipe Up / Swipe Down             - generic scroll in the middle of the screen
  Press Back / Press Home
  Restart App    <package>          - force-stop and relaunch the app (clean starting state)
  Assert Visible    <text>          - waits until text is on screen
  Assert Not Visible    <text>
  Take Screenshot    <name>
  Assert Image Visible    <template>    [threshold=0.8]   - find a reference PNG anywhere on screen;
                                    use only for elements with no text/id at all (maps, video,
                                    custom-drawn icons) - never in place of a step with a real locator
  Tap On Image    <template>    [threshold=0.8]   - tap wherever that reference image is found
  Assert Screenshot Matches    <name>    [threshold=98]   - whole-screen visual regression; do not
                                    invent this step, only include it if the recording explicitly
                                    calls for a visual check

How to read the recording: each entry is one user action. `locator_type`/`locator_value` say
how the tapped element was identified. `screen_before` lists texts visible before the action
and `screen_after` the texts visible after it. A `warning` means the step may be unreliable.

Rules:
- Produce one test case whose name describes what the flow accomplishes (Title Case).
- If a start package is given, the first step is `Restart App    <start package>`.
- Keep every user action, in order, using the recorded locator; do not invent steps or text.
- Do not assert on generic toolbar chrome (Back, Search, Scroll up/down) or on text the user typed.
- After each action that changes the screen, add `Assert Visible` for a text that appears in
  screen_after but not screen_before (prefer a page title). Only use texts from the recording.
- Never use coordinates when the recording gives a locator: prefer text, then content-desc,
  then resource-id (add index=<n> when locator_index is given).
- Swipes that scroll a list to reach the item tapped next become ONE
  `Scroll To Text    <that item>    direction=<swipe direction>` (plus container/container_index
  from a non-stale swipe when present); other swipes become `Scroll    <direction>` with the
  recorded container. Use raw `Swipe` only for a swipe that has no direction.
- For a step with a warning, keep it and add a `# NOTE:` comment line above it.
- A tap whose warning starts with `stale_snapshot` was resolved against an outdated screen, so
  its locator may be wrong: emit `Tap On Point    <x>    <y>` from its x/y instead, and do not
  use its screen_before/screen_after texts for assertions around it.
- A step with `system_bar` is a tap on the OS system bar (e.g. app grid / home button): keep it
  as `Tap On Point` and add a comment naming the bar.
- Escape literal `#`, `${` and runs of spaces the Robot way.
- Output only the .robot file content. No markdown fences, no explanations."""


def generate_with_llm(actions: list[dict], test_name: str, start_package: str | None = None,
                      client=None, max_attempts: int = 2) -> str:
    if client is None:
        import os

        import anthropic
        # Keys that aren't scoped to a workspace need the workspace named explicitly.
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)
    messages = [{"role": "user", "content":
                 f"Suggested test name: {test_name_from(test_name)}\n"
                 f"Start package: {start_package or 'unknown (no Restart App step)'}\n\n"
                 f"Recorded actions (JSON):\n{json.dumps(actions, indent=1, ensure_ascii=False)}"}]
    errors: list[str] = []
    for _ in range(max_attempts):
        response = client.messages.create(model=MODEL, max_tokens=16000, system=SYSTEM_PROMPT,
                                          messages=messages)
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to generate the test.")
        text = "".join(b.text for b in response.content if b.type == "text")
        suite = strip_fences(text)
        errors = validate_robot(suite)
        if not errors:
            return suite
        # Append-only retry: show Claude its output and what failed validation.
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "That suite failed validation:\n- " + "\n- ".join(errors)
                      + "\nReturn the corrected full .robot file only."}]
    raise ValueError("LLM output failed validation: " + "; ".join(errors))


def strip_fences(text: str) -> str:
    m = re.search(r"```(?:robot(?:framework)?)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


# ---- validation -----------------------------------------------------------

class _KeywordCollector(ModelVisitor):
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def visit_KeywordCall(self, node: KeywordCall):
        self.calls.append((node.keyword, node.lineno))


def validate_robot(suite: str) -> list[str]:
    """Syntax errors plus any keyword the library/BuiltIn doesn't provide."""
    model = get_model(suite, data_only=False)
    errors = [f"line {e.lineno}: {e.error}" for e in _errors(model)]
    if "*** Test Cases ***" not in suite:
        errors.append("missing *** Test Cases *** section")
    if "engine.MobileUILibrary" not in suite:
        errors.append("missing `Library    engine.MobileUILibrary` import")
    collector = _KeywordCollector()
    collector.visit(model)
    known = {k.lower() for k in LIBRARY_KEYWORDS | BUILTIN_KEYWORDS} | {"no operation"}
    for name, line in collector.calls:
        if name and name.lower() not in known:
            errors.append(f"line {line}: unknown keyword '{name}'")
    return errors


def _errors(model):
    class V(ModelVisitor):
        def __init__(self):
            self.found = []

        def generic_visit(self, node):
            for err in getattr(node, "errors", ()) or ():
                self.found.append(type("E", (), {"lineno": getattr(node, "lineno", 0), "error": err}))
            super().generic_visit(node)

    v = V()
    v.visit(model)
    return v.found
