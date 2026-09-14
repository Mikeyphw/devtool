from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from devtool.artifact_validation import run_artifact_validation_script
from devtool.artifacts import write_archive_entries
from devtool.core.events.bus import EventBus
from devtool.core.events.events import BuildStarted
from devtool.core.events.listeners import JsonEventListener, LoggingEventListener, event_to_dict
from devtool.core.results import CommandResult
from devtool.hooks.engine import _python_command
from devtool.hooks.result import load_structured_hook_result
from devtool.ipc import IpcBroker, IpcPublisher, _Peer, _state_is_newer, broker_is_running
from devtool.models import Issue
from devtool.plugins import registry as plugin_registry
from devtool.plugins.api import PLUGIN_API_VERSION, PluginCheck, PluginDescriptor


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive_with_validator(
    path: Path,
    script: bytes,
    *,
    timeout: int = 5,
    protocol: str = "legacy-env",
    write_paths: list[str] | None = None,
    runner: str = "bash",
    python: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    script_spec: dict[str, Any] = {
        "path": ".devtool-artifact/validate.sh",
        "runner": runner,
        "sha256": _sha(script),
        "timeout_seconds": timeout,
        "protocol": protocol,
    }
    if python is not None:
        script_spec["python"] = python
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "type": "overlay",
        "target": "devtool",
        "validation": {
            "required": True,
            "restore": False,
            "build": False,
            "test": False,
            "write_paths": list(write_paths or []),
            "script": script_spec,
        },
    }
    write_archive_entries(path, {".devtool-artifact/validate.sh": script, ".devtool-artifact.json": json.dumps(manifest).encode()})
    return path, manifest


def _socket_peer() -> tuple[_Peer, socket.socket]:
    left, right = socket.socketpair()
    left.settimeout(1.0)
    right.settimeout(1.0)
    return _Peer(left), right


def _read_json(sock: socket.socket) -> dict[str, Any]:
    data = bytearray()
    while b"\n" not in data:
        data.extend(sock.recv(8192))
    return json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))


def test_event_bus_contains_bad_listener_and_continues() -> None:
    bus = EventBus()
    seen: list[str] = []

    def bad(_event: BuildStarted) -> None:
        raise RuntimeError("boom")

    def good(event: BuildStarted) -> None:
        seen.append(event.project)

    bus.subscribe(BuildStarted, bad)
    bus.subscribe(BuildStarted, good)
    bus.publish(BuildStarted(project="app"))
    assert seen == ["app"]


def test_event_to_dict_and_logging_are_json_safe_for_path_metadata(tmp_path: Path) -> None:
    event = BuildStarted(project="app", metadata={"path": tmp_path / "repo", "nested": {"items": [tmp_path]}})
    payload = event_to_dict(event)
    assert payload["metadata"]["path"] == str(tmp_path / "repo")
    json.dumps(payload, sort_keys=True)
    LoggingEventListener()(event)


def test_json_event_listener_collects_json_safe_payload(tmp_path: Path) -> None:
    listener = JsonEventListener()
    listener(BuildStarted(project="app", metadata={"path": tmp_path}))
    assert listener.events[0]["metadata"]["path"] == str(tmp_path)


def test_ipc_terminal_state_cannot_resurrect_or_overwrite_on_equal_identity() -> None:
    current = {"run_id": "run", "status": "failed", "sequence": 5, "updated_at": "2026-09-13T10:00:00Z"}
    assert not _state_is_newer({"run_id": "run", "status": "running", "sequence": 6, "updated_at": "2026-09-13T10:01:00Z"}, current)
    assert not _state_is_newer({"run_id": "run", "status": "failed", "sequence": 5, "updated_at": "2026-09-13T10:02:00Z"}, current)


def test_ipc_timestamp_order_is_chronological_not_lexical() -> None:
    current = {"run_id": "run", "status": "running", "updated_at": "2026-09-13T09:30:00+00:00"}
    older_but_lexically_larger = {"run_id": "run", "status": "running", "updated_at": "2026-09-13T10:00:00+02:00"}
    newer = {"run_id": "run", "status": "running", "updated_at": "2026-09-13T09:31:00Z"}
    assert not _state_is_newer(older_but_lexically_larger, current)
    assert _state_is_newer(newer, current)


