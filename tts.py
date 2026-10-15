"""
tts.py — Edge TTS synthesis, one request per sentence chunk.

Uses Microsoft Edge's online TTS service (no API key required). Supports
multiple languages via BCP-47 voice tags. The response is streamed so
time-to-first-byte can be measured; the chunk is returned whole because the
browser decodes one complete MP3 per chunk.

Three languages supported:
  - en  : English  (en-US-GuyNeural / en-GB-RyanNeural)
  - id  : Indonesian (id-ID-AndikaNeural)
  - zh  : Chinese/Simplified (zh-CN-YunjianNeural)

Voice selection is based on language hint from the brain turn context.
Fallback chain: requested language → English.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("jarvis.tts")

# Voice mapping: language code -> Edge TTS voice name
# These are stable Microsoft neural voices available via Edge TTS.
VOICE_MAP: dict[str, str] = {
    "en": "en-US-GuyNeural",     # English (US) male, professional tone
    "id": "id-ID-AndikaNeural",  # Indonesian male
    "zh": "zh-CN-YunjianNeural", # Chinese (China) male — JARVIS persona
}

DEFAULT_LANGUAGE = "en"

# Edge TTS WebSocket endpoint — no API key needed.
# The edge_tts library handles the WebSocket handshake and audio streaming.


@dataclass
class SynthResult:
    audio: bytes
    first_byte_sec: float
    total_sec: float


async def synthesize_chunk(
    text: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    voice_override: Optional[str] = None,
    client: Optional[object] = None,
    timeout: float = 15.0,
) -> Optional[SynthResult]:
    """Synthesize speech using Edge TTS.

    Args:
        text: The text to speak.
        language: BCP-47 language tag prefix (en, id, zh). Determines voice.
        voice_override: Explicit voice name to use instead of language-based selection.
        client: Ignored — kept for call-site compatibility during migration.
        timeout: Total time budget for synthesis.

    Voice resolution: explicit override → JARVIS_TTS_VOICE_<EN|ID|ZH> env →
    built-in VOICE_MAP → default English. The env knobs exist because
    "correct accent" is personal: e.g. id-ID-GadisNeural (female) instead
    of the default id-ID-AndikaNeural (male).

    Returns:
        SynthResult with audio bytes, or None on failure.
    """
    import os as _os

    text = (text or "").strip()
    if not text:
        return None

    try:
        import edge_tts
    except ImportError:
        log.error("TTS: edge_tts is not installed (pip install edge-tts)")
        return None

    # Select voice: explicit override > language-based > default
    voice_name = voice_override
    if voice_name is None:
        voice_name = _os.getenv(f"JARVIS_TTS_VOICE_{language.upper()}")
    if voice_name is None:
        voice_name = VOICE_MAP.get(language, VOICE_MAP[DEFAULT_LANGUAGE])

    t0 = time.monotonic()
    first_byte_sec: Optional[float] = None
    audio_data: Optional[bytes] = None

    try:
        # edge_tts streams word-boundary + audio chunks; keep the audio bytes.
        communicate = edge_tts.Communicate(text, voice_name)

        async def _collect():
            nonlocal first_byte_sec, audio_data
            chunks = []
            async for chunk in communicate.stream():
                # edge-tts 7.x yields {"type": "audio", "data": ...};
                # 6.x used "bytes". Accept both.
                if chunk.get("type") == "audio":
                    blob = chunk.get("data", chunk.get("bytes"))
                    if not blob:
                        continue
                    if first_byte_sec is None:
                        first_byte_sec = time.monotonic() - t0
                    chunks.append(blob)
            audio_data = b"".join(chunks) if chunks else None

        await asyncio.wait_for(_collect(), timeout=timeout)

    except asyncio.TimeoutError:
        log.error(f"TTS timeout after {timeout}s for {text[:40]!r}")
        return None
    except Exception as e:
        log.error(f"TTS error: {type(e).__name__}: {e}")
        return None

    if not audio_data:
        log.warning(f"TTS produced no audio for {text[:40]!r}")
        return None

    return SynthResult(
        audio=audio_data,
        first_byte_sec=first_byte_sec if first_byte_sec is not None else 0.0,
        total_sec=time.monotonic() - t0,
    )
