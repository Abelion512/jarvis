#!/usr/bin/env python3
"""A stand-in `hermes` for tests: one turn per process, Hermes-shaped JSONL.

Invoked by Brain as `hermes chat --format stream-json -m <model>
--query-file - [--resume <sid>]`. The prompt arrives on stdin as RAW TEXT
(not the `{"type": "user", ...}` lines Claude Code uses); argv is otherwise
ignored. Emits the two events the real CLI was observed to emit:

    {"type": "system", "subtype": "init", "model": ..., "session_id": ...}
    {"type": "result", "session_id": ..., "exit_code": 0, "text": ...,
     "tokens": {"input": ..., "output": ..., "total": ...}}

A `--resume <sid>` turn answers under that same session id; otherwise a
fresh `fake-hermes-<n>` id is minted. A prompt containing HERMESERROR ends
with exit_code 1 and an error string instead of text.
"""
import json
import os
import re
import sys
import time

SESSION_SEQ = 0


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    args = sys.argv[1:]
    resumed = None
    if "--resume" in args:
        resumed = args[args.index("--resume") + 1]
    model = args[args.index("-m") + 1] if "-m" in args else "fake-model"
    text = sys.stdin.read().strip()
    dump_to = os.environ.get("FAKE_HERMES_DUMP_STDIN")
    if dump_to:
        with open(dump_to, "w") as fh:
            fh.write(text)
    if resumed:
        session_id = resumed
    else:
        global SESSION_SEQ
        SESSION_SEQ += 1
        session_id = f"fake-hermes-{SESSION_SEQ:04d}"
    emit({"type": "system", "subtype": "init", "model": model,
          "session_id": session_id})
    slow = re.search(r"SLOW:(\d+(?:\.\d+)?)", text)
    if slow:
        time.sleep(float(slow.group(1)))
    if "HERMESERROR" in text:
        emit({"type": "result", "session_id": session_id, "exit_code": 1,
              "text": "", "tokens": {"input": 0, "output": 0, "total": 0},
              "error": "HTTP 401: Invalid API key"})
        return
    out = f"Echo: {text}"
    # Streamed fragments first (the real shape), then the full result.
    cut = max(1, len(out) // 2)
    for piece in (out[:cut], out[cut:]):
        emit({"type": "text", "text": piece})
    if "HTOOL" in text:
        # A hermes-native tool call in the real tool_use shape.
        emit({"type": "tool_use", "name": "WebFetch",
              "input": {"url": "https://example.com/"}})
    emit({"type": "result", "session_id": session_id, "exit_code": 0,
          "text": out,
          "tokens": {"input": 12, "output": len(out) // 4,
                     "total": 12 + len(out) // 4}})


main()
