#!/usr/bin/env python3
"""Drive the real /retry command over RPC: prompt, abort, then send "/retry"."""
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKDIR = pathlib.Path(os.environ.get("PI_RETRY_WORKDIR", "/tmp/pi-retry-test"))
CONTEXT_LOG = pathlib.Path("/tmp/pi-retry-context.jsonl")
CONTEXT_LOG.unlink(missing_ok=True)

WORKDIR.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, PI_RETRY_LOG=str(CONTEXT_LOG))

proc = subprocess.Popen(
    [
        "pi",
        "--mode",
        "rpc",
        "--no-session",
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
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        events.append(event)
        if event.get("type") == "agent_settled":
            settled.release()


threading.Thread(target=drain, daemon=True).start()


def send(payload: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def wait_settled(timeout: float, label: str) -> dict:
    if not settled.acquire(timeout=timeout):
        print(f"TIMEOUT waiting for {label}")
        proc.kill()
        sys.exit(1)
    for event in reversed(events):
        if event.get("type") == "agent_settled":
            return event
    return {}


scenario = os.environ.get("PI_RETRY_SCENARIO", "abort")

send({"id": "p1", "type": "prompt", "message": "ESSAY Write 900 words about the history of cats, slowly."})
abort_after = float(os.environ.get("PI_RETRY_ABORT_AFTER", "6"))

if scenario == "abort":
    time.sleep(abort_after)
    send({"id": "a1", "type": "abort"})
    first = wait_settled(120, "aborted run")
    print(f"first settle: aborted={first.get('aborted')}")
elif scenario == "busy":
    time.sleep(2)
    send({"id": "p2", "type": "prompt", "message": "/retry"})
    time.sleep(2)
    send({"id": "a1", "type": "abort"})
    first = wait_settled(120, "aborted run")
    print(f"first settle: aborted={first.get('aborted')}")
    second = {"aborted": True}
else:  # regen: let the first answer finish normally
    first = wait_settled(180, "first run")
    print(f"first settle: aborted={first.get('aborted')}")

payload_path = pathlib.Path("/tmp/pi-retry-payload.jsonl")


def payload_count() -> int:
    if not payload_path.exists():
        return 0
    return len([line for line in payload_path.read_text().splitlines() if line.strip()])


interrupted_count = payload_count()

if scenario != "busy":
    time.sleep(1)
    send({"id": "p3", "type": "prompt", "message": "/retry"})
    second = wait_settled(180, "retried run")
    print(f"second settle: aborted={second.get('aborted')}")

time.sleep(1)
proc.kill()
proc.wait(timeout=10)
time.sleep(0.3)

errors = [
    f"{e.get('event')}: {e.get('error')}" for e in events if e.get("type") in ("extension_error",)
]
notifications = [
    e.get("message") for e in events if e.get("type") == "extension_ui_request" and e.get("method") == "notify"
]

print("=== notifications ===")
for note in notifications:
    print(" ", note)
print("=== extension errors ===")
print("\n".join(errors) if errors else " (none)")
print("=== provider requests ===")
for line in CONTEXT_LOG.read_text().splitlines():
    data = json.loads(line)
    msgs = data["messages"]
    aborted_turn = any(m.get("stopReason") == "aborted" for m in msgs)
    trigger = any((m.get("customType") or "") == "pi-retry/trigger" for m in msgs)
    print(
        f" request {data['request']}: {len(msgs)} msgs, aborted_turn={aborted_turn}, trigger={trigger}, "
        f"last={msgs[-1]['role']}/{msgs[-1].get('stopReason') or '-'}"
    )

joined = " ".join(n for n in notifications if n)
if scenario == "busy":
    ok = not errors and "still streaming" in joined
else:
    print("=== provider payload (last interrupted request vs first retried request) ===")
if payload_path.exists():
    payloads = [json.loads(line)["payload"] for line in payload_path.read_text().splitlines()]
else:
    payloads = []
same_shape = len(payloads) > interrupted_count
if same_shape:
    before = payloads[interrupted_count - 1]["messages"]
    after = payloads[interrupted_count]["messages"]
    print(f" interrupted run sent {interrupted_count} request(s), {len(payloads) - interrupted_count} after retry")
    print(" before:", [m.get("role") for m in before])
    print(" after :", [m.get("role") for m in after])
    if scenario == "regen" and before and before[-1].get("role") == "assistant":
        expected = before[:-1]
    else:
        expected = before
    identical = after == expected
    print(" retried request == expected context:", identical)
else:
    identical = False
    print(f" no retried payload (interrupted_count={interrupted_count}, total={len(payloads)})")

print("=== projection after each settle (what compaction sees) ===")
proj_path = pathlib.Path("/tmp/pi-retry-projection.jsonl")
if proj_path.exists():
    for line in proj_path.read_text().splitlines():
        data = json.loads(line)
        msgs = data["messages"]
        print(
            f" settle: {len(msgs)} msgs, "
            f"triggers={sum(1 for m in msgs if (m.get('customType') or '') == 'pi-retry/trigger')}, "
            f"interrupted={sum(1 for m in msgs if m.get('stopReason') in ('aborted', 'error'))}"
        )

joined = " ".join(n for n in notifications if n)
if scenario == "busy":
    ok = not errors and "still streaming" in joined
else:
    ok = second.get("aborted") is False and not errors and "re-sending" in joined and identical
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