def test_ipc_register_rejects_state_run_id_mismatch() -> None:
    peer, client = _socket_peer()
    try:
        broker = IpcBroker(Path("/tmp/unused.sock"))
        broker.peers.add(peer)
        broker._handle(peer, {"type": "register", "run_id": "good", "state": {"run_id": "evil", "status": "running"}})
        assert "good" not in broker.producers
        response = _read_json(client)
        assert response["code"] == "run_id_mismatch"
    finally:
        peer.sock.close()
        client.close()


def test_ipc_publish_rejects_state_run_id_mismatch() -> None:
    peer, client = _socket_peer()
    try:
        broker = IpcBroker(Path("/tmp/unused.sock"))
        broker.peers.add(peer)
        broker._handle(peer, {"type": "register", "run_id": "good", "state": {"status": "running", "sequence": 1}})
        _read_json(client)
        broker._handle(peer, {"type": "publish", "run_id": "good", "state": {"run_id": "evil", "status": "running", "sequence": 2}})
        assert broker.runs["good"]["sequence"] == 1
        assert _read_json(client)["code"] == "run_id_mismatch"
    finally:
        peer.sock.close()
        client.close()


def test_ipc_publisher_registers_and_publishes_json_safe_run_id_state(tmp_path: Path) -> None:
    peer, client = _socket_peer()
    publisher = IpcPublisher("run-123")
    try:
        publisher.sock = client
        assert publisher.publish({"status": "running", "path": tmp_path / "repo"})
        message = _read_json(peer.sock)
        assert message["run_id"] == "run-123"
        assert message["state"]["run_id"] == "run-123"
        assert message["state"]["path"] == str(tmp_path / "repo")
    finally:
        publisher.close()
        peer.sock.close()


def test_broker_is_running_requires_real_pong(tmp_path: Path) -> None:
    sock_path = tmp_path / "fake.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    ready = threading.Event()

    def serve_once() -> None:
        ready.set()
        conn, _ = server.accept()
        with conn:
            conn.recv(1024)
            conn.sendall(b'{"type":"not-pong"}\n')
        server.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    ready.wait(1)
    assert broker_is_running(sock_path) is False
    thread.join(timeout=1)


def test_command_result_from_operation_treats_fatal_issues_as_failure() -> None:
    @dataclass
    class Op:
        name: str = "build"
        argv: list[str] = None  # type: ignore[assignment]
        returncode: int = 0
        output: str = ""
        duration_seconds: float = 0.0
        issues: list[Issue] = None  # type: ignore[assignment]
        metadata: dict[str, Any] = None  # type: ignore[assignment]
        skipped: bool = False
        log_path: str = ""
        failure_kind: str = ""

    op = Op(argv=["build"], issues=[Issue("fatal", "build", "file", "boom", "fatal problem")], metadata={})
    result = CommandResult.from_operation(op)
    assert result.success is False
    assert result.exit_code == 1
    assert result.errors == ["fatal problem"]


