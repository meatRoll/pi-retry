#!/usr/bin/env python3
"""Deterministic /retry test on a seeded session: no real abort needed.

    PI_RETRY_FIXTURE=aborted  (default) -> transcript ends in an interrupted turn
    PI_RETRY_FIXTURE=stop               -> transcript ends in a completed answer
    PI_RETRY_BASELINE=1                 -> send "continue" instead of /retry (control group)

The assertion is always about the real provider payload, i.e. what the model receives.
"""
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKDIR = pathlib.Path("/tmp/pi-retry-test")
WORKDIR.mkdir(parents=True, exist_ok=True)
FIXTURE_STOP = os.environ.get("PI_RETRY_FIXTURE", "aborted")
BASELINE = os.environ.get("PI_RETRY_BASELINE") == "1"

fixture = WORKDIR / f"fixture-{FIXTURE_STOP}.jsonl"
subprocess.run(
    [sys.executable, str(ROOT / "test" / "make-fixture.py"), str(fixture), str(WORKDIR), FIXTURE_STOP],
    check=True,
    capture_output=True,
)

CONTEXT_LOG = pathlib.Path("/tmp/pi-retry-context.jsonl")
PAYLOAD_LOG = pathlib.Path("/tmp/pi-retry-payload.jsonl")
PROJECTION_LOG = pathlib.Path("/tmp/pi-retry-projection.jsonl")
for path in (CONTEXT_LOG, PAYLOAD_LOG, PROJECTION_LOG):
    path.unlink(missing_ok=True)

env = dict(os.environ, PI_RETRY_LOG=str(CONTEXT_LOG), PI_RETRY_PAYLOAD_LOG=str(PAYLOAD_LOG))
proc = subprocess.Popen(
    [
        "pi",
        "--mode",
        "rpc",
        "--session",
        str(fixture),
        "-e",
        str(ROOT / "index.ts"),
        "-e",
        str(ROOT / "test" / "log-context.ts"),
        "-e",
        str(ROOT / "test" / "log-payload.ts"),
    ],
    cwd=WORKDIR,
    env=env,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

events: list[dict] = []
settled = threading.Semaphore(0)


def drain() -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except Exception:
            continue
        events.append(event)
        if event.get("type") == "agent_settled":
            settled.release()


threading.Thread(target=drain, daemon=True).start()

prompt = "continue" if BASELINE else "/retry"
assert proc.stdin is not None
proc.stdin.write(json.dumps({"id": "p1", "type": "prompt", "message": prompt}) + "\n")
proc.stdin.flush()

if not settled.acquire(timeout=180):
    print("TIMEOUT waiting for the run")
    proc.kill()
    sys.exit(1)

time.sleep(1)
proc.kill()
proc.wait(timeout=10)
time.sleep(0.3)

errors = [f"{e.get('event')}: {e.get('error')}" for e in events if e.get("type") == "extension_error"]

print(f"=== {'baseline (typed \"continue\")' if BASELINE else '/retry'} on a {FIXTURE_STOP} transcript ===")
if errors:
    print("errors:", errors)

requests = [json.loads(line)["payload"] for line in PAYLOAD_LOG.read_text().splitlines()]
first = requests[0]
messages = first.get("messages", [])
flat = json.dumps(messages)
roles = [message.get("role") for message in messages]

print(" first request roles:", roles)
print(" contains the old answer (PARTIAL_MARKER):", "PARTIAL_MARKER" in flat)
print(" contains the hidden trigger:", "pi-retry/trigger" in flat)
print(" user messages:", sum(1 for role in roles if role == "user"))

projection = json.loads(PROJECTION_LOG.read_text().splitlines()[-1])["messages"]
print(
    " projection after the run:",
    f"triggers={sum(1 for m in projection if (m.get('customType') or '') == 'pi-retry/trigger')}",
    f"partial={sum(1 for m in projection if 'PARTIAL_MARKER' in (m.get('text') or ''))}",
)

# What the model must see, per scenario. Typing "continue" necessarily adds a user message;
# /retry must not add any.
expected_old_answer = BASELINE and FIXTURE_STOP == "stop"
expected_user_messages = 2 if BASELINE else 1
ok = (
    not errors
    and ("PARTIAL_MARKER" in flat) == expected_old_answer
    and "pi-retry/trigger" not in flat
    and sum(1 for role in roles if role == "user") == expected_user_messages
)
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
