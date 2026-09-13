from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

from devtool.artifacts import validate_artifact, write_archive_entries
from devtool.ipc import IpcBroker, _Peer
from devtool.runners import apply_overlay


ROOT = Path(__file__).resolve().parents[1]
ROOT_BRIDGE = ROOT / "root-module/devtool-bridge/bin/devtool-root-bridge"


def _recv_json(sock: socket.socket) -> dict:
    sock.settimeout(1.0)
    data = bytearray()
    while b"\n" not in data:
        chunk = sock.recv(8192)
        if not chunk:
            break
        data.extend(chunk)
    line = bytes(data).split(b"\n", 1)[0]
    return json.loads(line.decode("utf-8"))


def _bridge_function_call(call: str, **environment: str) -> subprocess.CompletedProcess[str]:
    source = ROOT_BRIDGE.read_text(encoding="utf-8")
    prefix, separator, _dispatch = source.partition('\ncase "${1:-status}" in')
    assert separator, "root bridge dispatch marker changed"
    env = dict(os.environ)
    env.update(environment)
    return subprocess.run(
        ["sh"],
        input=prefix + "\n" + call + "\n",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_direct_apply_revalidates_artifact_before_repository_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive = tmp_path / "invalid-overlay.tar.gz"
    payload = b"owned-by-invalid-artifact"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [
                {
                    "path": "payload.txt",
                    "action": "add",
                    # Deliberately wrong: the old direct runner still extracted it.
                    "sha256": "0" * 64,
                    "size": len(payload),
                }
            ],
        },
        "apply": {
            "atomic": True,
            "conflict_policy": "fail",
            "path_policy": "repository-relative",
        },
    }
    write_archive_entries(
        archive,
        {
            ".devtool-artifact.json": json.dumps(manifest).encode("utf-8"),
            "payload.txt": payload,
        },
    )

    validation = validate_artifact(repo, archive)
    assert validation.ok is False

    result = apply_overlay(repo, str(archive))

    assert result.returncode == 1
    assert result.failure_kind == "artifact_failure"
    assert result.metadata.get("artifact_validation_enforced") is True
    assert "Artifact validation failed before direct apply" in result.output
    assert not (repo / "payload.txt").exists()
    assert not (repo / ".devtool").exists()


def test_ipc_unregistered_peer_cannot_spoof_run_update(tmp_path: Path) -> None:
    broker = IpcBroker(tmp_path / "broker.sock")
    server_sock, client_sock = socket.socketpair()
    try:
        peer = _Peer(server_sock)
        broker._handle(
            peer,
            {
                "type": "publish",
                "state": {
                    "run_id": "victim",
                    "status": "running",
                    "sequence": 99,
                },
            },
        )
        response = _recv_json(client_sock)
        assert response["type"] == "error"
        assert response["code"] == "producer_ownership_required"
        assert "victim" not in broker.runs
    finally:
        server_sock.close()
        client_sock.close()


def test_ipc_superseded_producer_cannot_publish_or_remove_run(tmp_path: Path) -> None:
    broker = IpcBroker(tmp_path / "broker.sock")
    old_server, old_client = socket.socketpair()
    new_server, new_client = socket.socketpair()
    try:
        old_peer = _Peer(old_server)
        new_peer = _Peer(new_server)
        broker._handle(
            old_peer,
            {
                "type": "register",
                "run_id": "run-1",
                "state": {"run_id": "run-1", "status": "running", "sequence": 1},
            },
        )
        assert _recv_json(old_client)["type"] == "registered"

        broker._handle(
            new_peer,
            {
                "type": "register",
                "run_id": "run-1",
                "state": {"run_id": "run-1", "status": "running", "sequence": 2},
            },
        )
        assert _recv_json(new_client)["type"] == "registered"
        assert broker.producers["run-1"] is new_peer

        broker._handle(
            old_peer,
            {
                "type": "publish",
                "state": {"run_id": "run-1", "status": "failed", "sequence": 999},
            },
        )
        rejected_publish = _recv_json(old_client)
        assert rejected_publish["code"] == "producer_ownership_required"
        assert broker.runs["run-1"]["sequence"] == 2

        broker._handle(old_peer, {"type": "remove_run", "run_id": "run-1"})
        rejected_remove = _recv_json(old_client)
        assert rejected_remove["code"] == "producer_ownership_required"
        assert "run-1" in broker.runs

        broker._handle(new_peer, {"type": "remove_run", "run_id": "run-1"})
        assert "run-1" not in broker.runs
    finally:
        old_server.close()
        old_client.close()
        new_server.close()
        new_client.close()


def test_root_bridge_rejects_command_substitution_in_env_assignment() -> None:
    safe = _bridge_function_call('validate_command "$TEST_COMMAND"', TEST_COMMAND="CI=1 devtool validate")
    assert safe.returncode == 0, safe.stderr

    unsafe = _bridge_function_call(
        'validate_command "$TEST_COMMAND"',
        TEST_COMMAND='EVIL="$(id)" devtool validate',
    )
    assert unsafe.returncode == 64
    assert "shell expansion/control syntax" in unsafe.stderr


def test_root_bridge_rejects_command_substitution_in_double_quoted_argument() -> None:
    result = _bridge_function_call(
        'validate_command "$TEST_COMMAND"',
        TEST_COMMAND='devtool validate --phase "$(id)"',
    )
    assert result.returncode == 64


def test_root_bridge_rejects_cancel_run_id_path_traversal() -> None:
    good = _bridge_function_call('validate_run_id "$TEST_RUN_ID"', TEST_RUN_ID="root-deadbeef")
    assert good.returncode == 0, good.stderr

    traversal = _bridge_function_call('validate_run_id "$TEST_RUN_ID"', TEST_RUN_ID="../../token")
    assert traversal.returncode == 64
    assert "Invalid run id" in traversal.stderr

    absolute = _bridge_function_call('validate_run_id "$TEST_RUN_ID"', TEST_RUN_ID="/data/adb/devtool-bridge/token")
    assert absolute.returncode == 64
