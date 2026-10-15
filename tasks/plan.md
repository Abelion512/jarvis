# Implementation Plan: JARVIS TODO Build (2026-10-15)

## Overview
Batch of 9 ordered build items from TODO.md. Dirty tree already contains partial work (gemini_live.py lifecycle, learned corrections with hits, Edge-TTS migration, lang tagging). Remaining gaps are small bounded slices. Blocked-on-human items need no code — verified via browser/CDP where possible, otherwise recorded as awaiting human artifact.

## Architecture Decisions
- `forget_correction`/`list_corrections` live in server.py beside `learn_correction`, same MEMORY_WRITERS gate, same profile lock. No new module (ponytail: reuse).
- Missing-tag default reads `preferred_lang` from profile, not hardcoded `en`. One call-site change in WS transcript handler.
- Edge-TTS probe is warn-only, TCP/TLS reachability to `speech.platform.bing.com:443`, 3s budget, never fails boot. Follows `_run_subprocess` pattern (no raise).
- Pruning retires zero-hit pairs older than N turns using existing `hits` counters + `uses`/`turns` tracking. Counters already ship; add `last_used_turn` + `prune_dead_corrections()`.
- Frontend PCM is minimal: AudioWorklet capture 16kHz mono + AudioContext playback, behind `JARVIS_STT_PROVIDER`. `tsc` clean is the gate.
- Gemini wiring is thin: WS binary PCM → `GeminiLiveSession.send_pcm`, transcripts → existing `_handle_utterance`. Flag flip changes nothing when unset.
- Benchmark is blocked (no memo file). Ship `scripts/gemini_transcribe_bench.py` + instructions, mark awaiting human.

## Task List

### Phase 1: Foundation (independent, any order)
- [ ] Task 1: forget_correction + list_corrections
- [ ] Task 2: Missing-tag turns default to preferred_lang
- [ ] Task 3: Preflight Edge-TTS reachability probe (warn-only)
- [ ] Task 4: Hit-rate pruning for dead correction pairs

### Checkpoint: Foundation
- [ ] Focused tests pass for tasks 1-4
- [ ] No new deps

### Phase 2: Voice path (ordered)
- [ ] Task 5: Frontend PCM audio path (capture + playback)
- [ ] Task 6: Gemini STT wiring behind JARVIS_STT_PROVIDER
- [ ] Task 7: Gemini transcribe benchmark script (blocked on memo)

### Checkpoint: Voice path
- [ ] tsc clean, gemini_live tests green
- [ ] Flag flip changes nothing when unset

### Phase 3: Close-out
- [ ] Task 8: Faster-model decision + Hermes plugin trim note
- [ ] Task 9: Full suite green (only 3 known HEAD failures remain)

### Checkpoint: Complete
- [ ] All acceptance criteria met
- [ ] Code review + commit

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| No voice memo file | Benchmark blocked | Ship script, mark blocked, proceed |
| No mic/human for manual tests | Phone/dashboard/cert unverifiable | Verify via CDP/browser automation what is reachable locally |
| Hermes plugin trim needs human call | Cannot autonomously decide | Prepare one-line change + note, leave decision to human |
| Gemini Live transport incomplete | Wiring partial | Keep behind flag, fake transport tests, no default behavior change |

## Open Questions
- Voice memo file (~10s Indonesian HP recording) — awaiting human.
- JARVIS_BRAIN_MODEL faster-model choice — human call.
- Mic device selection — human-only.
