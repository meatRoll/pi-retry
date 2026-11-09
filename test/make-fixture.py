#!/usr/bin/env python3
"""Write a session fixture whose transcript ends in an interrupted assistant turn.

Deterministic alternative to waiting for a real Escape: the retry path then always sees an
aborted turn that already streamed text (which is what `keep` needs).
"""
import json
import pathlib
import sys

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pi-retry-fixture.jsonl")
CWD = sys.argv[2] if len(sys.argv) > 2 else "/tmp/pi-retry-test"
STOP = sys.argv[3] if len(sys.argv) > 3 else "aborted"

usage = {
    "input": 10,
    "output": 5,
    "cacheRead": 0,
    "cacheWrite": 0,
    "totalTokens": 15,
    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
}

entries = [
    {
        "type": "session",
        "version": 3,
        "id": "01a1bdeb-0000-7000-8000-000000000000",
        "timestamp": "2026-11-09T00:00:00.000Z",
        "cwd": CWD,
    },
    {
        "type": "message",
        "id": "e1",
        "parentId": None,
        "timestamp": "2026-11-09T00:00:01.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "Write a 900 word essay about the history of cats."}],
            "timestamp": 1794190000000,
        },
    },
    {
        "type": "message",
        "id": "e2",
        "parentId": "e1",
        "timestamp": "2026-11-09T00:00:02.000Z",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "PARTIAL_MARKER The cat arrived in human history sideways, as a guest at the granary,",
                }
            ],
            "api": "openai-completions",
            "provider": "meatroll",
            "model": "deepseek/deepseek-v4.1-flash",
            "usage": usage,
            "stopReason": STOP,
            **({"errorMessage": "Request aborted"} if STOP == "aborted" else {}),
            "timestamp": 1794190002000,
            "durationMs": 1452,
        },
    },
]

OUT.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")
print(OUT)
