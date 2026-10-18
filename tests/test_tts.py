"""Tests for tts.py — Edge TTS synthesis.

These tests verify the Edge TTS integration. Since edge_tts uses WebSockets
to Microsoft's service, offline tests verify structure and parameter handling.
Network-dependent tests are marked and skipped when offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.asyncio
async def test_synthesize_chunk_rejects_empty_text():
    import tts
    result = await tts.synthesize_chunk("   ", language="en")
    assert result is None


@pytest.mark.asyncio
async def test_synthesize_chunk_rejects_missing_text():
    import tts
    result = await tts.synthesize_chunk("", language="en")
    assert result is None


@pytest.mark.asyncio
async def test_voice_map_contains_three_languages():
    import tts
    assert "en" in tts.VOICE_MAP
    assert "id" in tts.VOICE_MAP
    assert "zh" in tts.VOICE_MAP
    assert tts.VOICE_MAP["en"] == "en-US-GuyNeural"
    assert tts.VOICE_MAP["id"] == "id-ID-AndikaNeural"
    assert tts.VOICE_MAP["zh"] == "zh-CN-YunjianNeural"


@pytest.mark.asyncio
async def test_default_language_is_english():
    import tts
    assert tts.DEFAULT_LANGUAGE == "en"


@pytest.mark.asyncio
async def test_synthesize_chunk_signature():
    """Verify the new Edge TTS interface accepts the expected parameters."""
    import tts
    import inspect
    sig = inspect.signature(tts.synthesize_chunk)
    params = list(sig.parameters.keys())
    assert "language" in params
    assert "voice_override" in params
    assert "client" in params  # kept for call-site compatibility
    assert "timeout" in params


@pytest.mark.asyncio
async def test_synth_result_dataclass():
    import tts
    r = tts.SynthResult(audio=b"test", first_byte_sec=0.5, total_sec=1.0)
    assert r.audio == b"test"
    assert r.first_byte_sec == 0.5
    assert r.total_sec == 1.0


@pytest.mark.asyncio
async def test_timeout_parameter_default():
    import tts
    import inspect
    sig = inspect.signature(tts.synthesize_chunk)
    assert sig.parameters["timeout"].default == 15.0


@pytest.mark.asyncio
async def test_client_parameter_is_accepted_for_compatibility():
    """The client parameter is kept for call-site compatibility during migration."""
    import tts
    import inspect
    sig = inspect.signature(tts.synthesize_chunk)
    assert "client" in sig.parameters
    assert sig.parameters["client"].default is None


@pytest.mark.asyncio
async def test_voice_override_takes_precedence(monkeypatch):
    """When voice_override is set, it should be used instead of language-based selection."""
    import sys
    import types
    import tts
    seen = {}

    class FakeCommunicate:
        def __init__(self, text, voice):
            seen["voice"] = voice
            seen["text"] = text

        async def stream(self):
            yield {"type": "audio", "data": b"1234"}
            return

    fake_mod = types.ModuleType("edge_tts")
    fake_mod.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_mod)
    r = await tts.synthesize_chunk("hello", language="en", voice_override="custom-voice")
    assert r is not None and r.audio == b"1234"
    assert seen["voice"] == "custom-voice"


def test_falls_back_to_english_for_unknown_language(monkeypatch):
    """Unknown language codes fall back to the default English voice."""
    import sys
    import types
    import asyncio
    import tts
    seen = {}

    class FakeCommunicate:
        def __init__(self, text, voice):
            seen["voice"] = voice

        async def stream(self):
            yield {"type": "audio", "data": b"1234"}
            return

    fake_mod = types.ModuleType("edge_tts")
    fake_mod.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_mod)
    r = asyncio.run(tts.synthesize_chunk("hello", language="xx"))
    assert r is not None and r.audio == b"1234"
    assert seen["voice"] == "en-US-GuyNeural"


def test_voice_env_override_wins_over_map(monkeypatch):
    """Behavioral, offline: a stub edge_tts records the voice name."""
    import sys
    import types
    import tts
    seen = {}

    class FakeCommunicate:
        def __init__(self, text, voice):
            seen["voice"] = voice

        async def stream(self):
            yield {"type": "audio", "data": b"1234"}
            return

    fake_mod = types.ModuleType("edge_tts")
    fake_mod.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_mod)
    monkeypatch.setenv("JARVIS_TTS_VOICE_ID", "id-ID-GadisNeural")
    import asyncio
    r = asyncio.run(tts.synthesize_chunk("halo", language="id"))
    assert r is not None and r.audio == b"1234"
    assert seen["voice"] == "id-ID-GadisNeural"
    monkeypatch.delenv("JARVIS_TTS_VOICE_ID")
    r = asyncio.run(tts.synthesize_chunk("halo", language="id"))
    assert seen["voice"] == "id-ID-AndikaNeural"
