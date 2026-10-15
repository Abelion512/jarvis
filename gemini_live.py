"""
gemini_live.py — Gemini Live API voice session (the WebSpeech replacement).

One session carries all three languages as a unit: input audio goes in,
transcripts and reply audio come out, and the model follows the user's
language turn by turn under the system instruction below. No sr.lang, no
toggle, no per-language mode anywhere.

STT provider selection lives here but defaults OFF:

    JARVIS_STT_PROVIDER=webspeech  (default — current behavior, unchanged)
    JARVIS_STT_PROVIDER=gemini     (this module; needs GEMINI_API_KEY)

The SDK (`google-genai`) is imported lazily inside `connect()` so the
server boots, preflights, and tests fine without it. Transports are
injected (`open_session` callable) so tests drive the whole lifecycle
with fakes and no network.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("jarvis.gemini_live")

PROVIDER_ENV = "JARVIS_STT_PROVIDER"
API_KEY_ENV = "GEMINI_API_KEY"
MODEL_ENV = "JARVIS_GEMINI_MODEL"
DEFAULT_MODEL = "gemini-2.5-flash-native-audio-latest"

# The unity contract, in the model's own context: one conversation, three
# languages, reply always in the language just heard. Spoken style matches
# jarvis_home/CLAUDE.md (short, no markdown) because this path speaks
# directly — nothing downstream re-shapes it.
SYSTEM_INSTRUCTION = (
    "You are JARVIS, a British-butler voice assistant. The user speaks "
    "English, Indonesian, or Chinese, sometimes switching mid-conversation: "
    "treat all three as one conversation, never ask which language, and "
    "always reply in the language just used. ONE sentence ideally, TWO at "
    "most. No markdown, no lists, no emoji — every word is spoken aloud. "
    "Address the user as sir."
)


def provider() -> str:
    return (os.getenv(PROVIDER_ENV, "webspeech") or "webspeech").lower()


def model_name() -> str:
    return os.getenv(MODEL_ENV, DEFAULT_MODEL)


@dataclass
class LiveTranscript:
    text: str
    is_final: bool
    lang: str = ""  # detected downstream when empty


class GeminiLiveSession:
    """One full-duplex voice session. Created per conversation, closed after.

    `open_session` is `(model, system_instruction, callbacks) -> session`
    where session has `send_pcm(bytes)`, `send_text(str)` and `close()`,
    and callbacks is a dict with `on_text(str, is_final)` / `on_audio(bytes)`
    / `on_close()`. Production passes the SDK adapter (`sdk_open_session`
    below); tests pass fakes.
    """

    def __init__(self, *,
                 on_transcript: Callable[[LiveTranscript], None],
                 on_audio: Callable[[bytes], None],
                 open_session: Optional[Callable] = None,
                 model: Optional[str] = None):
        self._on_transcript = on_transcript
        self._on_audio = on_audio
        self._open_session = open_session or sdk_open_session
        self._model = model or model_name()
        self._session: Any = None
        self._receiver: Optional[asyncio.Task] = None
        self.closed = False

    @property
    def running(self) -> bool:
        return self._session is not None and not self.closed

    async def connect(self) -> None:
        if self.running:
            return
        self._session = await self._open_session(
            self._model, SYSTEM_INSTRUCTION,
            {"on_text": self._handle_text, "on_audio": self._handle_audio,
             "on_close": self._handle_close})
        self.closed = False

    async def send_pcm(self, chunk: bytes) -> None:
        if not self.running:
            raise RuntimeError("live session is not connected")
        await self._session.send_pcm(chunk)

    async def close(self) -> None:
        self.closed = True
        if self._receiver and not self._receiver.done():
            self._receiver.cancel()
        session, self._session = self._session, None
        if session is not None:
            try:
                await session.close()
            except Exception as e:  # never let teardown raise
                log.warning(f"live session close failed: {e}")

    def _handle_text(self, text: str, is_final: bool) -> None:
        try:
            self._on_transcript(LiveTranscript(text=text, is_final=is_final))
        except Exception as e:  # a listener must never break the session
            log.warning(f"live transcript listener failed: {e}")

    def _handle_audio(self, data: bytes) -> None:
        try:
            self._on_audio(data)
        except Exception as e:
            log.warning(f"live audio listener failed: {e}")

    def _handle_close(self) -> None:
        self._session = None


async def sdk_open_session(model: str, system_instruction: str,
                           callbacks: dict) -> Any:
    """The production transport over `google-genai`. Imported here (not at
    module top) so everything else works without the SDK installed."""
    try:
        from google import genai  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "google-genai is not installed (pip install google-genai)")
    api_key = os.getenv(API_KEY_ENV, "")
    if not api_key:
        raise RuntimeError(
            f"{API_KEY_ENV} is not set — Gemini Live needs it")
    raise RuntimeError(
        "live audio transport lands with the frontend PCM path; "
        "transcription spike first (see scripts/gemini_spike.py)")
