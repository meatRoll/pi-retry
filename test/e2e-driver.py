#!/usr/bin/env python3
"""Drive test/e2e.ts: start pi in RPC mode, send one prompt, wait for the retried run."""
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKDIR = pathlib.Path(os.environ.get("PI_RETRY_WORKDIR", "/tmp/pi-retry-test"))
E2E_LOG = pathlib.Path("/tmp/pi-retry-e2e.log")
CONTEXT_LOG = pathlib.Path("/tmp/pi-retry-context.jsonl")

for path in (E2E_LOG, CONTEXT_LOG):
    path.unlink(missing_ok=True)

WORKDIR.mkdir(parents=True, exist_ok=True)

env = dict(os.environ)
env["PI_RETRY_E2E_LOG"] = str(E2E_LOG)
env["PI_RETRY_LOG"] = str(CONTEXT_LOG)
env["PI_RETRY_PAYLOAD_LOG"] = "/tmp/pi-retry-payload.jsonl"

cmd = [
    "pi",
    "--mode",
    "rpc",
    "--no-session",
    "-e",
    str(ROOT / "test" / "e2e.ts"),
    "-e",
    str(ROOT / "test" / "log-context.ts"),
    "-e",
    str(ROOT / "test" / "log-payload.ts"),
]
proc = subprocess.Popen(
    cmd,
    cwd=WORKDIR,
    env=env,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

lines: list[str] = []


def drain() -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)


threading.Thread(target=drain, daemon=True).start()

prompt = "ESSAY Write a 900 word essay about the history of cats, one paragraph per line, slowly."
proc.stdin.write(json.dumps({"id": "p1", "type": "prompt", "message": prompt}) + "\n")
proc.stdin.flush()

deadline = time.time() + 180
seen_done = False
while time.time() < deadline:
    if proc.poll() is not None:
        print("pi exited early", proc.returncode)
        break
    if "retried run settled" in E2E_LOG.read_text() if E2E_LOG.exists() else False:
        seen_done = True
        break
    time.sleep(0.5)

proc.kill()
proc.wait(timeout=10)
time.sleep(0.2)
def summarize(line: str) -> str:
    try:
        event = json.loads(line)
    except Exception:
        return line[:200]
    kind = event.get("type")
    if kind == "extension_error":
        return f"extension_error {event.get('event')}: {event.get('error')}"
    if kind == "agent_settled":
        return f"agent_settled aborted={event.get('aborted')}"
    if kind == "response" and not event.get("success"):
        return f"response error {event.get('error')}"
    return ""


tail = "\n".join(filter(None, (summarize(line) for line in lines)))

print("=== e2e log ===")
print(E2E_LOG.read_text() if E2E_LOG.exists() else "(missing)")
print("=== provider payloads ===")
payload_log = pathlib.Path("/tmp/pi-retry-payload.jsonl")
leaked = False
if payload_log.exists():
    for line in payload_log.read_text().splitlines():
        payload = json.loads(line)["payload"]
        messages = payload.get("messages", [])
        flat = json.dumps(messages)
        trigger = "pi-retry/trigger" in flat
        leaked = leaked or trigger
        print(f" {json.loads(line)['request']}: {[m.get('role') for m in messages]} trigger={trigger}")
else:
    print(" (missing)")

print("=== settled events ===")
print(tail or "(none)")
print("RESULT:", "PASS" if seen_done and not leaked else "FAIL")
sys.exit(0 if seen_done and not leaked else 1)
