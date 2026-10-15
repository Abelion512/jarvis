import importlib
import asyncio
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


class FakeBrain:
    """Stands in for brain.Brain: streams 'Echo: <text>.' in two deltas."""

    def __init__(self):
        self.ready = True
        self.failed = False
        self.turns = []

    async def stop(self):
        pass

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        from brain import TurnResult
        self.turns.append((text, origin))
        out = f"Echo: {text}."
        if on_delta:
            on_delta(out[:5])
            on_delta(out[5:])
        return TurnResult(origin=origin, text=out, stop_reason="result", context_tokens=1234,
                          output_tokens=5, duration_sec=0.01, first_delta_sec=0.001)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    import data_paths
    importlib.reload(data_paths)
    import run_store
    importlib.reload(run_store)
    import server
    importlib.reload(server)
    run_store.init_db()

    async def fake_synth(text):
        return b"MP3:" + text.encode()

    monkeypatch.setattr(server, "_synth_for_speech", fake_synth)
    server._last_greeting_time = time.time()          # suppress the greeting by default
    # The voice page's own Origin. /ws/voice refuses any other, because a
    # socket that reaches _handle_utterance speaks with origin="user".
    with TestClient(server.app,
                    headers={"Origin": "http://localhost:5173"}) as c:
        server.brain_instance = FakeBrain()
        yield c, server


def _drain_until(ws, predicate, limit=10):
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if predicate(msg):
            return seen
    raise AssertionError(f"never saw expected message; got {seen}")


