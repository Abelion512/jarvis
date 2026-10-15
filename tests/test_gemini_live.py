"""Gemini Live session lifecycle against a fake transport. No network, no key."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class FakeSession:
    def __init__(self, callbacks):
        self.callbacks = callbacks
        self.sent = []
        self.closed = False

    async def send_pcm(self, chunk: bytes):
        self.sent.append(chunk)

    async def close(self):
        self.closed = True


async def _open(model, instruction, callbacks):
    assert "three" in instruction and "sir" in instruction
    return FakeSession(callbacks)


def test_lifecycle_connect_send_close():
    import gemini_live
    texts, audios = [], []

    async def _run():
        s = gemini_live.GeminiLiveSession(
            on_transcript=texts.append, on_audio=audios.append,
            open_session=_open, model="test-model")
        assert not s.running
        await s.connect()
        assert s.running
        await s.send_pcm(b"\x00\x01")
        s._session.callbacks["on_text"]("halo", True)
        s._session.callbacks["on_audio"](b"mp3")
        inner = s._session
        await s.close()
        assert not s.running
        assert inner.closed

    asyncio.run(_run())
    assert len(texts) == 1 and texts[0].text == "halo" and texts[0].is_final
    assert audios == [b"mp3"]


def test_send_before_connect_raises():
    import gemini_live
    import pytest

    async def _run():
        s = gemini_live.GeminiLiveSession(
            on_transcript=lambda t: None, on_audio=lambda b: None,
            open_session=_open)
        with pytest.raises(RuntimeError):
            await s.send_pcm(b"x")

    asyncio.run(_run())


def test_provider_defaults_to_webspeech(monkeypatch):
    import gemini_live
    monkeypatch.delenv("JARVIS_STT_PROVIDER", raising=False)
    assert gemini_live.provider() == "webspeech"


def test_missing_key_is_a_clear_error():
    import gemini_live
    import pytest
    monkeypatch_key = None
    import os
    old = os.environ.pop("GEMINI_API_KEY", None)
    try:
        with pytest.raises(RuntimeError) as e:
            asyncio.run(gemini_live.sdk_open_session("m", "i", {}))
        assert "GEMINI_API_KEY" in str(e.value) or "google-genai" in str(e.value)
    finally:
        if old is not None:
            os.environ["GEMINI_API_KEY"] = old
