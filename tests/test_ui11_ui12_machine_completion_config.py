from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from devtool.cli import build_parser
from devtool.completion import ZSH_COMPLETION
from devtool.completion_state import build_completion_state, completion_values
from devtool.config import load_project_config
from devtool.config_migration import analyze_config_migration_text, migrate_config_text
from devtool.ipc import IpcBroker, IpcPublisher, request_run_action
from devtool.models import OperationResult
from devtool.summary import build_runner_json
from devtool.tasker import TaskerProgressSink
from devtool.ui_core.machine import IPC_PROTOCOL_VERSION, MACHINE_SCHEMA_VERSION, TASKER_SCHEMA_VERSION, ipc_contract, machine_state_from_runtime, tasker_fields

ROOT = Path(__file__).resolve().parents[1]


def _recv(sock: socket.socket, timeout: float = 2.0) -> dict:
    sock.settimeout(timeout)
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = sock.recv(8192)
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in data:
            return json.loads(bytes(data).split(b"\n", 1)[0])
    raise TimeoutError("no IPC message")


class MachineContractTests(unittest.TestCase):
    def test_machine_state_v2_contains_canonical_sections(self) -> None:
        raw = {
            "run_id": "run-1", "status": "running", "command": "validate", "repo_root": "/repo",
            "repo_name": "repo", "target": "api", "targets": ["api"], "current": 2, "total": 4,
            "phase": {"name": "test", "node_id": "api:test", "workflow": "validate"},
            "test_selection": {"pytest_tests": ["tests/test_api.py::test_create"]},
        }
        machine = machine_state_from_runtime(raw).as_dict()
        self.assertEqual(machine["schema_version"], 2)
        for key in ("workspace", "selection", "operation", "workflow", "test_selection", "progress", "diagnostics", "actions", "navigation", "notifications", "presentation", "capabilities", "responsive"):
            self.assertIn(key, machine)
        self.assertEqual(machine["workflow"]["current_node"], "api:test")
        self.assertEqual(machine["test_selection"]["pytest_tests"], ["tests/test_api.py::test_create"])

    def test_tasker_fields_are_stable_semantic_values(self) -> None:
        machine = machine_state_from_runtime({
            "run_id": "r", "status": "failed", "command": "test", "target": "api",
            "current": 1, "total": 2, "phase": {"name": "unit", "node_id": "api:unit"},
        })
        fields = tasker_fields(machine)
        self.assertEqual(fields["DEVTOOL_MACHINE_SCHEMA"], MACHINE_SCHEMA_VERSION)
        self.assertEqual(fields["DEVTOOL_OPERATION"], "test")
        self.assertEqual(fields["DEVTOOL_TARGET"], "api")
        self.assertEqual(fields["DEVTOOL_WORKFLOW_NODE"], "api:unit")
        self.assertEqual(fields["DEVTOOL_PROGRESS"], 50)

    def test_tasker_live_state_keeps_legacy_and_adds_machine_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = TaskerProgressSink(
                directory=Path(tmp), command="validate", repo_root="/repo", repo_name="repo", target="api",
                test_selection={"pytest_tests": ["tests/test_api.py::test_create"]},
            )
            sink.started()
            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["schema_version"], 1)
            self.assertEqual(state["machine"]["schema_version"], MACHINE_SCHEMA_VERSION)
            self.assertEqual(state["tasker"]["schema_version"], TASKER_SCHEMA_VERSION)
            self.assertEqual(state["machine"]["test_selection"]["pytest_tests"], ["tests/test_api.py::test_create"])
            self.assertIn("DEVTOOL_STATUS", state["tasker"]["fields"])

    def test_runner_json_keeps_v1_envelope_and_embeds_machine_v2(self) -> None:
        result = OperationResult("test", ["pytest"], 0, 0.2, "ok", [])
        payload = build_runner_json(
            [result], repo_root="/repo", command="validate", raw_output="ok", summary_text="passed",
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["machine_schema_version"], 2)
        self.assertEqual(payload["machine"]["schema_version"], 2)
        self.assertEqual(payload["machine"]["operation"]["command"], "validate")

    def test_ipc_contract_is_v2_and_legacy_compatible(self) -> None:
        contract = ipc_contract()
        self.assertEqual(contract["protocol_version"], IPC_PROTOCOL_VERSION)
        self.assertTrue(contract["compatibility"]["legacy_cancel"])
        self.assertIn("action", contract["messages"])


