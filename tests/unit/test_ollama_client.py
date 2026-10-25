import json
import urllib.error

import pytest

from agents.recorder.generator import generate_with_llm
from agents.recorder.ollama_client import OllamaClient, OllamaConnectionError

ACTIONS = [{"action": "tap", "locator_type": "text", "locator_value": "Display"}]
VALID = """*** Settings ***
Documentation     Opens display settings.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Open Display Settings
    Tap On Text    Display
"""


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_create_posts_system_and_messages_and_parses_reply(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"message": {"role": "assistant", "content": VALID}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OllamaClient(model="qwen2.5-coder:7b", host="http://localhost:11434")
    response = client.messages.create(model="ignored", max_tokens=16000, system="SYS",
                                      messages=[{"role": "user", "content": "hi"}])

    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["body"]["model"] == "qwen2.5-coder:7b"
    assert captured["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert captured["body"]["messages"][1] == {"role": "user", "content": "hi"}
    assert response.stop_reason == "end_turn"
    assert response.content[0].text == VALID


def test_connection_failure_raises_with_a_helpful_message(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OllamaClient()
    with pytest.raises(OllamaConnectionError, match="ollama serve"):
        client.messages.create(system="SYS", messages=[])


def test_generate_with_llm_works_unchanged_against_the_ollama_adapter(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return FakeResponse(json.dumps({"message": {"role": "assistant", "content": VALID}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = OllamaClient()
    assert generate_with_llm(ACTIONS, "display", client=client) == VALID
