#!/usr/bin/env python3
"""Gemini transcribe accuracy benchmark (TODO Task 7).

Usage:
  .venv/bin/python scripts/gemini_transcribe_bench.py <memo.m4a|mp3|wav> [--webspeech "..."]

Without a memo file: prints instructions and exits 2 (blocked on human).
With a memo: uploads to Gemini, asks for verbatim Indonesian transcript,
prints it, and records go/no-go vs WebSpeech quality (human verdict).

Go criterion (TODO): id transcript >= WebSpeech quality, else stop wiring.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    print(__doc__)
    print("BLOCKED: need HP voice memo file (Indonesian, ~10s). No mic needed.")
    sys.exit(2 if len(sys.argv) < 2 else 0)

memo = Path(sys.argv[1])
if not memo.is_file():
    print(f"memo not found: {memo}")
    sys.exit(2)

api_key = os.getenv("GEMINI_API_KEY", "")
if not api_key:
    print("GEMINI_API_KEY is not set in .env")
    sys.exit(1)

try:
    from google import genai
except ImportError:
    print("google-genai is not installed (pip install google-genai)")
    sys.exit(1)

client = genai.Client(api_key=api_key)
print(f"uploading {memo} ...")
f = client.files.upload(file=str(memo))
print("transcribing (verbatim Indonesian) ...")
resp = client.models.generate_content(
    model=os.getenv("JARVIS_GEMINI_MODEL", "gemini-2.5-flash-native-audio-latest"),
    contents=[f, "Transcribe this audio verbatim in Indonesian. Output transcript only."],
)
print("--- gemini transcript ---")
print((getattr(resp, "text", "") or "").strip())
print("--- end ---")
print("Compare against WebSpeech `mic-hears:` line for the same memo.")
print("Verdict: go (≥ WebSpeech) → wire STT; no-go → stop.")