class IpcSemanticActionTests(unittest.TestCase):
    def test_snapshot_advertises_v2_and_semantic_action_reaches_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "control.sock"
            broker = IpcBroker(path)
            thread = threading.Thread(target=broker.serve_forever, daemon=True)
            thread.start()
            # A bound Unix socket path is not proof that listen() completed.
            # Wait for an actual connection before publishing; otherwise a
            # scheduled CI worker can race the broker's bind/listen boundary.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                self.assertTrue(thread.is_alive(), "IPC broker stopped during startup")
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.settimeout(0.2)
                    probe.connect(str(path))
                    break
                except (OSError, socket.timeout):
                    time.sleep(0.01)
                finally:
                    probe.close()
            else:
                self.fail("IPC broker did not accept connections within 5 seconds")

            cancelled = threading.Event()
            publisher = IpcPublisher("run-1", socket_path=path, on_cancel=cancelled.set)
            self.assertTrue(publisher.publish({"run_id": "run-1", "status": "running", "command": "build"}))

            consumer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            consumer.connect(str(path))
            consumer.sendall(b'{"type":"subscribe"}\n')
            snapshot = _recv(consumer)
            self.assertEqual(snapshot["protocol_version"], IPC_PROTOCOL_VERSION)
            self.assertEqual(snapshot["machine_schema_version"], MACHINE_SCHEMA_VERSION)
            self.assertEqual(snapshot["runs"][0]["machine"]["schema_version"], MACHINE_SCHEMA_VERSION)

            ok, _ = request_run_action("run-1", "operation.cancel", socket_path=path)
            self.assertTrue(ok)
            self.assertTrue(cancelled.wait(1.0))
            publisher.close(unregister=True)
            consumer.close()
            broker.stop_event.set()
            thread.join(timeout=2)
            broker.close()


class CompletionStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_project_config(ROOT, config_path_arg=str(ROOT / ".devtool.toml"), target_name="devtool")

    def test_completion_state_uses_resolved_config(self) -> None:
        state = build_completion_state(self.config)
        self.assertIn("devtool", state.targets)
        self.assertIn("android_hud", state.targets)
        self.assertIn("developer", state.ui_profiles)

    def test_pytest_completion_uses_source_catalog(self) -> None:
        values = completion_values(self.config, "pytest-tests")
        self.assertTrue(any(value.startswith("tests/") and "::" in value for value in values))

    def test_completion_script_prefers_completion_state_with_fallback(self) -> None:
        self.assertIn("completion-state --kind", ZSH_COMPLETION)
        self.assertIn("_devtool_pytest_tests", ZSH_COMPLETION)
        self.assertIn("sed -nE", ZSH_COMPLETION)  # compatibility fallback

    def test_parser_exposes_completion_state_and_config_migration(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["completion-state", "--kind", "targets"])
        self.assertEqual(args.kind, "targets")
        args = parser.parse_args(["config", "migrate", "--check"])
        self.assertEqual(args.config_command, "migrate")
        self.assertTrue(args.check)


class ConfigMigrationTests(unittest.TestCase):
    def test_migration_detects_project_kind_and_target_type(self) -> None:
        text = '''[project]\nkind = "python"\n\n[targets.api]\ntype = "python"\nroot = "api"\n'''
        findings = analyze_config_migration_text(text)
        self.assertEqual([(item.section, item.key) for item in findings], [("project", "kind"), ("targets.api", "type")])

    def test_safe_migration_rewrites_runner_and_adds_platform(self) -> None:
        text = '''[project]\nkind = "python"\n\n[targets.app]\ntype = "android"\nroot = "app"\n'''
        migrated, findings = migrate_config_text(text)
        self.assertEqual(len(findings), 2)
        self.assertIn('runner = "python"', migrated)
        self.assertIn('platform = "server"', migrated)
        self.assertIn('runner = "android"', migrated)
        self.assertIn('platform = "android"', migrated)
        self.assertNotIn('kind = "python"', migrated)
        self.assertNotIn('type = "android"', migrated)

    def test_existing_runner_removes_redundant_legacy_alias_without_duplicate(self) -> None:
        text = '[project]\nrunner = "python"\nkind = "python"\n'
        migrated, findings = migrate_config_text(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(migrated.count('runner = "python"'), 1)
        self.assertNotIn('kind = "python"', migrated)

    def test_canonical_config_reports_no_migration(self) -> None:
        findings = analyze_config_migration_text('[project]\nrunner = "python"\nplatform = "server"\n')
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
