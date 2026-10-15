#!/usr/bin/env python3
"""JARVIS autonomous end-to-end self-test. No mic, no network, no real CLI.

Everything the suite fakes, this drives in one process, in dependency
order, and says plainly which stage broke:

    .venv/bin/python scripts/selftest_e2e.py

Stages: imports → preflight → TTS (offline) → brain via fake Hermes →
brain via fake Claude → voice WebSocket (transcript → audio → ack) →
run pipeline (spawn → terminal state) → HTTP settings/status.

Exit 0 only when every stage passes. Uses a temp JARVIS_DATA_DIR, so live
data is never touched.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import stat
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="jarvis-selftest-"))
os.environ["JARVIS_DATA_DIR"] = str(TMP)
os.environ["JARVIS_BRAIN_AUTOSTART"] = "0"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        RESULTS.append((name, True, str(detail)))
        print(f"[ok] {name}" + (f" — {detail}" if detail else ""))
    except Exception as e:  # noqa: BLE001 — the report IS the handling
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)


def stage_imports():
    import server  # noqa: F401
    import brain  # noqa: F401
    import tts  # noqa: F401
    import preflight  # noqa: F401
    import run_executor  # noqa: F401
    return "server brain tts preflight run_executor"


def stage_preflight():
    import preflight

    async def _run():
        return await asyncio.wait_for(preflight.run_checks(timeout=5.0), timeout=30.0)

    checks = asyncio.run(_run())
    assert checks, "no checks ran"
    names = {c.name for c in checks}
    assert "edge_tts" in names, names
    return f"{len(checks)} checks, edge_tts present"


def stage_tts_offline():
    import tts

    async def _run():
        assert await tts.synthesize_chunk("   ") is None
        assert await tts.synthesize_chunk("") is None

    asyncio.run(_run())
    assert set(("en", "id", "zh")) <= set(tts.VOICE_MAP)
    return "empty rejected, 3 voices"


def _hermes_cfg(tmp_path):
    import brain

    return brain.BrainConfig(
        home=tmp_path / "jarvis-hermes",
        cli_path=f"{sys.executable} {ROOT / 'tests' / 'fixtures' / 'fake_hermes.py'}",
        model="kgw/kilo-auto/free",
        turn_timeout=15.0,
        warmup_timeout=15.0,
    )


def stage_brain_hermes():
    import brain

    async def _run():
        b = brain.Brain(_hermes_cfg(TMP))
        assert await b.start(), "hermes warm-up failed"
        assert b.ready and b.session_id, "not ready after start"
        first = b.session_id
        r = await b.turn("hello selftest")
        assert r.stop_reason == "result", r
        assert "hello selftest" in r.text, r.text
        r2 = await b.turn("second turn")
        assert r2.stop_reason == "result", r2
        assert b.session_id == first, "session was not resumed"
        r3 = await b.turn("HERMESERROR please")
        assert r3.stop_reason == "error" and not r3.text, r3
        await b.stop()
        return first

    return f"start/turn/resume/error ok (session {asyncio.run(_run())})"


def stage_brain_claude():
    import brain

    fake = ROOT / "tests" / "fixtures" / "fake_brain.py"
    cfg = brain.BrainConfig(home=TMP / "jarvis-claude",
                            claude_path=f"{sys.executable} {fake}",
                            turn_timeout=10.0, warmup_timeout=15.0)

    async def _run():
        b = brain.Brain(cfg)
        assert await b.start(), "claude warm-up failed"
        r = await b.turn("hello selftest")
        assert r.stop_reason == "result", r
        assert "hello selftest" in r.text, r.text
        await b.stop()

    asyncio.run(_run())
    return "start/turn ok"


class _FakeBrain:
    def __init__(self):
        self.turns: list = []
        self.ready = True
        self.failed = False

    async def stop(self):
        pass

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        from brain import TurnResult

        self.turns.append((text, origin))
        out = f"Echo: {text}."
        if on_delta:
            on_delta(out[:5])
            on_delta(out[5:])
        return TurnResult(origin=origin, text=out, stop_reason="result",
                          context_tokens=1234, output_tokens=5,
                          duration_sec=0.01, first_delta_sec=0.001)


def _server_client():
    import data_paths
    importlib.reload(data_paths)
    import run_store
    importlib.reload(run_store)
    import server
    importlib.reload(server)
    run_store.init_db()

    async def fake_synth(text):
        return b"MP3:" + text.encode()

    server._synth_for_speech = fake_synth
    server._last_greeting_time = time.time()
    from fastapi.testclient import TestClient

    client = TestClient(server.app, headers={"Origin": "http://localhost:5173"})
    return client, server


def stage_voice_ws():
    client, server = _server_client()
    with client as c:
        server.brain_instance = _FakeBrain()
        with c.websocket_connect("/ws/voice") as ws:
            assert ws.receive_json()["type"] == "config"
            assert ws.receive_json() == {"type": "status", "state": "idle"}
            ws.send_json({"type": "transcript", "text": "hello selftest", "isFinal": True})
            audio = None
            for _ in range(10):
                msg = ws.receive_json()
                if msg.get("type") == "audio":
                    audio = msg
                    break
            assert audio, "no audio frame for the transcript"
            assert audio["text"] == "Echo: hello selftest.", audio
            ws.send_json({"type": "played", "utt": audio["utt"], "idx": audio["idx"]})
            for _ in range(10):
                msg = ws.receive_json()
                if msg == {"type": "status", "state": "idle"}:
                    break
            else:
                raise AssertionError("never returned to idle after ack")
    return "transcript → audio → ack → idle"


def stage_runs():
    import run_store
    import run_executor

    fixture = ROOT / "tests" / "fixtures" / "stream_success.jsonl"
    script = TMP / "fake_claude.py"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"sys.stdout.write(open({str(fixture)!r}).read())\n"
        "sys.stdout.flush()\nsys.exit(0)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    async def _run():
        ex = run_executor.RunExecutor(run_store, claude_path=f"{sys.executable} {script}")
        run_id = await ex.spawn("selftest run", "proj", str(TMP), "selftest")
        run = await asyncio.wait_for(ex.wait_for(run_id, timeout=60), timeout=70)
        assert run["status"] in run_store.RunStatus.TERMINAL, run["status"]
        return run["status"]

    return f"run reached terminal state ({asyncio.run(_run())})"


def stage_http():
    client, _ = _server_client()
    with client as c:
        r = c.get("/api/settings/status")
        assert r.status_code == 200, r.status_code
        body = r.json()
        assert "env_keys_set" in body, body
        assert body["env_keys_set"].get("edge_tts") is True
        r = c.get("/api/health")
        assert r.status_code == 200, r.status_code
    return "settings/status + health 200, edge_tts true"


def main() -> int:
    print(f"selftest root={ROOT} datadir={TMP}")
    check("imports", stage_imports)
    check("preflight", stage_preflight)
    check("tts-offline", stage_tts_offline)
    check("brain-hermes", stage_brain_hermes)
    check("brain-claude", stage_brain_claude)
    check("voice-ws", stage_voice_ws)
    check("runs", stage_runs)
    check("http", stage_http)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} stages passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