def test_structured_hook_result_rejects_bool_schema_and_bad_annotation(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"schema_version": True, "status": "pass"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="schema_version"):
        load_structured_hook_result(path)
    path.write_text(json.dumps({"schema_version": 1, "status": "pass", "annotations": [{"message": 4}]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="annotation message"):
        load_structured_hook_result(path)


def test_hook_json_payload_context_is_json_safe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'''set -eu
python3 - <<'PYSCRIPT'
import json, os, pathlib
payload=json.load(open(os.environ["DEVTOOL_HOOK_PAYLOAD_PATH"]))
pathlib.Path(os.environ["DEVTOOL_REPO_ROOT"], "payload.json").write_text(json.dumps(payload["context"], sort_keys=True))
PYSCRIPT
'''
    archive, manifest = _archive_with_validator(tmp_path / "payload.zip", script, protocol="json-v1", write_paths=["payload.json"])
    manifest["validation"]["payload_context"] = {"path": tmp_path / "repo", "set": {"b", "a"}, "nested": {"bytes": b"ok"}}
    result = run_artifact_validation_script(repo, archive, manifest=manifest)
    assert result and result.returncode == 0, result.output if result else ""
    context = json.loads((repo / "payload.json").read_text(encoding="utf-8"))
    assert context["path"] == str(tmp_path / "repo")
    assert sorted(context["set"]) == ["a", "b"]
    assert context["nested"]["bytes"] == "ok"


def test_hook_python_runner_string_false_options_are_not_enabled(tmp_path: Path) -> None:
    command = _python_command(
        script_path=tmp_path / "check.py",
        spec={"python": {"sync_dependencies": "false", "unlocked": "false", "offline": "false", "install_project": "false"}},
        workspace_root=tmp_path,
        repo_root=tmp_path,
        timeout_seconds=10,
    )
    assert "--sync-dependencies" not in command
    assert "--unlocked" not in command
    assert "--offline" not in command
    assert "--no-install-project" in command


def test_hook_timeout_kills_descendants_before_they_mutate_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'''set -eu
( sleep 2; printf late > "$DEVTOOL_REPO_ROOT/late.txt" ) &
sleep 10
'''
    archive, manifest = _archive_with_validator(tmp_path / "timeout.zip", script, timeout=1)
    result = run_artifact_validation_script(repo, archive, manifest=manifest)
    assert result and result.returncode == 124, result.output if result else ""
    time.sleep(2.5)
    assert not (repo / "late.txt").exists()


def test_hook_write_boundary_detects_gitignored_files_and_empty_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / ".gitignore").write_text("ignored.txt\nemptydir/\n", encoding="utf-8")
    script = b'''set -eu
printf ignored > "$DEVTOOL_REPO_ROOT/ignored.txt"
mkdir "$DEVTOOL_REPO_ROOT/emptydir"
'''
    archive, manifest = _archive_with_validator(tmp_path / "ignored.zip", script, write_paths=["declared.txt"])
    result = run_artifact_validation_script(repo, archive, manifest=manifest)
    assert result and result.returncode == 1, result.output if result else ""
    violations = result.metadata["hook_event"]["write_violations"]
    assert "ignored.txt" in violations
    assert "emptydir" in violations


def test_plugin_discovery_contains_malformed_descriptor_and_builtin_override(monkeypatch: pytest.MonkeyPatch) -> None:
    class Point:
        def __init__(self, name: str, descriptor: PluginDescriptor) -> None:
            self.name = name
            self.value = f"tests:{name}"
            self._descriptor = descriptor

        def load(self) -> Any:
            return self._descriptor

    class Points(list):
        def select(self, *, group: str) -> "Points":
            assert group == plugin_registry.ENTRY_POINT_GROUP
            return self

    malformed = PluginDescriptor(123, "bad", api_version=PLUGIN_API_VERSION)  # type: ignore[arg-type]
    override = PluginDescriptor("android", "override", api_version=PLUGIN_API_VERSION)
    extra = PluginDescriptor("custom", "ok", capabilities=("doctor",), api_version=PLUGIN_API_VERSION)
    monkeypatch.setattr(plugin_registry.metadata, "entry_points", lambda: Points([Point("bad", malformed), Point("android", override), Point("custom", extra)]))
    plugins, errors = plugin_registry.discover_plugins()
    assert any(plugin.name == "android" and plugin.source == "builtin" for plugin in plugins)
    assert any(plugin.name == "custom" for plugin in plugins)
    assert any("name must be" in item["error"] for item in errors)
    assert any(item["plugin"] == "android" and "override" in item["error"] for item in errors)


def test_plugin_doctor_failure_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(_context: Any) -> list[PluginCheck]:
        raise RuntimeError("doctor exploded")

    plugin = PluginDescriptor("boom", "bad doctor", doctor=boom)
    monkeypatch.setattr(plugin_registry, "discover_plugins", lambda: ([plugin], []))
    report = plugin_registry.doctor_plugin("boom", repo_root=tmp_path)
    assert report["ok"] is False
    assert report["checks"][0]["name"] == "doctor"
    assert "doctor exploded" in report["checks"][0]["detail"]
