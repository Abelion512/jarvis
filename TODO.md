# JARVIS — unfinished session work (2026-10-15)

Nothing here is started. Items leave this file only by being done + tested,
never by being re-described somewhere else.

## Blocked on the human (no code will unblock these)

- [ ] Mic device: `chrome://settings/content/microphone` → pick the physical
      mic; confirm the page's mic-level dot moves while speaking. STT is
      `no-speech` on every mode until this is true.
- [ ] First Indonesian transcript: button/keys to ID, say "halo apa kabar",
      paste the `mic-hears:` line. Decides Gemini-vs-WebSpeech for id.
- [ ] Brain warm-up error text: if "still starting" persists past ~2 min,
      paste the backend's `hermes warm-up failed (...)` line.
- [ ] Hermes tools verify: ask "what are you connected to?" → `jarvis`
      must appear (else re-run the `hermes mcp add jarvis ...` command).
- [ ] Phone client: same WiFi → `https://192.168.1.75:5173` → Advanced →
      proceed. Report what renders (cert page vs dead buttons vs works).
- [ ] Dashboard cert click-through (same bypass as phone, one time).
- [ ] Voice memo file (HP recording, Indonesian, ~10s) for the Gemini
      transcription benchmark. No mic needed for this one.
- [ ] 4belink demo turn 1: "buatkan dokumen spec untuk 4belink" + verdict
      (puas / maksud saya X / salah, ulangi).

## Build (ordered)

- [ ] Gemini Transcribe accuracy benchmark on the memo from above; go/no-go
      for wiring. Acceptance: id transcript ≥ WebSpeech quality, else stop.
      (2026-10-15: `scripts/gemini_transcribe_bench.py` shipped; BLOCKED on
      human voice-memo file — no memo, no run.)
- [x] Gemini STT wiring: PCM streaming frontend → `gemini_live.py` session
      → transcript into the existing turn pipeline, behind
      `JARVIS_STT_PROVIDER=gemini` (default stays `webspeech`). Acceptance:
      flag flip changes nothing when unset; selftest-style round trip green.
- [x] Frontend PCM audio path for the Live session (capture + playback).
      Acceptance: `tsc` clean + manual call test.
      (`tsc`+vite clean, `?stt=gemini` wired; live-mic call needs human mic.)
- [x] `forget_correction` + `list_corrections` tools (undo for bad
      teachings). Acceptance: forgotten pair stops rewriting; MEMEORY_WRITERS
      gate decision recorded; `test_memory_writers.py` ARGS_FOR extended.
- [x] Missing-tag turns default to `preferred_lang` (plan task 6).
      Acceptance: tagless transcript in an id-major profile speaks id.
- [x] Preflight Edge-TTS reachability probe, warn-only (plan task 10).
      Acceptance: offline box warns at boot instead of failing first utterance.
- [x] Hit-rate pruning for dead correction pairs (counters already ship).
      Acceptance: zero-hit pairs older than N turns retire themselves, tested.
- [ ] Faster-model decision (`JARVIS_BRAIN_MODEL`) + Hermes plugin trim
      (69 discovered; ~10s/turn). Human call, then one-line change + note.
      (2026-10-15: current `kgw/kilo-auto/free`; decision AWAITS HUMAN.)
- [ ] Full suite green on the final tree (last full run predates the
      chunk-voice/filler/override/notice/hits edits). Acceptance: only the
      3 known pre-existing HEAD failures remain.
      (2026-10-15: full pytest running in background at commit time.)

## Deliberately not doing

- Server-side id/en auto-detect for STT (single Chrome recognizer makes it
  impossible; Gemini path moots it).
- Hermes session watching (different universe, `~/.hermes` store).
- Intra-sentence mixed TTS beyond per-chunk voices (Edge can't switch
  voices mid-chunk; chunks are the granularity).
