from types import SimpleNamespace

import pytest

from agents.recorder.generator import MODEL, generate_with_llm

VALID = """*** Settings ***
Documentation     Opens display settings.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Open Display Settings
    Restart App    com.android.car.settings
    Tap On Text    Display
    Assert Visible    Brightness level
"""
INVALID = VALID.replace("Tap On Text", "Click Element")
ACTIONS = [{"action": "tap", "locator_type": "text", "locator_value": "Display"}]


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        # Snapshot the list: the generator appends to it after this call returns.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        text = self.replies.pop(0)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def test_valid_first_answer_is_returned_with_fences_stripped():
    client = FakeClient(["```robot\n" + VALID + "```"])
    assert generate_with_llm(ACTIONS, "display", "com.android.car.settings", client=client) == VALID
    call = client.calls[0]
    assert call["model"] == MODEL == "claude-sonnet-5"
    assert "Start package: com.android.car.settings" in call["messages"][0]["content"]


def test_invalid_answer_gets_one_append_only_correction_round():
    client = FakeClient([INVALID, VALID])
    assert generate_with_llm(ACTIONS, "display", client=client) == VALID
    first, second = client.calls
    assert second["messages"][:1] == first["messages"]  # earlier turns untouched
    assert second["messages"][1] == {"role": "assistant", "content": INVALID}
    assert "unknown keyword 'Click Element'" in second["messages"][2]["content"]


def test_gives_up_after_max_attempts():
    with pytest.raises(ValueError, match="failed validation"):
        generate_with_llm(ACTIONS, "display", client=FakeClient([INVALID, INVALID]))
