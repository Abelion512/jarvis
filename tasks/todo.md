# TODO — JARVIS Build (2026-10-15)

## Task 1: forget_correction + list_corrections tools

**Description:** Undo for bad teachings. Forgotten pair stops rewriting; gate decision recorded.

**Acceptance criteria:**
- [ ] Forgotten pair stops rewriting future transcripts
- [ ] MEMORY_WRITERS gate decision recorded for both tools
- [ ] test_memory_writers.py ARGS_FOR extended to cover both

**Verification:**
- [ ] Tests pass: `.venv/bin/python -m pytest tests/test_memory_writers.py -q`
- [ ] Manual check: teach pair, forget it, verify no rewrite

**Dependencies:** None

**Files likely touched:**
- `server.py`
- `jarvis_mcp.py`
- `tests/test_memory_writers.py`

**Estimated scope:** Small: 2-3 files

## Task 2: Missing-tag turns default to preferred_lang

**Description:** Tagless transcript in an id-major profile speaks id, not en.

**Acceptance criteria:**
- [ ] Tagless transcript in id-major profile speaks id
- [ ] Explicit tags still win over preferred_lang
- [ ] No exception on missing/malformed lang

**Verification:**
- [ ] Tests pass: `.venv/bin/python -m pytest tests/test_voice_ws.py -q`
- [ ] Manual check: id-major profile + tagless transcript → id voice

**Dependencies:** None

**Files likely touched:**
- `server.py`

**Estimated scope:** XS: 1 file

## Task 3: Preflight Edge-TTS reachability probe (warn-only)

**Description:** Offline box warns at boot instead of failing first utterance.

**Acceptance criteria:**
- [ ] Offline box warns at boot instead of failing first utterance
- [ ] Probe never fails boot (warn-only, 3s budget)
- [ ] spoken_summary names Edge-TTS reachability

**Verification:**
- [ ] Tests pass: `.venv/bin/python -m pytest tests/test_preflight.py -q`
- [ ] Manual check: offline → warn at boot, online → ok

**Dependencies:** None

**Files likely touched:**
- `preflight.py`
- `tests/test_preflight.py`

**Estimated scope:** Small: 1-2 files

## Task 4: Hit-rate pruning for dead correction pairs

**Description:** Zero-hit pairs older than N turns retire themselves.

**Acceptance criteria:**
- [ ] Zero-hit pairs older than N turns retire themselves
- [ ] Hit pairs survive pruning
- [ ] Tested (unit test for prune function)

**Verification:**
- [ ] Tests pass: `.venv/bin/python -m pytest tests/test_voice_ws.py tests/test_memory_writers.py -q`

**Dependencies:** None (counters already ship)

**Files likely touched:**
- `server.py`

**Estimated scope:** Small: 1-2 files

## Checkpoint: After Tasks 1-4
- [ ] All focused tests pass
- [ ] No new dependencies added
- [ ] Review before voice path

## Task 5: Frontend PCM audio path for Live session

**Description:** Capture + playback for Gemini Live session.

**Acceptance criteria:**
- [ ] `tsc` clean
- [ ] Manual call test (capture flows, playback plays)
- [ ] No change to WebSpeech default path

**Verification:**
- [ ] Build succeeds: `frontend: bun run build` or `npx tsc --noEmit`
- [ ] Manual check: call test via browser automation

**Dependencies:** None (parallelizable with 1-4, but ordered in TODO)

**Files likely touched:**
- `frontend/src/voice.ts`
- `frontend/src/main.ts`

**Estimated scope:** Medium: 2-3 files

## Task 6: Gemini STT wiring behind JARVIS_STT_PROVIDER

**Description:** PCM streaming frontend → gemini_live.py session → transcript into existing turn pipeline. Default stays webspeech.

**Acceptance criteria:**
- [ ] Flag flip changes nothing when unset
- [ ] Selftest-style round trip green (fake transport)
- [ ] Real transport raises clear error without key/SDK

**Verification:**
- [ ] Tests pass: `.venv/bin/python -m pytest tests/test_gemini_live.py tests/test_voice_ws.py -q`

**Dependencies:** Task 5 (PCM path)

**Files likely touched:**
- `server.py`
- `gemini_live.py`
- `tests/test_gemini_live.py`

**Estimated scope:** Medium: 2-3 files

## Task 7: Gemini transcribe benchmark script

**Description:** Accuracy benchmark on HP voice memo. Blocked on memo file — ship script + instructions.

**Acceptance criteria:**
- [ ] Script exists, runs without memo with clear error
- [ ] With memo: id transcript ≥ WebSpeech quality → go/no-go recorded
- [ ] Else stop (no wiring if benchmark fails)

**Verification:**
- [ ] Manual check: `scripts/gemini_transcribe_bench.py --help`

**Dependencies:** Blocked on human memo file

**Files likely touched:**
- `scripts/gemini_transcribe_bench.py`

**Estimated scope:** XS: 1 file

## Checkpoint: After Tasks 5-7
- [ ] tsc clean
- [ ] gemini_live tests green
- [ ] Flag-off behavior unchanged

## Task 8: Faster-model decision + Hermes plugin trim

**Description:** Human call, then one-line change + note. 69 discovered plugins, ~10s/turn.

**Acceptance criteria:**
- [ ] Decision recorded (which model, which plugins trimmed)
- [ ] One-line change + note if human decides
- [ ] Else documented as awaiting human call

**Verification:**
- [ ] Manual check: config shows decision

**Dependencies:** Human call

**Files likely touched:**
- `.env.example` / docs note

**Estimated scope:** XS: 1 file

## Task 9: Full suite green on final tree

**Description:** Only the 3 known pre-existing HEAD failures remain.

**Acceptance criteria:**
- [ ] Full suite run recorded
- [ ] Only 3 known pre-existing HEAD failures remain
- [ ] New failures triaged (fix or record)

**Verification:**
- [ ] Tests pass: `.venv/bin/python -m pytest -q`

**Dependencies:** Tasks 1-8

**Files likely touched:**
- (triage only)

**Estimated scope:** XS: verification only

## Checkpoint: Complete
- [ ] All acceptance criteria met
- [ ] Code review done
- [ ] Committed
