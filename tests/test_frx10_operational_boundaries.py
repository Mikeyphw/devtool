from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from devtool.artifact_registry import load_registry, register_paths
from devtool.config import load_project_config
from devtool.federation_discovery import resolve_federated_repository
from devtool.federation_execution import run_federated_workflow
from devtool.federation_operations import (
    FederationOperationalBoundaryError,
    capture_federated_resume_checkpoint,
    collect_federated_operational_snapshot,
    discover_federated_orphan_children,
    validate_federated_resume_checkpoint,
)
from devtool.hooks.engine import EmbeddedHookEngine
from devtool.hooks.models import HookExecutionRequest
from devtool.invocation_origin import trusted_invocation_context, invocation_metadata_for_args
from devtool.models import CommandSpec
from devtool.repository_identity import build_repository_identity
from devtool.transactions import create_apply_transaction, mark_transaction_completed, transaction_root
from devtool.validation_sessions import create_session
from devtool.core.events import EventBus, RunFinished, RunStarted


def _child_config(workflow: str = "build") -> str:
    return f'''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\nroot="."\n[jobs.one]\nrunner="command"\ncommand=["echo","one"]\n[targets.app.workflows]\n{workflow}=["job:one"]\n'''


def _parent(tmp_path: Path, *, workflow: bool = False) -> tuple[Path, Path]:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")
    suffix = '''\n[federation.workflows.release]\nsteps=[{id="child",ref="child::build"}]\n''' if workflow else ""
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n''' + suffix,
        encoding="utf-8",
    )
    return parent, child


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _fed_env(child: Path, run_id: str = "fed-child-1") -> dict[str, str]:
    identity = build_repository_identity(child, child / ".devtool.toml")
    return {
        "DEVTOOL_RUN_ID": run_id,
        "DEVTOOL_FEDERATION_RELATIONSHIP": "child",
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": "parent-run",
        "DEVTOOL_FEDERATION_PARENT_NODE_ID": "federation:release:child",
        "DEVTOOL_FEDERATION_WORKFLOW": "release",
        "DEVTOOL_FEDERATION_STEP": "child",
        "DEVTOOL_FEDERATION_REPOSITORY": "child",
        "DEVTOOL_FEDERATION_SOURCE_REPOSITORY": "parent-fingerprint",
        "DEVTOOL_FEDERATION_REQUESTED_REPOSITORY": "child",
        "DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY": identity.fingerprint,
        "DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT": "local",
    }


@contextmanager
def _trusted_child_federation(child: Path, run_id: str):
    # The implementation refuses public DEVTOOL_FEDERATION_* routing hints as
    # authoritative lineage. These historical FRX10 tests must exercise the
    # private bridge context rather than implicitly spoofing it from a shell.
    with mock.patch.dict(os.environ, _fed_env(child, run_id), clear=False):
        with trusted_invocation_context(source="federation", command="child-execution"):
            yield


def test_untrusted_federation_environment_cannot_claim_operational_ownership(tmp_path: Path) -> None:
    from devtool.federation_ownership import federation_operational_owner_from_environment
    _, child = _parent(tmp_path)
    with mock.patch.dict(os.environ, _fed_env(child, "untrusted-child"), clear=False):
        assert federation_operational_owner_from_environment(child) == {}
        with trusted_invocation_context(source="federation", command="child-execution"):
            owner = federation_operational_owner_from_environment(child)
            assert owner["child_run_id"] == "untrusted-child"
            assert owner["owner_root"] == str(child.resolve())


def test_artifact_transaction_validation_and_hook_state_are_child_owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child = _parent(tmp_path)
    config = _load(parent)
    resolution = resolve_federated_repository(config, "child")
    run_id = "fed-child-1"
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    # Artifact validation intentionally redirects durable validation state to
    # the primary repository. This synthetic federation fixture must not inherit
    # the outer apply session or it can mistake the parent gate for child state.
    monkeypatch.delenv("DEVTOOL_VALIDATION_STATE_REPO_ROOT", raising=False)

    with _trusted_child_federation(child, run_id):
        artifact = child / "out.txt"
        artifact.write_text("artifact", encoding="utf-8")
        records = register_paths(child, [artifact], metadata={"producer": "test"})
        assert records[0]["metadata"]["federation_owner"]["child_run_id"] == run_id
        assert records[0]["metadata"]["federation_owner"]["owner_root"] == str(child.resolve())

        overlay = tmp_path / "noop.zip"
        import zipfile
        with zipfile.ZipFile(overlay, "w"):
            pass
        tx = create_apply_transaction(
            child,
            overlay,
            artifact_type="overlay",
            preview={"changes": [], "counts": {}},
        )
        assert tx["federation_owner"]["repository"] == "child"
        assert Path(tx["repo_root"]) == child.resolve()

        child_config = _load(child)
        session = create_session(
            child_config,
            [CommandSpec("echo", ["echo", "ok"], cwd=str(child))],
            transaction_path=str(tx["path"]),
            persist=True,
        )
        assert session.apply_context["federation_owner"]["child_run_id"] == run_id
        assert Path(session.repo_root) == child.resolve()

        dummy = child / "hook.zip"
        dummy.write_bytes(b"dummy")
        hook = EmbeddedHookEngine().execute(HookExecutionRequest(
            repo_root=child,
            artifact=dummy,
            manifest={},
            spec={"path": ".devtool-artifact/hook.sh", "runner": "sh", "sha256": "0" * 64},
            kind="finalize",
            phase="before_commit",
            script_label="Finalize",
            operation_name="hook",
            temp_prefix="frx10-",
            temp_env_key="DEVTOOL_TEST_HOOK",
            failure_kind="hook",
            run_id=run_id,
            dry_run=True,
        ))
        hook_event = hook.metadata["hook_event"]
        assert hook_event["federation_owner"]["owner_root"] == str(child.resolve())

        snapshot = SimpleNamespace(
            nodes=(SimpleNamespace(metadata={"hook_event": hook_event}),),
            diagnostics=(SimpleNamespace(diagnostic_id="diag-1", log_path="/tmp/child.log", severity="warning"),),
            processes=(SimpleNamespace(log_path="/tmp/child.log"),),
        )
        ownership = collect_federated_operational_snapshot(
            resolution,
            child_run_id=run_id,
            execution_snapshot=snapshot,
        )

    kinds = {ref.kind for ref in ownership.references}
    assert {"artifact-registry", "transaction", "validation-session", "hook-event", "diagnostic", "log"} <= kinds
    assert all(ref.owner_root == str(child.resolve()) for ref in ownership.references)
    assert all(ref.child_run_id == run_id for ref in ownership.references)
    # Parent aggregation is references only; it never imports transaction/session payloads.
    payload = ownership.as_dict()
    assert "affected_paths" not in json.dumps(payload)
    assert "plan_specs" not in json.dumps(payload)

    mark_transaction_completed(tx["path"])
    history = transaction_root(child) / "history" / f"{tx['transaction_id']}.json"
    assert history.is_file()
    archived = json.loads(history.read_text(encoding="utf-8"))
    assert archived["federation_owner"]["child_run_id"] == run_id


def test_parent_validation_never_satisfies_child_operational_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child = _parent(tmp_path)
    config = _load(parent)
    resolution = resolve_federated_repository(config, "child")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    # Keep the synthetic parent/child validation state inside tmp_path rather
    # than inheriting an enclosing artifact-validation session.
    monkeypatch.delenv("DEVTOOL_VALIDATION_STATE_REPO_ROOT", raising=False)

    parent_session = create_session(
        config,
        [CommandSpec("parent", ["echo", "parent"], cwd=str(parent))],
        persist=True,
    )
    assert Path(parent_session.repo_root) == parent.resolve()

    with _trusted_child_federation(child, "child-validation"):
        child_config = _load(child)
        child_session = create_session(
            child_config,
            [CommandSpec("child", ["echo", "child"], cwd=str(child))],
            persist=True,
        )
        ownership = collect_federated_operational_snapshot(
            resolution,
            child_run_id="child-validation",
        )
    refs = [ref for ref in ownership.references if ref.kind == "validation-session"]
    assert [ref.identifier for ref in refs] == [child_session.session_id]
    assert all(ref.identifier != parent_session.session_id for ref in refs)


def test_resume_checkpoint_rejects_child_revision_change(tmp_path: Path) -> None:
    parent, child = _parent(tmp_path)
    subprocess = __import__("subprocess")
    subprocess.run(["git", "init", "-q"], cwd=child, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=child, check=True)
    subprocess.run(["git", "config", "user.name", "FRX Test"], cwd=child, check=True)
    subprocess.run(["git", "add", "."], cwd=child, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=child, check=True)

    config = _load(parent)
    checkpoint = capture_federated_resume_checkpoint(resolve_federated_repository(config, "child"))
    validate_federated_resume_checkpoint(config, checkpoint)

    (child / "revision.txt").write_text("changed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=child, check=True)
    subprocess.run(["git", "commit", "-qm", "two"], cwd=child, check=True)
    with pytest.raises(FederationOperationalBoundaryError, match="revision changed"):
        validate_federated_resume_checkpoint(config, checkpoint)


def test_orphan_child_discovery_requires_missing_parent_snapshot() -> None:
    child = SimpleNamespace(run_id="child-1", run_lineage={"relationship": "child", "parent_run_id": "parent-1"})
    unrelated = SimpleNamespace(run_id="other", run_lineage={})
    assert discover_federated_orphan_children([child, unrelated], parent_run_id="parent-1") == ("child-1",)
    parent = SimpleNamespace(run_id="parent-1", run_lineage={"relationship": "coordinator"})
    assert discover_federated_orphan_children([parent, child], parent_run_id="parent-1") == ()


def test_parent_cancellation_requests_child_cancel_without_parent_rollback(tmp_path: Path) -> None:
    parent, _child = _parent(tmp_path, workflow=True)
    config = _load(parent)
    requested: list[str] = []

    def interrupt(_argv: list[str]) -> int:
        raise KeyboardInterrupt

    report = run_federated_workflow(
        config,
        "release",
        reenter=interrupt,
        parent_run_id="parent-cancel",
        request_child_cancel=lambda run_id: requested.append(run_id),
    )
    assert report.status == "cancelled"
    assert report.exit_code == 130
    assert len(requested) == 1
    # Parent cancellation records the active child as cancelled so EXO lineage
    # remains inspectable, but it does not manufacture rollback/validation
    # ownership for that child.
    assert len(report.children) == 1
    child = report.children[0]
    assert child.status == "cancelled" and child.exit_code == 130
    assert child.operational_ownership == {}


def test_collector_rejects_mismatched_child_owner_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child = _parent(tmp_path)
    config = _load(parent)
    resolution = resolve_federated_repository(config, "child")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    artifact = child / "bad.txt"; artifact.write_text("x", encoding="utf-8")
    with _trusted_child_federation(child, "child-good"):
        register_paths(child, [artifact])
    registry = load_registry(child)
    registry["items"][0]["metadata"]["federation_owner"]["child_run_id"] = "wrong"
    # Persist corruption directly to prove collector fails closed; the public
    # save API is append-only and intentionally refuses to rewrite old events.
    from devtool.artifact_registry import registry_path
    registry_path(child).write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(FederationOperationalBoundaryError, match="mismatched federation owner"):
        collect_federated_operational_snapshot(resolution, child_run_id="child-good")


def test_federated_run_report_aggregates_child_operational_references_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child = _parent(tmp_path, workflow=True)
    config = _load(parent)
    bus = EventBus()
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))

    def child_reenter(argv: list[str]) -> int:
        run_id = os.environ["DEVTOOL_RUN_ID"]
        root = Path(argv[argv.index("--repo") + 1])
        metadata = invocation_metadata_for_args(SimpleNamespace(
            _devtool_invocation_source="federation",
            _devtool_invocation_command="child",
            _devtool_wrapper_schema="",
            command="invoke",
        ))
        metadata["repository_identity"] = build_repository_identity(root, root / ".devtool.toml").as_dict()
        bus.publish(RunStarted(run_id=run_id, command="invoke", project=str(root), source="federation", metadata=metadata))
        produced = root / "produced.txt"; produced.write_text("ok", encoding="utf-8")
        register_paths(root, [produced], metadata={"producer": "child"})
        bus.publish(RunFinished(run_id=run_id, status="succeeded", exit_code=0, source="federation"))
        return 0

    report = run_federated_workflow(
        config,
        "release",
        reenter=child_reenter,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parent_run_id="parent-own",
    )
    assert report.ok and len(report.children) == 1
    ownership = report.children[0].operational_ownership
    assert ownership["repository"] == "child"
    assert ownership["child_run_id"] == report.children[0].run_id
    assert ownership["resume_checkpoint"]["root"] == str(child.resolve())
    artifact_refs = [item for item in ownership["references"] if item["kind"] == "artifact-registry"]
    assert len(artifact_refs) == 1
    assert artifact_refs[0]["metadata"]["artifact_paths"] == ["produced.txt"]
