from __future__ import annotations

import json
import os
from pathlib import Path

from devtool.core.events import EventBus, NodeFinished, NodeStarted
from devtool.core.execution_semantics import NodeDefinitionIdentity, ReusableNodeRecord, ReuseDisposition
from devtool.core.execution_store import ExecutionStore
from devtool.core.reuse import (
    InputProvenance,
    PortabilityClass,
    ReuseHistoryIndex,
    aggregate_fingerprints,
    annotate_spec_reuse,
    conservative_repository_identity,
    contribution_from_spec,
    dependency_fingerprint_from_metadata,
    diff_fingerprints,
    finalize_spec_reuse_metadata,
    fingerprint_for_spec,
    output_manifest_for_spec,
    qualify_candidate,
    verify_output_manifest,
)
from devtool.models import CommandSpec, WorkflowStepRef
from devtool.workflow_graph import ExecutionGraph, WorkflowNode


def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    os.system(f"git -C {root} init -q")
    os.system(f"git -C {root} config user.email test@example.invalid")
    os.system(f"git -C {root} config user.name Test")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    os.system(f"git -C {root} add tracked.txt && git -C {root} commit -qm base")
    return root


def test_explicit_input_manifest_is_content_addressed(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    path = root / "src.py"
    path.write_text("print(1)\n", encoding="utf-8")
    spec = CommandSpec("test", ["python", "src.py"], cwd=str(root), metadata={"workflow_inputs": ["src.py"]})
    first = contribution_from_spec(spec, repo_root=root)
    assert first.input_manifest.provenance is InputProvenance.EXPLICIT
    path.touch()
    second = contribution_from_spec(spec, repo_root=root)
    assert first.input_manifest.digest == second.input_manifest.digest
    path.write_text("print(2)\n", encoding="utf-8")
    third = contribution_from_spec(spec, repo_root=root)
    assert third.input_manifest.digest != first.input_manifest.digest


def test_unknown_inputs_use_git_content_fallback_not_timestamps(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    before = conservative_repository_identity(root)
    (root / "tracked.txt").touch()
    touched = conservative_repository_identity(root)
    assert touched.digest == before.digest
    (root / "tracked.txt").write_text("two\n", encoding="utf-8")
    changed = conservative_repository_identity(root)
    assert changed.digest != before.digest


def test_raw_passthrough_participates_in_reuse_identity(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    a = CommandSpec("pytest", ["pytest", "-q"], cwd=str(root), metadata={"raw_passthrough_argv": ["tests/unit"], "workflow_inputs": ["tracked.txt"]})
    b = CommandSpec("pytest", ["pytest", "-q"], cwd=str(root), metadata={"raw_passthrough_argv": ["tests/integration"], "workflow_inputs": ["tracked.txt"]})
    fa, _, _ = fingerprint_for_spec(a, repo_root=root)
    fb, _, _ = fingerprint_for_spec(b, repo_root=root)
    assert fa.invocation_digest != fb.invocation_digest
    assert "invocation_digest" in diff_fingerprints(fa.to_dict(), fb.to_dict()).changed


def test_relevant_environment_domains_participate_but_presentation_does_not(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    base = CommandSpec("go", ["go", "test", "./..."], cwd=str(root), env={"TERM": "xterm"}, metadata={"workflow_inputs": ["tracked.txt"], "environment_domains": {"go": "go1.24"}, "execution_environment": "native-termux"})
    presentation = CommandSpec("go", list(base.argv), cwd=str(root), env={"TERM": "dumb"}, metadata=dict(base.metadata))
    changed = CommandSpec("go", list(base.argv), cwd=str(root), env={"TERM": "dumb"}, metadata={**base.metadata, "environment_domains": {"go": "go1.25"}})
    f1, _, _ = fingerprint_for_spec(base, repo_root=root)
    f2, _, _ = fingerprint_for_spec(presentation, repo_root=root)
    f3, _, _ = fingerprint_for_spec(changed, repo_root=root)
    assert f1.environment_digest == f2.environment_digest
    assert f1.environment_digest != f3.environment_digest


def test_secret_dependent_work_is_non_reusable_without_safe_identity(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    spec = CommandSpec("publish", ["tool", "publish"], cwd=str(root), env={"API_TOKEN": "super-secret-value"}, metadata={"workflow_inputs": ["tracked.txt"]})
    fp, contribution, _ = fingerprint_for_spec(spec, repo_root=root)
    assert not fp.reusable
    assert contribution.environment.portability is PortabilityClass.NON_REUSABLE
    assert "super-secret-value" not in json.dumps(contribution.to_dict())


def test_safe_secret_identity_allows_change_sensitive_reuse_without_secret_storage(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    one = CommandSpec("publish", ["tool"], cwd=str(root), env={"API_TOKEN": "raw-one"}, metadata={"workflow_inputs": ["tracked.txt"], "reuse_secret_identities": ["vault:token:v1"]})
    two = CommandSpec("publish", ["tool"], cwd=str(root), env={"API_TOKEN": "raw-two"}, metadata={"workflow_inputs": ["tracked.txt"], "reuse_secret_identities": ["vault:token:v2"]})
    f1, c1, _ = fingerprint_for_spec(one, repo_root=root)
    f2, _, _ = fingerprint_for_spec(two, repo_root=root)
    assert f1.reusable and f2.reusable
    assert f1.environment_digest != f2.environment_digest
    payload = json.dumps(c1.to_dict())
    assert "raw-one" not in payload
    assert "vault:token:v1" in payload


def test_dependency_result_identity_propagates_transitively() -> None:
    first = dependency_fingerprint_from_metadata({"workflow_depends_on": ["a"], "reuse_dependency_results": {"a": "result-1"}})
    second = dependency_fingerprint_from_metadata({"workflow_depends_on": ["a"], "reuse_dependency_results": {"a": "result-2"}})
    missing = dependency_fingerprint_from_metadata({"workflow_depends_on": ["a"]})
    assert first.complete and second.complete
    assert first.digest != second.digest
    assert not missing.complete


def test_output_manifest_detects_missing_and_mutated_outputs(tmp_path: Path) -> None:
    root = tmp_path / "repo"; root.mkdir()
    out = root / "out.bin"; out.write_bytes(b"abc")
    spec = CommandSpec("build", ["build"], cwd=str(root), metadata={"workflow_outputs": ["out.bin"]})
    manifest = output_manifest_for_spec(spec)
    assert verify_output_manifest(manifest.to_dict(), cwd=root)[0]
    out.write_bytes(b"xyz")
    ok, failures = verify_output_manifest(manifest.to_dict(), cwd=root)
    assert not ok and any(v.startswith("mutated-output:") for v in failures)
    out.unlink()
    ok, failures = verify_output_manifest(manifest.to_dict(), cwd=root)
    assert not ok and any(v.startswith("missing-output:") for v in failures)


def _candidate(fp: dict, output: dict, *, materialization: tuple[str, ...] = ()) -> dict:
    output = dict(output)
    output["materialization_refs"] = list(materialization)
    return {
        "schema_version": 2,
        "node_definition_id": fp["node_definition_id"],
        "source_run_id": "run-old",
        "source_node_id": "node-old",
        "execution_status": "succeeded",
        "qualification_complete": True,
        "reusable": True,
        "fingerprint": fp,
        "output_manifest": output,
        "result_identity": "result-old",
        "evidence_refs": [],
    }


def test_qualification_distinguishes_reuse_materialize_and_invalidated(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    (root / "out.bin").write_bytes(b"abc")
    spec = CommandSpec("build", ["build"], cwd=str(root), metadata={"workflow_inputs": ["tracked.txt"], "workflow_outputs": ["out.bin"]})
    fp, _, _ = fingerprint_for_spec(spec, repo_root=root)
    output = output_manifest_for_spec(spec).to_dict()
    current = fp.to_dict()
    assert qualify_candidate(current, _candidate(current, output), cwd=root).disposition == "reuse"
    (root / "out.bin").unlink()
    assert qualify_candidate(current, _candidate(current, output), cwd=root).disposition == "invalidated"
    assert qualify_candidate(current, _candidate(current, output, materialization=("artifact:sha256:" + "a" * 64,)), cwd=root).disposition == "materialize"
    assert qualify_candidate(current, _candidate(current, output, materialization=("artifact:latest",)), cwd=root).disposition == "invalidated"
    changed = dict(current); changed["environment_digest"] = "different"
    assert qualify_candidate(changed, _candidate(current, output), cwd=root).disposition == "invalidated"


def test_old_x01_reuse_shell_fails_safe_to_run() -> None:
    record = ReusableNodeRecord.from_history_payload({
        "schema_version": 1,
        "node_definition_id": "workflow-node:a@1",
        "source_run_id": "run-old",
        "source_node_id": "node-old",
        "execution_status": "succeeded",
        "reusable": True,
        "qualification_complete": True,
    })
    assert record.disposition is ReuseDisposition.RUN
    assert not record.reusable
    assert not record.qualification_complete


def test_history_index_is_rebuilt_from_exo_history(tmp_path: Path) -> None:
    history = tmp_path / "history" / "run-1"; history.mkdir(parents=True)
    record = {
        "schema_version": 2,
        "node_definition_id": "node:def",
        "source_run_id": "run-1",
        "source_node_id": "node-1",
        "execution_status": "succeeded",
        "reusable": True,
        "qualification_complete": True,
        "fingerprint": {"digest": "fp"},
        "output_manifest": {"digest": "out"},
        "result_identity": "result",
    }
    (history / "execution.json").write_text(json.dumps({"execution": {"nodes": [{"metadata": {"reusable_node_record": record}}]}}), encoding="utf-8")
    index = ReuseHistoryIndex.from_history_directory(tmp_path / "history")
    assert index.candidates("node:def")[0]["source_run_id"] == "run-1"


def test_multi_spec_fingerprints_aggregate_in_order() -> None:
    rows = [
        {"digest": "a", "invocation_digest": "ia", "input_manifest_digest": "xa", "dependency_digest": "da", "environment_digest": "ea", "reusable": True},
        {"digest": "b", "invocation_digest": "ib", "input_manifest_digest": "xb", "dependency_digest": "db", "environment_digest": "eb", "reusable": True},
    ]
    first = aggregate_fingerprints("node:def", rows)
    reverse = aggregate_fingerprints("node:def", list(reversed(rows)))
    assert first["digest"] != reverse["digest"]
    assert first["spec_digests"] == ["a", "b"]


def test_execution_store_attaches_qualified_record_to_exo_terminal_history(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    out = root / "out.txt"; out.write_text("ok", encoding="utf-8")
    node = WorkflowNode(
        key="api.build.compile", target="api", workflow="build", index=1,
        ref=WorkflowStepRef(raw="runner:python#build", kind="runner", name="python", action="build", step_id="compile"),
    )
    graph = ExecutionGraph(target="api", workflow="build", nodes=(node,))
    store = ExecutionStore(); store.plan_graph(graph, run_id="run-new")
    definition = NodeDefinitionIdentity.from_workflow_node(node).value
    spec = CommandSpec("build", ["python", "-m", "build"], cwd=str(root), metadata={"workflow_inputs": ["tracked.txt"], "workflow_outputs": ["out.txt"]})
    annotate_spec_reuse(spec, repo_root=root, node_definition_id=definition)
    terminal = finalize_spec_reuse_metadata(spec, execution_status="succeeded")
    bus = EventBus(execution_store=store)
    bus.publish(NodeStarted(run_id="run-new", node_id=node.key, label="compile", metadata=dict(spec.metadata)))
    bus.publish(NodeFinished(run_id="run-new", node_id=node.key, status="succeeded", metadata=terminal))
    record = store.snapshot("run-new").node(node.key).metadata["reusable_node_record"]
    assert record["schema_version"] == 2
    assert record["qualification_complete"] is True
    assert record["reusable"] is True
    assert record["result_identity"]
    assert record["fingerprint"]["node_definition_id"] == definition


def test_runner_marked_non_reusable_policy_is_explicit(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    spec = CommandSpec("deploy", ["deploy"], cwd=str(root), metadata={"workflow_inputs": ["tracked.txt"], "reuse_disabled": True, "reuse_disabled_reason": "external-side-effect"})
    fp, contribution, _ = fingerprint_for_spec(spec, repo_root=root)
    assert not fp.reusable
    assert contribution.environment.portability is PortabilityClass.NON_REUSABLE
    assert fp.reason == "external-side-effect"


def test_exo_terminal_record_propagates_upstream_result_identity(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    a = WorkflowNode(
        key="api.build.a", target="api", workflow="build", index=1,
        ref=WorkflowStepRef(raw="runner:python#build", kind="runner", name="python", action="build", step_id="a"),
    )
    b = WorkflowNode(
        key="api.build.b", target="api", workflow="build", index=2,
        ref=WorkflowStepRef(raw="runner:python#test", kind="runner", name="python", action="test", step_id="b"),
        depends_on=(a.key,),
    )
    graph = ExecutionGraph(target="api", workflow="build", nodes=(a, b))
    store = ExecutionStore(); store.plan_graph(graph, run_id="run-deps")
    bus = EventBus(execution_store=store)

    def finish(node: WorkflowNode) -> dict:
        definition = NodeDefinitionIdentity.from_workflow_node(node).value
        spec = CommandSpec(
            node.label, ["python", "-c", "pass"], cwd=str(root),
            metadata={"workflow_inputs": ["tracked.txt"], "workflow_depends_on": list(node.depends_on)},
        )
        annotate_spec_reuse(spec, repo_root=root, node_definition_id=definition)
        bus.publish(NodeStarted(run_id="run-deps", node_id=node.key, label=node.label, metadata=dict(spec.metadata)))
        bus.publish(NodeFinished(run_id="run-deps", node_id=node.key, status="succeeded", metadata=finalize_spec_reuse_metadata(spec, execution_status="succeeded")))
        return store.snapshot("run-deps").node(node.key).metadata["reusable_node_record"]

    first = finish(a)
    second = finish(b)
    assert first["result_identity"]
    assert second["qualification_complete"] is True
    assert second["reusable"] is True
    assert second["fingerprint"]["dependency_complete"] is True
    assert second["fingerprint"]["dependency_digest"]
    assert second["fingerprint"]["dependency_digest"] != dependency_fingerprint_from_metadata({"workflow_depends_on": [a.key]}).digest


def test_history_qualification_selects_compatible_candidate(tmp_path: Path) -> None:
    from devtool.core.reuse import qualify_from_history
    root = _git_repo(tmp_path / "repo")
    spec = CommandSpec("test", ["pytest"], cwd=str(root), metadata={"workflow_inputs": ["tracked.txt"]})
    fp, _, _ = fingerprint_for_spec(spec, repo_root=root)
    output = output_manifest_for_spec(spec).to_dict()
    index = ReuseHistoryIndex()
    wrong = dict(fp.to_dict()); wrong["environment_digest"] = "old-env"
    index.add(_candidate(wrong, output))
    index.add(_candidate(fp.to_dict(), output))
    result = qualify_from_history(index, fp.to_dict(), cwd=root)
    assert result.disposition == "reuse"
    assert result.source_run_id == "run-old"


def test_portability_class_controls_destination_axes_without_dropping_semantic_domains() -> None:
    from devtool.core.reuse import ExecutionEnvironmentFingerprint
    portable_a = ExecutionEnvironmentFingerprint(domains={"python": "3.14"}, execution_environment="native-termux", portability=PortabilityClass.PORTABLE)
    portable_b = ExecutionEnvironmentFingerprint(domains={"python": "3.14"}, execution_environment="desktop-linux", portability=PortabilityClass.PORTABLE)
    assert portable_a.digest == portable_b.digest
    changed_domain = ExecutionEnvironmentFingerprint(domains={"python": "3.13"}, execution_environment="desktop-linux", portability=PortabilityClass.PORTABLE)
    assert portable_a.digest != changed_domain.digest

    host_a = ExecutionEnvironmentFingerprint(domains={}, host_identity="host-a", portability=PortabilityClass.SAME_HOST)
    host_b = ExecutionEnvironmentFingerprint(domains={}, host_identity="host-b", portability=PortabilityClass.SAME_HOST)
    assert host_a.digest != host_b.digest

    provider_a = ExecutionEnvironmentFingerprint(domains={}, provider_identity="ssh:alpha", portability=PortabilityClass.SAME_PROVIDER)
    provider_b = ExecutionEnvironmentFingerprint(domains={}, provider_identity="ssh:beta", portability=PortabilityClass.SAME_PROVIDER)
    assert provider_a.digest != provider_b.digest


def test_run_specs_persists_reuse_proof_on_canonical_exo_node(monkeypatch, tmp_path: Path) -> None:
    from devtool.core.events import EventBus
    from devtool.models import ProjectConfig
    from devtool.runners import run_specs

    root = _git_repo(tmp_path / "repo")
    out = root / "out.txt"
    out.write_text("ok", encoding="utf-8")
    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    monkeypatch.setattr("devtool.runners._run_process_live", lambda *a, **kw: (0, "ok"))

    spec = CommandSpec(
        "build",
        ["echo", "ok"],
        cwd=str(root),
        metadata={
            "workflow_graph": True,
            "workflow_node_key": "app.build.compile",
            "workflow_node_spec_index": 1,
            "workflow_node_spec_total": 1,
            "workflow_inputs": ["tracked.txt"],
            "workflow_outputs": ["out.txt"],
        },
    )
    annotate_spec_reuse(spec, repo_root=root)
    results, _ = run_specs(ProjectConfig(repo_root=root, kind="generic"), [spec], verbose=False, event_run_id="reuse-run")
    assert results[0].metadata["reuse_result_identity"]
    node = bus.execution_store.snapshot("reuse-run").node("app.build.compile")
    record = node.metadata["reusable_node_record"]
    assert record["qualification_complete"] is True
    assert record["reusable"] is True
    assert record["fingerprint"]["digest"]
    assert record["output_manifest"]["entries"][0]["path"] == "out.txt"


def test_canonical_workflow_node_identity_is_preserved_across_multi_spec_parts(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    canonical = "workflow-node:compile@canonical"
    first = CommandSpec("configure", ["tool", "configure"], cwd=str(root), metadata={"node_definition_id": canonical, "workflow_inputs": ["tracked.txt"]})
    second = CommandSpec("compile", ["tool", "compile"], cwd=str(root), metadata={"node_definition_id": canonical, "workflow_inputs": ["tracked.txt"]})
    annotate_spec_reuse(first, repo_root=root)
    annotate_spec_reuse(second, repo_root=root)
    assert first.metadata["node_definition_id"] == canonical
    assert second.metadata["node_definition_id"] == canonical
    assert first.metadata["reuse_fingerprint"]["node_definition_id"] == canonical
    assert second.metadata["reuse_fingerprint"]["node_definition_id"] == canonical
    assert first.metadata["reuse_fingerprint"]["invocation_digest"] != second.metadata["reuse_fingerprint"]["invocation_digest"]


def test_multispec_exo_record_aggregates_all_required_outputs(monkeypatch, tmp_path: Path) -> None:
    from devtool.core.events import EventBus
    from devtool.models import ProjectConfig
    from devtool.runners import run_specs

    root = _git_repo(tmp_path / "repo")
    (root / "one.bin").write_bytes(b"one")
    (root / "two.bin").write_bytes(b"two")
    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    monkeypatch.setattr("devtool.runners._run_process_live", lambda *a, **kw: (0, "ok"))
    canonical = "workflow-node:compile@canonical"
    common = {
        "workflow_graph": True,
        "workflow_node_key": "app.build.compile",
        "workflow_node_spec_total": 2,
        "workflow_inputs": ["tracked.txt"],
        "node_definition_id": canonical,
    }
    specs = [
        CommandSpec("configure", ["echo", "one"], cwd=str(root), metadata={**common, "workflow_node_spec_index": 1, "workflow_outputs": ["one.bin"]}),
        CommandSpec("compile", ["echo", "two"], cwd=str(root), metadata={**common, "workflow_node_spec_index": 2, "workflow_outputs": ["two.bin"]}),
    ]
    for spec in specs:
        annotate_spec_reuse(spec, repo_root=root)
    run_specs(ProjectConfig(repo_root=root, kind="generic"), specs, verbose=False, event_run_id="multi-reuse")
    record = bus.execution_store.snapshot("multi-reuse").node("app.build.compile").metadata["reusable_node_record"]
    assert record["qualification_complete"] is True
    paths = {row["path"] for row in record["output_manifest"]["entries"]}
    assert paths == {"one.bin", "two.bin"}
    assert len(record["spec_fingerprint_digests"]) == 2


def test_missing_declared_output_never_creates_reusable_terminal_record(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    node = WorkflowNode(
        key="api.build.missing", target="api", workflow="build", index=1,
        ref=WorkflowStepRef(raw="runner:python#build", kind="runner", name="python", action="build", step_id="missing"),
    )
    graph = ExecutionGraph(target="api", workflow="build", nodes=(node,))
    store = ExecutionStore(); store.plan_graph(graph, run_id="run-missing")
    definition = NodeDefinitionIdentity.from_workflow_node(node).value
    spec = CommandSpec("build", ["python", "-c", "pass"], cwd=str(root), metadata={"workflow_inputs": ["tracked.txt"], "workflow_outputs": ["missing.bin"]})
    annotate_spec_reuse(spec, repo_root=root, node_definition_id=definition)
    bus = EventBus(execution_store=store)
    bus.publish(NodeStarted(run_id="run-missing", node_id=node.key, label="missing", metadata=dict(spec.metadata)))
    bus.publish(NodeFinished(run_id="run-missing", node_id=node.key, status="succeeded", metadata=finalize_spec_reuse_metadata(spec, execution_status="succeeded")))
    record = store.snapshot("run-missing").node(node.key).metadata["reusable_node_record"]
    assert record["qualification_complete"] is False
    assert record["reusable"] is False
    assert record["output_manifest"]["complete"] is False


def test_corrupt_claimed_reuse_candidate_fails_safe_to_run(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    spec = CommandSpec("test", ["pytest"], cwd=str(root), metadata={"workflow_inputs": ["tracked.txt"]})
    fp, _, _ = fingerprint_for_spec(spec, repo_root=root)
    corrupt = {
        "schema_version": 2,
        "node_definition_id": fp.node_definition_id,
        "source_run_id": "run-old",
        "source_node_id": "node-old",
        "execution_status": "succeeded",
        "qualification_complete": True,
        "reusable": True,
        "fingerprint": {"digest": fp.digest},
        "output_manifest": {"digest": "out", "entries": []},
        "result_identity": "result",
    }
    result = qualify_candidate(fp.to_dict(), corrupt, cwd=root)
    assert result.disposition == "run"
    assert "incomplete-fingerprint" in result.reason

    incomplete_current = {"node_definition_id": fp.node_definition_id, "digest": fp.digest}
    result = qualify_candidate(incomplete_current, _candidate(fp.to_dict(), output_manifest_for_spec(spec).to_dict()), cwd=root)
    assert result.disposition == "run"
    assert result.reason == "current-fingerprint-incomplete"
