#!/usr/bin/env python3
"""Spike: validate GEMINI_API_KEY and discover Live-capable models.

Read-only (models.list only). Loads .env like server.py does.
Usage: .venv/bin/python scripts/gemini_spike.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
print("client ok")
for m in client.models.list():
    name = getattr(m, "name", "?")
    lower = name.lower()
    if any(k in lower for k in ("live", "transcribe", "native-audio", "dialog")):
        print("-", name)
