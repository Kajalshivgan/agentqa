"""Speech helpers: cache keys, cache hits skipping the network, device resolution."""

from pathlib import Path

import pytest

from engine import speech


def test_cache_path_is_stable_and_distinguishes_inputs(tmp_path):
    a = speech.cache_path(tmp_path, "hey google", "v", "+0%", "+0%")
    assert a == speech.cache_path(tmp_path, "hey google", "v", "+0%", "+0%")
    assert a != speech.cache_path(tmp_path, "hey google!", "v", "+0%", "+0%")
    assert a != speech.cache_path(tmp_path, "hey google", "v2", "+0%", "+0%")
    assert a != speech.cache_path(tmp_path, "hey google", "v", "+10%", "+0%")
    assert a.parent == tmp_path and a.suffix == ".mp3"


def test_synthesize_uses_cache_without_edge_tts(tmp_path):
    path = speech.cache_path(tmp_path, "open weather", speech.DEFAULT_VOICE, speech.DEFAULT_RATE,
                             speech.DEFAULT_VOLUME)
    path.write_bytes(b"fake-mp3")
    assert speech.synthesize("open weather", tmp_path) == path


def test_synthesize_rejects_empty_text(tmp_path):
    with pytest.raises(speech.SpeechError):
        speech.synthesize("   ", tmp_path)


class FakeSD:
    def __init__(self, devices):
        self._devices = devices

    def query_devices(self):
        return self._devices


DEVICES = [{"name": "Microphone", "max_output_channels": 0},
           {"name": "Speakers (Realtek Audio)", "max_output_channels": 2},
           {"name": "USB Audio Device", "max_output_channels": 2}]


def test_resolve_output_device():
    sd = FakeSD(DEVICES)
    assert speech.resolve_output_device(sd, None) is None
    assert speech.resolve_output_device(sd, "") is None
    assert speech.resolve_output_device(sd, "3") == 3
    assert speech.resolve_output_device(sd, "usb audio") == 2
    with pytest.raises(speech.SpeechError, match="Available"):
        speech.resolve_output_device(sd, "bluetooth")
    with pytest.raises(speech.SpeechError):  # input-only device must not match
        speech.resolve_output_device(sd, "Microphone")


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SPEECH_TEST_A=1\nSPEECH_TEST_B='two'\n# c\n", encoding="utf-8")
    monkeypatch.setenv("SPEECH_TEST_A", "keep")
    monkeypatch.delenv("SPEECH_TEST_B", raising=False)
    speech.load_dotenv(env)
    import os
    assert os.environ["SPEECH_TEST_A"] == "keep"
    assert os.environ["SPEECH_TEST_B"] == "two"
    monkeypatch.delenv("SPEECH_TEST_B")
