from __future__ import annotations

from pathlib import Path

from devtool.artifact_tests import run_artifact_tests
from devtool.artifacts import _validate_validation_policy, artifact_validation_refs


def _config(root: Path) -> None:
    (root / ".devtool.toml").write_text(
        '''
[project]
runner = "command"

[workspace]
default_target = "app"

[targets.app]
runner = "command"
root = "."

[[test]]
id = "gate-unit"
name = "Gate unit"
runner = "command"
command = ["python", "-c", "print('gate-unit-ok')"]

[targets.app.workflows]
gate = [
  { id = "unit", ref = "test:gate-unit", depends_on = [] },
]
'''.strip() + "\n",
        encoding="utf-8",
    )


def test_artifact_validation_refs_are_first_class_schema_inputs(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "target": "app",
        "inventory": {"format": 1, "entries": []},
        "validation": {"required": True, "restore": False, "build": False, "test": False, "refs": ["workflow:gate"]},
    }
    messages: list[str] = []
    assert _validate_validation_policy(manifest, messages, bundle=False)
    assert messages == []
    assert artifact_validation_refs(manifest) == [{"ref": "workflow:gate", "kind": "workflow", "name": "gate"}]


def test_artifact_workflow_ref_executes_through_canonical_workflow_scheduler(monkeypatch, tmp_path: Path) -> None:
    _config(tmp_path)
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(tmp_path / "missing-global.toml"))
    manifest = {
        "target": "app",
        "validation": {"refs": ["workflow:gate"]},
    }
    results, raw = run_artifact_tests(tmp_path, manifest, verbose=False)
    assert results and all(result.returncode == 0 for result in results)
    assert "gate-unit-ok" in raw
    assert all(result.metadata.get("artifact_validation_ref") == "workflow:gate" for result in results)
    assert all(result.metadata.get("artifact_validation_workflow") == "gate" for result in results)
    assert any(result.metadata.get("workflow_node_key") for result in results)


def test_invoke_alias_normalizes_to_workflow_ref() -> None:
    refs = artifact_validation_refs({"validation": {"refs": ["invoke:roadmap-exec-g1"]}})
    assert refs == [{"ref": "workflow:roadmap-exec-g1", "kind": "workflow", "name": "roadmap-exec-g1"}]
