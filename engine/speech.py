"""Text-to-speech synthesis (edge-tts) with an on-disk cache, and playback to an audio device.

No ADB or Robot Framework dependency. `edge_tts`, `sounddevice` and `soundfile` are imported lazily, so
importing this module (and `robot --dryrun`) works without them or without a network.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_VOICE = "en-US-AriaNeural"
DEFAULT_RATE = "+0%"
DEFAULT_VOLUME = "+0%"


class SpeechError(RuntimeError):
    pass


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


def cache_path(cache_dir: Path, text: str, voice: str, rate: str, volume: str) -> Path:
    """Stable file name for a phrase: the same text+voice+rate+volume always maps to the same file."""
    key = "\x1f".join((text, voice, rate, volume)).encode("utf-8")
    return cache_dir / f"{hashlib.sha256(key).hexdigest()[:24]}.mp3"


def synthesize(text: str, cache_dir: Path, voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE,
               volume: str = DEFAULT_VOLUME) -> Path:
    """Return the audio file for ``text``, generating it with edge-tts only on a cache miss."""
    if not text or not text.strip():
        raise SpeechError("Nothing to say: text is empty")
    path = cache_path(cache_dir, text, voice, rate, volume)
    if path.is_file() and path.stat().st_size > 0:
        return path
    try:
        import edge_tts
    except ImportError as e:
        raise SpeechError("edge-tts is not installed: pip install edge-tts") from e
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    try:
        asyncio.run(edge_tts.Communicate(text, voice, rate=rate, volume=volume).save(str(tmp)))
    except Exception as e:  # network down, bad voice name, service change ...
        tmp.unlink(missing_ok=True)
        raise SpeechError(f"Could not synthesize speech with voice '{voice}' (is the internet "
                          f"reachable?): {e}") from e
    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise SpeechError(f"edge-tts produced no audio for voice '{voice}' (is the voice name valid?)")
    tmp.replace(path)  # only a complete file ever has the final name
    return path


def resolve_output_device(sd, device: str | None):
    """Map a configured device (empty = default, digits = index, else name substring) for sounddevice."""
    if device is None or str(device).strip() == "":
        return None
    device = str(device).strip()
    if device.isdigit():
        return int(device)
    wanted = device.lower()
    for index, info in enumerate(sd.query_devices()):
        if info["max_output_channels"] > 0 and wanted in info["name"].lower():
            return index
    names = [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]
    raise SpeechError(f"No output audio device matching '{device}'. Available: {names}")


def play(path: Path, device: str | None = None, blocking: bool = True) -> float:
    """Play an audio file on the output device; returns its duration in seconds."""
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError as e:
        raise SpeechError("Playback needs sounddevice and soundfile: pip install sounddevice soundfile") from e
    try:
        data, rate = sf.read(str(path), dtype="float32")
    except Exception as e:
        raise SpeechError(f"Could not decode {path.name} (soundfile needs libsndfile >= 1.1 for MP3): {e}") from e
    sd.play(data, rate, device=resolve_output_device(sd, device))
    if blocking:
        sd.wait()
    return len(data) / float(rate)
