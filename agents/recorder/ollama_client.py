"""Adapter so a local Ollama model can be used wherever generate_with_llm() expects an
anthropic.Anthropic()-shaped client. It duck-types the one method generate_with_llm() calls
(`.messages.create(model=, max_tokens=, system=, messages=) -> response`) so the prompt,
validation and retry logic in generator.py stays identical for the cloud and local backends.
No new dependency: talks to Ollama's REST API with the stdlib's urllib.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import SimpleNamespace

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5-coder:7b"


class OllamaConnectionError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 300):
        self.model = model or DEFAULT_MODEL
        self.host = (host or DEFAULT_HOST).rstrip("/")
        self.timeout = timeout
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, *, system: str, messages: list[dict], **_ignored) -> SimpleNamespace:
        # `model`/`max_tokens` from the caller are ignored: this adapter always targets its own
        # configured Ollama model, and Ollama has no equivalent output-length cap to forward.
        payload = {"model": self.model, "stream": False,
                   "messages": [{"role": "system", "content": system}, *messages]}
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise OllamaConnectionError(
                f"could not reach Ollama at {self.host} ({e.reason if hasattr(e, 'reason') else e}). "
                f"Is `ollama serve` running, and has `ollama pull {self.model}` been run?") from e
        text = data.get("message", {}).get("content", "")
        # Ollama has no refusal concept comparable to Anthropic's stop_reason - every reply is
        # treated as a normal turn and goes through the same validate_robot() check either way.
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])