def test_connect_sends_config_and_idle(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        assert ws.receive_json() == {"type": "config", "muteMicDuringSpeech": False}
        assert ws.receive_json() == {"type": "status", "state": "idle"}


def test_greeting_is_spoken_through_the_scheduler(client):
    c, server = client
    server._last_greeting_time = 0
    with c.websocket_connect("/ws/voice") as ws:
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        audio = seen[-1]
        assert audio["idx"] == 0 and audio["text"].startswith("Good ")
        assert audio["text"].endswith("sir.")


def test_transcript_streams_chunks_and_acks_return_to_idle(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        assert {"type": "status", "state": "thinking"} in seen
        assert {"type": "status", "state": "speaking"} in seen
        audio = seen[-1]
        assert audio["text"] == "Echo: hello there." and audio["idx"] == 0
        ws.send_json({"type": "played", "utt": audio["utt"], "idx": audio["idx"]})
        seen = _drain_until(ws, lambda m: m == {"type": "status", "state": "idle"})
        assert server.brain_instance.turns == [("hello there", "user")]


def test_echo_of_jarvis_own_words_is_ignored(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        # the microphone hears JARVIS: same words come back as a transcript
        ws.send_json({"type": "transcript", "text": "echo hello there", "isFinal": True})
        ws.send_json({"type": "interim", "text": "echo hello"})
        ws.send_json({"type": "transcript", "text": "what is the weather like", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio" and m["text"] != "Echo: hello there.")
        assert seen[-1]["text"] == "Echo: what is the weather like."
        assert [t for t, _ in server.brain_instance.turns] == ["hello there", "what is the weather like"]


def test_say_that_again_replays_without_a_brain_turn(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        audio = seen[-1]
        ws.send_json({"type": "played", "utt": audio["utt"], "idx": audio["idx"]})
        _drain_until(ws, lambda m: m == {"type": "status", "state": "idle"})

        ws.send_json({"type": "transcript", "text": "say that again", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        replay = seen[-1]
        assert replay["text"] == "Echo: hello there."
        assert replay["data"] == audio["data"], "identical audio bytes, not re-synthesized"
        assert server.brain_instance.turns == [("hello there", "user")], "no brain turn for the replay"


def test_a_real_question_is_not_treated_as_a_replay(client):
    """"What did you mean by that" is a real question and must reach the
    brain, unlike "say that again"."""
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True})
        _drain_until(ws, lambda m: m["type"] == "audio")
        ws.send_json({"type": "transcript", "text": "what did you mean by that", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio" and m["text"] != "Echo: hello there.")
        assert seen[-1]["text"] == "Echo: what did you mean by that."
        assert [t for t, _ in server.brain_instance.turns] == \
            ["hello there", "what did you mean by that"]


def test_say_that_again_with_nothing_said_yet(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "say that again", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        assert seen[-1]["text"] == server.NOTHING_TO_REPLAY_LINE
        assert server.brain_instance.turns == []


def test_speech_corrections_still_apply(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "open cloud code", "isFinal": True})
        _drain_until(ws, lambda m: m["type"] == "audio")
        assert server.brain_instance.turns[0][0] == "open Claude Code"


def test_brain_not_ready_says_so_instead_of_hanging(client):
    c, server = client
    server.brain_instance.ready = False
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        assert "still starting" in seen[-1]["text"]
        assert server.brain_instance.turns == []


def test_lifespan_builds_brain_without_spawning_under_test(client):
    c, server = client
    # the fixture replaced brain_instance after startup; the one lifespan built must not be running
    assert server.speech is not None
    assert server.brain_instance.turns == []


# ── failure paths (from the Task 7 review) ──────────────────────────────

class ExplodingBrain(FakeBrain):
    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        self.turns.append((text, origin))
        raise RuntimeError("boom")


def test_a_raising_turn_still_ends_the_utterance_and_speaks_a_recovery_line(client):
    c, server = client
    server.brain_instance = ExplodingBrain()
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        assert "lost my train of thought" in seen[-1]["text"]
        # the failed turn's utterance was closed, not left open: a new turn plays normally
        server.brain_instance = FakeBrain()
        ws.send_json({"type": "played", "utt": seen[-1]["utt"], "idx": seen[-1]["idx"]})
        ws.send_json({"type": "transcript", "text": "still there", "isFinal": True})
        _drain_until(ws, lambda m: m["type"] == "audio" and m["text"] == "Echo: still there.")


def test_voice_emit_raises_for_content_with_no_client_but_not_for_status(client):
    """The scheduler abandons an utterance whose audio reaches nobody (instead of
    waiting forever for an ack); status frames into the void are simply lost."""
    import asyncio
    c, server = client
    assert not server.voice_clients
    with pytest.raises(server.NoVoiceClient):
        asyncio.run(server._voice_emit({"type": "audio", "utt": 1, "idx": 0, "data": "", "text": "x"}))
    with pytest.raises(server.NoVoiceClient):
        asyncio.run(server._voice_emit({"type": "text", "text": "x"}))
    asyncio.run(server._voice_emit({"type": "status", "state": "idle"}))      # no raise
    asyncio.run(server._voice_emit({"type": "stop"}))                          # no raise


def test_a_client_that_dies_mid_send_is_dropped_and_others_still_get_frames(client):
    """A frame now goes on each client's own queue and a writer sends it, so
    deadness is discovered by the writer rather than by the emit. What must
    not change is the outcome: the dead one goes, the live one is served.
    See test_voice_backpressure.py for why the queue is there at all."""
    import asyncio
    c, server = client

    class Dead:
        async def send_json(self, msg):
            raise RuntimeError("gone")

    class Alive:
        def __init__(self):
            self.got = []

        async def send_json(self, msg):
            self.got.append(msg)

    dead, alive = Dead(), Alive()

    async def scenario():
        server._add_voice_client(dead)
        server._add_voice_client(alive)
        await server._voice_emit({"type": "audio", "utt": 1, "idx": 0,
                                  "data": "", "text": "x"})
        async with asyncio.timeout(2):
            while dead in server.voice_clients or not alive.got:
                await asyncio.sleep(0.01)

    try:
        asyncio.run(scenario())
        assert alive.got and dead not in server.voice_clients
        assert alive in server.voice_clients
    finally:
        server.voice_clients.discard(alive)
        server._voice_queues.pop(alive, None)
        server._voice_writers.pop(alive, None)


def test_fmt_reset_names_the_day_when_it_is_not_today(client):
    """A seven-day window resets days away: "until 10 AM" on Monday at 10 AM is
    both wrong and confusing. Found live 2026-09-02."""
    from datetime import datetime, timedelta
    c, server = client
    now = datetime.now()

    def at(days, hour=10):
        return (now.replace(hour=hour, minute=0, second=0, microsecond=0)
                + timedelta(days=days)).timestamp()

    assert server._fmt_reset(at(0)) == "10 AM"
    assert server._fmt_reset(at(1)) == "tomorrow at 10 AM"
    three = server._fmt_reset(at(3))
    assert three.endswith(" at 10 AM") and three.split(" at ")[0].isalpha()
    far = server._fmt_reset(at(9))
    assert far.endswith(" at 10 AM") and any(ch.isdigit() for ch in far.split(" at ")[0])
    assert server._fmt_reset(None) == "later"
    assert server._fmt_reset("not a time") == "later"


def test_transcript_carrying_a_language_tag_completes(client):
    """The EN/ID/ZH selector tags transcripts with `lang`. An Indonesian
    turn must complete the same audio round trip, not die on the tag."""
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "halo apa kabar", "isFinal": True, "lang": "id"})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        audio = seen[-1]
        assert audio["text"] == "Echo: [user language: Indonesian] halo apa kabar."
        assert server.brain_instance.turns == [
            ("[user language: Indonesian] halo apa kabar", "user")]


# --- learned profile: corrections + language preference ---------------------

def _isolated_profile(monkeypatch, tmp_path):
    import data_paths
    profile = tmp_path / "profile.json"
    monkeypatch.setattr(data_paths, "profile_path", lambda: profile)
    return profile


def test_learned_correction_rewrites_future_transcripts(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert server.learn_correction("chitauri", "Chitauri Prime") is None
    assert server.apply_speech_corrections("open chitauri") == "open Chitauri Prime"
    # The shipped guesses still work underneath.
    assert server.apply_speech_corrections("open cloud code") == "open Claude Code"


def test_learned_correction_overrules_the_shipped_guesses(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert server.learn_correction("cloud", "awan") is None
    assert server.apply_speech_corrections("the cloud") == "the awan"


def test_correction_refusals_are_plain_sentences(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert "sir" in (server.learn_correction("", "x") or "")
    assert "sir" in (server.learn_correction("x", "x") or "")
    assert "sir" in (server.learn_correction("x" * 41, "y") or "")
    assert server.apply_speech_corrections("plain text") == "plain text"


def test_language_majority_becomes_preferred(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert server.note_language("id") == "id"    # first turn flips a fresh install
    assert server.note_language("en") == "en"    # 1-1 tie goes to most recent
    assert server.note_language("id") == "id"    # 2-1 majority
    assert server.note_language("id") == "id"
    assert server._read_profile()["preferred_lang"] == "id"


def test_unknown_language_is_ignored(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert server.note_language("xx") == "en"
    assert server._read_profile().get("lang_counts", {}).get("xx") is None


def test_english_guesses_never_rewrite_other_languages(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    # "cloud" is a real word outside English — leave it alone there.
    assert server.apply_speech_corrections("the cloud", "id") == "the cloud"
    assert server.apply_speech_corrections("the cloud", "en") == "the Claude"
    # Name fixes hold everywhere.
    assert server.apply_speech_corrections("hai travis", "id") == "hai JARVIS"


def test_learned_pairs_stay_in_their_language(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert server.learn_correction("awan", "awan?", lang="id") is None
    assert server.apply_speech_corrections("lihat awan", "id") == "lihat awan?"
    assert server.apply_speech_corrections("lihat awan", "en") == "lihat awan"


def test_tts_lang_does_not_leak_when_brain_not_ready(client):
    c, server = client
    server.brain_instance.ready = False
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "halo", "isFinal": True, "lang": "id"})
        _drain_until(ws, lambda m: m["type"] == "audio")
    assert server._tts_lang.get() == "en"


# --- gaps: invalid lang tags, overlap isolation, corrupt profile ----------

def test_invalid_lang_tag_via_ws_falls_back_to_english(client):
    """Garbage tags ("xx", "") must complete the round trip in English,
    not die or leak into the TTS voice."""
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True, "lang": "xx"})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        assert seen[-1]["text"] == "Echo: hello there."
        ws.send_json({"type": "played", "utt": seen[-1]["utt"], "idx": seen[-1]["idx"]})
        _drain_until(ws, lambda m: m == {"type": "status", "state": "idle"})
        ws.send_json({"type": "transcript", "text": "still here", "isFinal": True, "lang": ""})
        seen = _drain_until(ws, lambda m: m["type"] == "audio" and m["text"] == "Echo: still here.")
        assert [t for t, _ in server.brain_instance.turns] == ["hello there", "still here"]
    assert server._tts_lang.get() == "en"


def test_overlapping_utterances_keep_separate_voices(client, monkeypatch, tmp_path):
    """A barge-in overlaps the turn in flight; each keeps its own voice via
    the ContextVar instead of racing on one variable."""
    import asyncio
    from types import SimpleNamespace
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)

    class SpyBrain(FakeBrain):
        rotation_pending = False
        current_origin = None
        rotation_overdue = False

        def __init__(self):
            super().__init__()
            self.lang_seen = {}

        async def turn(self, text, origin="user", on_delta=None, on_tool=None):
            await asyncio.sleep(0.2)          # hold the overlap window open
            self.lang_seen[text] = server._tts_lang.get()
            from brain import TurnResult
            return TurnResult(origin=origin, text=f"Echo: {text}.", stop_reason="result",
                              context_tokens=1, output_tokens=1, duration_sec=0.01,
                              first_delta_sec=0.001)

    class QuietSpeech:
        def begin_turn(self):
            return SimpleNamespace(first_sent_at=None)

        def feed(self, utt, d):
            pass

        async def end_turn(self, utt):
            pass

        async def wait_for(self, utt, timeout=None):
            pass

        async def stop(self):
            pass

    spy = SpyBrain()
    monkeypatch.setattr(server, "brain_instance", spy)
    monkeypatch.setattr(server, "speech", QuietSpeech())

    async def both():
        await asyncio.gather(server._handle_utterance("halo apa kabar", "id"),
                             server._handle_utterance("hello there", "en"))

    asyncio.run(both())
    assert spy.lang_seen == {"[user language: Indonesian] halo apa kabar": "id", "hello there": "en"}


def test_corrupt_profile_on_the_hot_path_still_completes(client, monkeypatch, tmp_path):
    """Garbage profile.json must not break a live transcript: readers
    tolerate it and learning restarts from scratch."""
    c, server = client
    profile = _isolated_profile(monkeypatch, tmp_path)
    profile.write_text("{not json", encoding="utf-8")
    assert server._read_profile() == {}
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio")
        assert seen[-1]["text"] == "Echo: hello there."


def test_read_profile_tolerates_garbage_and_wrong_shapes(client, monkeypatch, tmp_path):
    c, server = client
    profile = _isolated_profile(monkeypatch, tmp_path)
    assert server._read_profile() == {}          # missing file
    for bad in ("{not json", "", "[1, 2]", '"just a string"', "123", "null"):
        profile.write_text(bad, encoding="utf-8")
        assert server._read_profile() == {}, bad


def test_learned_corrections_skip_oversized_entries(client, monkeypatch, tmp_path):
    import json
    c, server = client
    profile = _isolated_profile(monkeypatch, tmp_path)
    profile.write_text(json.dumps({"corrections": {
        "a" * 41: "short",                     # heard too long
        "ok": "b" * 41,                        # meant too long
        "fine": "fixed",
        "fine2": {"meant": "c" * 41, "lang": "en"},
        "fine3": {"meant": "fixed3", "lang": "en"},
    }}), encoding="utf-8")
    assert set(server._learned_corrections()) == {"fine", "fine3"}
    assert server.apply_speech_corrections("say fine and fine3") == "say fixed and fixed3"


def test_learn_correction_refuses_control_chars(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert "sir" in (server.learn_correction("a\nb", "c") or "")
    assert "sir" in (server.learn_correction("a", "b\tc") or "")


def test_learn_correction_refuses_long_or_empty_meant(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert "sir" in (server.learn_correction("heard", "y" * 41) or "")
    assert "sir" in (server.learn_correction("heard", "") or "")
    assert "sir" in (server.learn_correction("   ", "meant") or "")


def test_correction_cap_evicts_oldest_first(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    for i in range(100):
        assert server.learn_correction(f"k{i:03d}", f"v{i:03d}") is None
    assert len(server._read_profile()["corrections"]) == 100
    assert server.learn_correction("newest", "newval") is None
    corrections = server._read_profile()["corrections"]
    assert len(corrections) == 100
    assert "k000" not in corrections and corrections["newest"]["meant"] == "newval"
    assert server.apply_speech_corrections("say k000") == "say k000"


class _SilentBrain(FakeBrain):
    """Answers with no deltas and a delay, so the filler watchdog fires."""

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        from brain import TurnResult
        import asyncio as _aio
        self.turns.append((text, origin))
        await _aio.sleep(0.3)
        return TurnResult(origin=origin, text="Echo: done.", stop_reason="result",
                          context_tokens=1, output_tokens=1,
                          duration_sec=0.3, first_delta_sec=None)


def test_filler_bridges_a_silent_turn(client, monkeypatch):
    c, server = client
    monkeypatch.setattr(server, "FILLER_AFTER_SEC", 0.05)
    server.brain_instance = _SilentBrain()
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello", "isFinal": True})
        seen = _drain_until(ws, lambda m: m["type"] == "audio" and m["text"] == "One moment, sir.")
        assert seen[-1]["text"] == "One moment, sir."


def test_non_english_turn_is_tagged_for_the_brain(client):
    """Plan task 1: the reply language must not depend on the model
    guessing. Non-English turns carry an explicit tag; English passes
    through verbatim (pinned by every older test here)."""
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "halo apa kabar", "isFinal": True, "lang": "id"})
        _drain_until(ws, lambda m: m["type"] == "audio")
        assert server.brain_instance.turns == [
            ("[user language: Indonesian] halo apa kabar", "user")]


# --- unified language detection (no modes) --------------------------------

def test_detect_lang_cjk_is_chinese(client):
    assert client[1].detect_lang("你好吗") == "zh"
    assert client[1].detect_lang("mix 你好 mix") == "zh"


def test_detect_lang_indonesian_markers(client):
    assert client[1].detect_lang("apa kabar kamu hari ini") == "id"
    assert client[1].detect_lang("halo") == "id"
    assert client[1].detect_lang("terima kasih banyak") == "id"


def test_detect_lang_defaults_english(client):
    assert client[1].detect_lang("hello there") == "en"
    assert client[1].detect_lang("") == "en"
    assert client[1].detect_lang("apple cafe") == "en"


@pytest.mark.asyncio
async def test_tool_start_names_plain_work_on_screen(client, monkeypatch):
    """Transparency: a tool_use during a turn shows a plain-words notice
    (category prose, never identifiers), then clears it when the turn ends."""
    c, server = client
    notices = []
    orig_emit = server._voice_emit

    async def spy(msg):
        if isinstance(msg, dict) and msg.get("type") == "notice":
            notices.append(msg.get("text"))
        return await orig_emit(msg)

    monkeypatch.setattr(server, "_voice_emit", spy)
    hold = server._OneLinePerTurn(lambda d: None)
    hold.tool_started("mcp__jarvis__read_file")
    hold.tool_started("mcp__jarvis__remember")
    hold.tool_started("mcp__jarvis__spawn_run")
    hold.tool_started("")
    await asyncio.sleep(0)  # let the spawned notice emits land
    await server._voice_emit({"type": "notice", "text": ""})
    assert "Looking it up" in notices[0], notices
    assert "Noting that down" in notices[1], notices
    assert "Working on it" in notices[2], notices
    assert notices[3] == ""


def test_applied_correction_bumps_hit_count(client, monkeypatch, tmp_path):
    c, server = client
    _isolated_profile(monkeypatch, tmp_path)
    assert server.learn_correction("awan", "awan?", lang="id") is None
    assert server.apply_speech_corrections("lihat awan", "id") == "lihat awan?"
    assert server.apply_speech_corrections("lihat awan", "id") == "lihat awan?"
    profile = server._read_profile()
    assert profile["corrections"]["awan"]["hits"] == 2
    # A miss bumps nothing and breaks nothing.
    assert server.apply_speech_corrections("cerah", "id") == "cerah"
    assert server._read_profile()["corrections"]["awan"]["hits"] == 2


def test_chunk_voices_follow_code_switches(client):
    """One turn, three voices: each chunk speaks its own language, and weak
    evidence never flips the turn voice (short 'Ya' stays Indonesian)."""
    c, server = client
    cases = [
        # (turn lang, chunk, expected voice)
        ("en", "Hello there", "en"),
        ("en", "apa kabar kamu hari ini", "id"),
        ("en", "你好吗", "zh"),
        ("id", "Baik sir", "id"),
        ("id", "Ya", "id"),
        ("id", "the build sudah selesai sir", "id"),
        ("id", "你好", "zh"),
        ("zh", "Hello sir", "en"),
        ("zh", "构建完成了", "zh"),
    ]
    for turn_lang, chunk, expected in cases:
        server._tts_lang.set(turn_lang)
        assert server._resolve_chunk_lang(chunk) == expected, (turn_lang, chunk)
