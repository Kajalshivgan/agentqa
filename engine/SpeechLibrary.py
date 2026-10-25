"""Robot Framework keyword library that speaks text to a device's microphone via the PC speaker.

Usage in a suite:
    Library    engine.SpeechLibrary    device=emulator-5554

Defaults come from the environment / .env: SPEECH_VOICE, SPEECH_RATE, SPEECH_VOLUME,
SPEECH_OUTPUT_DEVICE (empty = the PC default speaker).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from robot.api import logger
from robot.api.deco import keyword, library

from engine import speech
from engine.adb_client import ADBClient

ROOT = Path(__file__).resolve().parent.parent


@library(scope="SUITE", auto_keywords=False)
class SpeechLibrary:
    def __init__(self, device: str | None = None, voice: str | None = None, rate: str | None = None,
                 volume: str | None = None, output_device: str | None = None,
                 cache_dir: str = "recordings/speech", listen_delay: float = 1.5):
        speech.load_dotenv()
        self._device = device or None  # ADB serial
        self.voice = voice or os.environ.get("SPEECH_VOICE") or speech.DEFAULT_VOICE
        self.rate = rate or os.environ.get("SPEECH_RATE") or speech.DEFAULT_RATE
        self.volume = volume or os.environ.get("SPEECH_VOLUME") or speech.DEFAULT_VOLUME
        self.output_device = output_device or os.environ.get("SPEECH_OUTPUT_DEVICE") or None
        self.cache_dir = Path(cache_dir) if os.path.isabs(cache_dir) else ROOT / cache_dir
        self.listen_delay = float(listen_delay)
        self._adb: ADBClient | None = None

    @property
    def adb(self) -> ADBClient:
        if self._adb is None:  # connect lazily so `robot --dryrun` works without a device
            self._adb = ADBClient(self._device)
        return self._adb

    @keyword("Speak")
    def speak(self, text: str, voice: str | None = None, rate: str | None = None,
              volume: str | None = None):
        """Say ``text`` out loud on the PC speaker and wait until it has finished playing.

        The audio is generated with edge-tts (needs internet) the first time and cached afterwards,
        so repeated runs of the same phrase play instantly and offline.
        """
        path = speech.synthesize(text, self.cache_dir, voice or self.voice, rate or self.rate,
                                 volume or self.volume)
        seconds = speech.play(path, self.output_device)
        logger.info(f"Spoke '{text}' ({seconds:.1f}s, {path.name})")

    @keyword("Launch Assistant By ADB")
    def launch_assistant_by_adb(self, listen_delay: float | None = None):
        """Open the voice assistant with KEYCODE_VOICE_ASSIST, bypassing the "Hey Google" hotword.

        Waits ``listen_delay`` seconds (default: library setting) for the assistant to start listening.
        """
        self.adb.keyevent("KEYCODE_VOICE_ASSIST")
        time.sleep(self.listen_delay if listen_delay is None else float(listen_delay))

    @keyword("Speak Command")
    def speak_command(self, text: str, listen_delay: float | None = None):
        """Launch the assistant by ADB, then speak only the command (no hotword), e.g. "open weather"."""
        self.launch_assistant_by_adb(listen_delay)
        self.speak(text)

    @keyword("Pre Generate Speech")
    def pre_generate_speech(self, *phrases: str):
        """Synthesize and cache phrases without playing them, so a run needs no internet later."""
        for text in phrases:
            path = speech.synthesize(text, self.cache_dir, self.voice, self.rate, self.volume)
            logger.info(f"Cached '{text}' -> {path.name}")
