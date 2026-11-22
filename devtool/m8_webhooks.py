#!/usr/bin/env python3
"""M8 source-pinned, signed GitHub webhook inbox. Events are wakeups, never authority."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import time
import sys
from typing import Any, Mapping
from uuid import UUID

MAX_BODY = 262144
EVENTS = {"pull_request": {"opened", "reopened", "synchronize", "ready_for_review", "closed"},
          "workflow_run": {"requested", "in_progress", "completed"},
          "check_run": {"created", "completed", "rerequested"}}
class Refusal(ValueError): pass
def refuse(code: str) -> None: raise Refusal(code)

def secret_from(path: Path) -> bytes:
    if path.is_symlink(): refuse("M8_SECRET_SYMLINK")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077 or s.st_size > 4096:
            refuse("M8_SECRET_PERMISSIONS")
        key = os.read(fd, 4097).rstrip(b"\r\n")
    finally: os.close(fd)
    if len(key) < 32: refuse("M8_SECRET_TOO_SHORT")
    return key

def authenticate(headers: Mapping[str, str], body: bytes, secret: bytes) -> tuple[str, str]:
    if type(body) is not bytes or not 1 <= len(body) <= MAX_BODY: refuse("M8_BODY_SIZE")
    if len(secret) < 32: refuse("M8_SECRET_TOO_SHORT")
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    signature = h.get("x-hub-signature-256", "")
    if not re.fullmatch(r"sha256=[0-9a-fA-F]{64}", signature): refuse("M8_SIGNATURE_FORMAT")
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.lower(), expected): refuse("M8_SIGNATURE_MISMATCH")
    try: delivery = str(UUID(h.get("x-github-delivery", "")))
    except (ValueError, AttributeError): refuse("M8_DELIVERY_UUID")
    if h.get("x-github-delivery", "").lower() != delivery: refuse("M8_DELIVERY_NONCANONICAL")
    event = h.get("x-github-event", "")
    if event not in EVENTS: refuse("M8_EVENT_NOT_ALLOWED")
    return delivery, event

@dataclass(frozen=True)
class Pin:
    repository: str
    repository_id: int
    installation_id: int
    head_sha: str
    pr_number: int
    session_id: str
    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}", self.repository): refuse("M8_REPO_PIN")
        if not all(type(n) is int and n > 0 for n in (self.repository_id, self.installation_id, self.pr_number)):
            refuse("M8_ID_PIN")
        if not re.fullmatch(r"[0-9a-f]{40}", self.head_sha): refuse("M8_SHA_PIN")
        if not re.fullmatch(r"[0-9a-f]{32}", self.session_id): refuse("M8_SESSION_PIN")

def signal(event: str, body: bytes, pin: Pin) -> dict[str, Any]:
    try: payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError): refuse("M8_JSON_INVALID")
    if not isinstance(payload, dict): refuse("M8_ROOT_INVALID")
    repo, installation = payload.get("repository"), payload.get("installation")
    if not isinstance(repo, dict) or repo.get("full_name") != pin.repository or repo.get("id") != pin.repository_id:
        refuse("M8_REPOSITORY_DRIFT")
    if not isinstance(installation, dict) or installation.get("id") != pin.installation_id:
        refuse("M8_INSTALLATION_DRIFT")
    action = payload.get("action")
    if action not in EVENTS[event]: refuse("M8_ACTION_NOT_ALLOWED")
    result = {"kind": event, "action": action, "repository": pin.repository,
              "head_sha": pin.head_sha, "pr": pin.pr_number, "authority": "wakeup-only"}
    if event == "pull_request":
        pr = payload.get("pull_request")
        if not isinstance(pr, dict) or payload.get("number") != pin.pr_number: refuse("M8_PR_IDENTITY_DRIFT")
        h, b = pr.get("head"), pr.get("base")
        if not isinstance(h, dict) or not isinstance(b, dict): refuse("M8_PR_REFS_INVALID")
        if h.get("sha") != pin.head_sha: refuse("M8_PR_HEAD_DRIFT")
        if not isinstance(h.get("repo"), dict) or h["repo"].get("full_name") != pin.repository:
            refuse("M8_FORK_REQUIRES_SEPARATE_POLICY")
        if not isinstance(b.get("repo"), dict) or b["repo"].get("full_name") != pin.repository:
            refuse("M8_BASE_REPO_DRIFT")
        result["merged_signal"] = action == "closed" and pr.get("merged") is True
    elif event == "workflow_run":
        run = payload.get("workflow_run")
        if not isinstance(run, dict) or run.get("head_sha") != pin.head_sha: refuse("M8_RUN_SOURCE_DRIFT")
        if any(type(run.get(k)) is not int or run[k] <= 0 for k in ("id", "run_attempt")):
            refuse("M8_RUN_IDENTITY_INVALID")
        result["run_id"], result["attempt"] = run["id"], run["run_attempt"]
        result["conclusion_signal"] = run.get("conclusion") if action == "completed" and run.get("conclusion") in {"success", "failure", "cancelled", "timed_out", "skipped"} else "unknown"
    else:
        check = payload.get("check_run")
        if not isinstance(check, dict) or check.get("head_sha") != pin.head_sha: refuse("M8_CHECK_SOURCE_DRIFT")
        if type(check.get("id")) is not int or check["id"] <= 0: refuse("M8_CHECK_ID_INVALID")
        prs = check.get("pull_requests")
        if not isinstance(prs, list) or not any(isinstance(p, dict) and p.get("number") == pin.pr_number for p in prs):
            refuse("M8_CHECK_PR_UNVERIFIED")
        result["check_run_id"] = check["id"]
    return result

def root_at(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.is_symlink(): refuse("M8_STATE_SYMLINK")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077: refuse("M8_STATE_PERMISSIONS")
    events = path / "events"
    if events.is_symlink(): refuse("M8_EVENTS_SYMLINK")
    events.mkdir(mode=0o700, exist_ok=True)
    if events.stat().st_mode & 0o077: refuse("M8_EVENTS_PERMISSIONS")
    return path

@contextmanager
def locked(root: Path):
    fd = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077: refuse("M8_LOCK_PERMISSIONS")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

def record(path: Path, value: dict[str, Any]):
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > 8192: refuse("M8_RECORD_SIZE")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())

def read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077 or path.stat().st_size > 8192:
        refuse("M8_RECORD_UNSAFE")
    try: obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError): refuse("M8_RECORD_INVALID")
    if not isinstance(obj, dict): refuse("M8_RECORD_INVALID")
    return obj

def pin(root: Path, value: Pin) -> str:
    root = root_at(root)
    with locked(root):
        path = root / "pin.json"
        if path.exists() or path.is_symlink():
            if read(path) != asdict(value): refuse("M8_PIN_CHANGE_REQUIRES_NEW_INBOX")
            return "EXISTING"
        record(path, asdict(value))
    return "CREATED"

def current_pin(root: Path) -> Pin:
    data = read(root_at(root) / "pin.json")
    if set(data) != set(Pin.__dataclass_fields__): refuse("M8_PIN_SCHEMA")
    return Pin(**data)

def ingest(root: Path, headers: Mapping[str, str], body: bytes, secret: bytes) -> dict[str, Any]:
    delivery, event = authenticate(headers, body, secret)
    root = root_at(root)
    item = signal(event, body, current_pin(root))
    digest = hashlib.sha256(body).hexdigest()
    path = root / "events" / (delivery + ".json")
    with locked(root):
        if path.exists() or path.is_symlink():
            old = read(path)
            if old.get("delivery") != delivery or old.get("sha256") != digest or old.get("signal") != item:
                refuse("M8_DELIVERY_COLLISION")
            return {"status": "DUPLICATE", "delivery": delivery, "signal": item}
        if sum(1 for _ in (root / "events").glob("*.json")) >= 4096: refuse("M8_INBOX_FULL")
        record(path, {"schema": 1, "delivery": delivery, "sha256": digest,
                      "signal": item, "state": "pending"})
    return {"status": "ACCEPTED", "delivery": delivery, "signal": item}

def inbox(root: Path, limit: int = 50) -> dict[str, Any]:
    if not 1 <= limit <= 100: refuse("M8_INBOX_LIMIT")
    root = root_at(root); p = current_pin(root)
    with locked(root):
        rows = [read(x) for x in sorted((root / "events").glob("*.json"), reverse=True)[:limit]]
    return {"schema": "devtool.m8.inbox.v1", "read_only": True,
            "head_sha": p.head_sha, "session_id": p.session_id, "events": rows}

def qualify(root: Path, request_id: str, auth: str = "auto") -> dict[str, Any]:
    """Trust only existing native B3 re-verification, never event conclusions."""
    if not re.fullmatch(r"[0-9a-f]{32}", request_id): refuse("M8_REQUEST_ID_INVALID")
    p = current_pin(root)
    from .delivery_session import status
    session = status(p.session_id)
    if (session.get("head_sha") != p.head_sha or session.get("repository") != p.repository or
            (session.get("pr") or {}).get("number") != p.pr_number or
            session.get("source_binding") != "matching" or session.get("resumable") is not True):
        refuse("M8_NATIVE_SESSION_DRIFT")
    from .delivery_qualification import qualify as native_qualify
    result = native_qualify(p.session_id, request_id=request_id, auth=auth)
    if result.get("head_sha") != p.head_sha: refuse("M8_B3_HEAD_DRIFT")
    return {"schema": "devtool.m8.continuation.v1", "read_only": True,
            "native": result, "pre_merge_pass": result.get("pre_merge_qualification") == "PASSED",
            "webhook_authority": "wakeup-only", "automatic_merge": False}

def maintain_pr(root: Path, *, approved: bool, auth: str = "auto") -> dict[str, Any]:
    """Explicit approval only; delegate identity checks and PR mutation to native Devtool."""
    if approved is not True: refuse("M8_PR_MAINTENANCE_NOT_APPROVED")
    p = current_pin(root)
    from .delivery_session import status
    session = status(p.session_id)
    if (session.get("head_sha") != p.head_sha or session.get("repository") != p.repository or
            (session.get("pr") or {}).get("number") != p.pr_number or
            session.get("source_binding") != "matching" or session.get("resumable") is not True):
        refuse("M8_NATIVE_SESSION_DRIFT")
    from .delivery_github import pr as native_pr
    result = native_pr(p.session_id, yes=True, auth=auth)
    if not isinstance(result, dict): refuse("M8_PR_NATIVE_RECEIPT_INVALID")
    verified = (result.get("pr") or {})
    if isinstance(verified, dict) and verified.get("number") not in (None, p.pr_number):
        refuse("M8_PR_NUMBER_CHANGED_RECONCILE")
    return {"schema": "devtool.m8.pr-maintenance.v1", "native": result,
            "explicit_approval": True, "webhook_authority": "wakeup-only"}

def follow(root: Path, request_id: str, *, auth: str = "auto", observed: set[str] | None = None) -> dict[str, Any]:
    """Event-driven read-only native B3 checks; no webhook-trusted CI verdicts.

    Each delivery UUID is observed at most once per watcher invocation and
    journaled independently. A later GitHub delivery can trigger a fresh
    observation without editing an earlier receipt.
    """
    root = root_at(root)
    rows = inbox(root, limit=100)["events"]
    done = observed if observed is not None else set()
    work = [r for r in rows if r["delivery"] not in done]
    if not work: return {"status": "NO_NEW_EVENTS", "read_only": True, "observations": []}
    if not re.fullmatch(r"[0-9a-f]{32}", request_id): refuse("M8_REQUEST_ID_INVALID")
    summary=[]
    for entry in reversed(work):
        identity=entry["delivery"]
        done.add(identity)
        outcome=qualify(root,request_id,auth)
        native=outcome["native"]
        row={"delivery":identity,"request_id":request_id,
             "head_sha":native["head_sha"],"session_id":native["session_id"],
             "pre_merge_qualification":native.get("pre_merge_qualification"),
             "required_ci":(native.get("required_delivery_ci") or {}).get("state"),
             "regression":(native.get("repository_regression") or {}).get("state"),
             "read_only":True}
        summary.append(row)
        # Journal only a small verdict projection; never raw provider JSON.
        observations=root/"observations"
        if observations.is_symlink():refuse("M8_OBSERVATIONS_SYMLINK")
        observations.mkdir(mode=0o700,exist_ok=True)
        with locked(root):
            path=observations/(identity+".json")
            if path.exists() or path.is_symlink():
                old=read(path)
                if old.get("delivery")!=identity or old.get("request_id")!=request_id:
                    refuse("M8_OBSERVATION_COLLISION")
            else:record(path,row)
    return {"status":"OBSERVED","read_only":True,"observations":summary,
            "automatic_mutation":False}

def serve(root: Path, key: bytes, host: str, port: int):
    if host != "127.0.0.1" or not 1024 <= port <= 65535: refuse("M8_LOOPBACK_ONLY")
    current_pin(root)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/webhook": code, reply = 404, {"ok": False, "error": "M8_UNKNOWN_ROUTE"}
            elif self.headers.get("Transfer-Encoding"):
                code, reply = 400, {"ok": False, "error": "M8_TRANSFER_ENCODING"}
            else:
                length = self.headers.get("Content-Length", "")
                if not length.isdigit() or not 1 <= int(length) <= MAX_BODY:
                    code, reply = 413, {"ok": False, "error": "M8_BODY_SIZE"}; self.close_connection = True
                else:
                    try:
                        result = ingest(root, dict(self.headers.items()), self.rfile.read(int(length)), key)
                        code = 200 if result["status"] == "DUPLICATE" else 202
                        reply = {"ok": True, "status": result["status"], "delivery": result["delivery"]}
                    except Refusal as exc: code, reply = 403, {"ok": False, "error": str(exc)}
            blob = json.dumps(reply).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob))); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(blob)
        def log_message(self, fmt, *args):
            print("M8_HTTP_STATUS=" + str(args[1] if len(args) > 1 else "unknown"), flush=True)
    class Server(ThreadingHTTPServer): daemon_threads = True
    with Server((host, port), Handler) as service:
        print(f"M8_LOOPBACK_LISTEN={host}:{port}; trusted TLS reverse proxy required", flush=True)
        service.serve_forever()

def main(argv=None):
    parser = argparse.ArgumentParser(description="M8 signed GitHub events; native B3 is authoritative")
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "devtool/m8-webhooks")
    sub = parser.add_subparsers(dest="operation", required=True)
    s = sub.add_parser("pin")
    for name in ("repository", "head-sha", "session-id"): s.add_argument("--" + name, required=True)
    for name in ("repository-id", "installation-id", "pr"): s.add_argument("--" + name, required=True, type=int)
    s = sub.add_parser("ingest")
    s.add_argument("--headers-json", type=Path, required=True)
    s.add_argument("--body-file", type=Path, required=True)
    s.add_argument("--secret-file", type=Path, required=True)
    s = sub.add_parser("serve")
    s.add_argument("--secret-file", type=Path, required=True)
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8787)
    s = sub.add_parser("inbox"); s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("qualify"); s.add_argument("--request-id", required=True)
    s.add_argument("--auth", choices=("auto", "app", "gh"), default="auto")
    s = sub.add_parser("follow", help="Observe new signed events through read-only native B3")
    s.add_argument("--request-id",required=True)
    s.add_argument("--auth",choices=("auto","app","gh"),default="auto")
    s.add_argument("--watch-seconds",type=int,default=0,
                   help="Optional bounded event-driven watcher duration (0 = once)")
    s = sub.add_parser("maintain-pr", help="Explicitly approved native PR reconciliation")
    s.add_argument("--yes", action="store_true", help="Explicitly authorize native PR maintenance")
    s.add_argument("--auth", choices=("auto", "app", "gh"), default="auto")
    a = parser.parse_args(argv); os.umask(0o077)
    try:
        if a.operation == "pin":
            result = {"status": pin(a.state_dir, Pin(a.repository, a.repository_id, a.installation_id, a.head_sha, a.pr, a.session_id))}
        elif a.operation == "ingest":
            h = json.loads(a.headers_json.read_text(encoding="utf-8"))
            if not isinstance(h, dict): refuse("M8_HEADER_JSON")
            if a.body_file.stat().st_size > MAX_BODY: refuse("M8_BODY_SIZE")
            result = ingest(a.state_dir, h, a.body_file.read_bytes(), secret_from(a.secret_file))
        elif a.operation == "inbox": result = inbox(a.state_dir, a.limit)
        elif a.operation == "qualify": result = qualify(a.state_dir, a.request_id, a.auth)
        elif a.operation == "maintain-pr": result = maintain_pr(a.state_dir, approved=a.yes, auth=a.auth)
        elif a.operation == "follow":
            if not 0 <= a.watch_seconds <= 7200: refuse("M8_WATCH_BOUNDS")
            seen=set();start=time.monotonic();result={"status":"NO_NEW_EVENTS","read_only":True}
            while True:
                result=follow(a.state_dir,a.request_id,auth=a.auth,observed=seen)
                if result.get("observations"):
                    print(json.dumps(result,sort_keys=True,indent=2),flush=True)
                if a.watch_seconds==0 or time.monotonic()-start>=a.watch_seconds: break
                time.sleep(3)

        else: return serve(a.state_dir, secret_from(a.secret_file), a.host, a.port)
        print(json.dumps(result, sort_keys=True, indent=2), flush=True); return 0
    except (Refusal, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc) if isinstance(exc, Refusal) else "M8_INPUT_ERROR"}), flush=True)
        return 2
if __name__ == "__main__": sys.exit(main())
