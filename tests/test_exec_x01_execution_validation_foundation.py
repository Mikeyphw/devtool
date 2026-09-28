from __future__ import annotations

from pathlib import Path

import pytest

from devtool.cli import (
    _apply_runner_cli_options,
    _canonical_validation_run_request,
    _normalize_command_aliases,
    _validate_apply_args,
    _validate_validation_api_request,
    build_parser,
)
from devtool.core.events import EventBus, NodeFinished, NodeStarted
from devtool.core.execution_semantics import (
    ApplicabilityPolicy,
    ExecutionIntent,
    ExecutionRoot,
    ExecutionRootKind,
    NodeDefinitionIdentity,
    ResolvedValidationContract,
    ReusableNodeRecord,
    ReuseDisposition,
    ToolInvocation,
    ValidationCheck,
    ValidationControlAction,
    ValidationControlIntent,
    ValidationOutcome,
    ValidationOutcomeState,
    ValidationRunRequest,
    ValidationVerdictStatus,
    canonical_validation_check_identity,
    resolved_validation_contract_from_specs,
)
from devtool.core.execution_store import ExecutionStore
from devtool.models import CommandSpec, OperationResult, ProjectConfig, WorkflowStepRef
from devtool.test_selection import resolve_test_selection
from devtool.validation_control import ValidationControlError
from devtool.validation_sessions import create_session, phase_name_for_spec, record_phase_result
from devtool.workflow_graph import ExecutionGraph, WorkflowNode


def _config(root: Path, *, kind: str = "python") -> ProjectConfig:
    root.mkdir(parents=True, exist_ok=True)
    return ProjectConfig(repo_root=root, project_root=str(root), kind=kind)


def test_node_definition_identity_is_stable_across_run_ids_and_changes_with_semantics() -> None:
    node = WorkflowNode(
        key="api.build.compile",
        target="api",
        workflow="build",
        index=1,
        ref=WorkflowStepRef(raw="runner:python#build", kind="runner", name="python", action="build", step_id="compile"),
    )
    first = NodeDefinitionIdentity.from_workflow_node(node)
    second = NodeDefinitionIdentity.from_workflow_node(node)
    changed = NodeDefinitionIdentity.from_workflow_node(
        WorkflowNode(
            key=node.key,
            target="api",
            workflow="build",
            index=1,
            ref=WorkflowStepRef(raw="runner:python#package", kind="runner", name="python", action="package", step_id="compile"),
        )
    )
    assert first.value == second.value
    assert first.value != changed.value
    assert "run_id" not in first.payload


def test_exo_graph_records_shadow_reuse_identity_without_skipping_execution() -> None:
    node = WorkflowNode(
        key="api.build.compile",
        target="api",
        workflow="build",
        index=1,
        ref=WorkflowStepRef(raw="runner:python#build", kind="runner", name="python", action="build", step_id="compile"),
    )
    graph = ExecutionGraph(target="api", workflow="build", nodes=(node,))
    store = ExecutionStore()
    snapshot = store.plan_graph(graph, run_id="run-a")
    planned = snapshot.node(node.key)
    assert planned.metadata["node_definition_id"]
    assert planned.metadata["reuse_prediction"]["mode"] == "shadow"
    assert planned.metadata["reuse_prediction"]["predicted_disposition"] == "run"
    assert planned.status == "planned"

    bus = EventBus(execution_store=store)
    bus.publish(NodeStarted(run_id="run-a", node_id=node.key, label="compile"))
    bus.publish(NodeFinished(run_id="run-a", node_id=node.key, status="succeeded", evidence=("evidence:1",)))
    terminal = store.snapshot("run-a").node(node.key)
    record = terminal.metadata["reusable_node_record"]
    assert record["source_run_id"] == "run-a"
    assert record["source_node_id"] == node.key
    assert record["reusable"] is False
    assert record["qualification_complete"] is False
    assert record["disposition"] == "run"
    intent = planned.metadata["execution_intent"]
    assert intent["operation_kind"] == "action"
    assert intent["canonical_ref"] == "runner:python#build"


def test_execution_intent_distinguishes_job_test_validation_and_delivery_contexts() -> None:
    def intent(kind: str, *, action: str = "", raw: str = "") -> ExecutionIntent:
        node = WorkflowNode(
            key=f"api.workflow.{kind}.{action or 'default'}",
            target="api",
            workflow="release",
            index=1,
            ref=WorkflowStepRef(
                raw=raw or f"{kind}:sample" if kind != "runner" else f"runner:python#{action}",
                kind=kind,
                name="sample" if kind != "runner" else "python",
                action=action,
                step_id="step",
            ),
        )
        return ExecutionIntent.from_workflow_node(node)

    assert intent("job").operation_kind == "job"
    assert intent("test").operation_kind == "test"
    assert intent("runner", action="validate").operation_kind == "validation"
    assert intent("runner", action="package").operation_kind == "delivery"


def test_invoke_and_future_run_lower_to_equal_execution_intent() -> None:
    node = WorkflowNode(
        key="api.build.compile",
        target="api",
        workflow="build",
        index=1,
        ref=WorkflowStepRef(raw="runner:python#build", kind="runner", name="python", action="build", step_id="compile"),
    )
    invoke = ExecutionIntent.from_workflow_node(node, entrypoint="invoke")
    future_run = ExecutionIntent.from_workflow_node(node, entrypoint="run")
    assert invoke.digest == future_run.digest
    assert invoke.to_dict() == future_run.to_dict()


def test_legacy_or_corrupt_reuse_metadata_fails_safe_to_run() -> None:
    old = ReusableNodeRecord.from_history_payload({
        "schema_version": 0,
        "node_definition_id": "node:a",
        "source_run_id": "run-old",
        "source_node_id": "node-old",
        "reusable": True,
        "disposition": "reuse",
    })
    corrupt = ReusableNodeRecord.from_history_payload({"schema_version": "broken", "reusable": True})
    for record in (old, corrupt):
        assert record.disposition is ReuseDisposition.RUN
        assert record.reusable is False
        assert record.qualification_complete is False


def test_legacy_app_run_and_launch_share_canonical_action_identity() -> None:
    legacy = ExecutionIntent.from_app_action("com.example.app", "run", target="android")
    canonical = ExecutionIntent.from_app_action("com.example.app", "launch", target="android")
    assert legacy.canonical_ref == canonical.canonical_ref == "android-app:com.example.app#launch"
    # Provenance is intentionally non-semantic for this migration seam.
    assert legacy.operation_kind == canonical.operation_kind == "action"


def test_validation_check_identity_is_distinct_from_execution_root_even_for_same_command() -> None:
    root = ExecutionRoot(ExecutionRootKind.NATIVE_TASK, "lint")
    check = ValidationCheck("repository:lint:1", "lint")
    assert root.to_dict()["value"] == "lint"
    assert check.check_id != root.to_dict()["value"]


def test_reuse_dispositions_are_distinct_canonical_values() -> None:
    assert {item.value for item in ReuseDisposition} == {
        "reuse", "materialize", "run", "invalidated", "blocked"
    }


def test_tool_invocation_identity_separates_managed_raw_and_protected_argv() -> None:
    base = ToolInvocation(managed_argv=("pytest", "-q"), raw_passthrough_argv=("tests/unit",))
    changed_raw = ToolInvocation(managed_argv=("pytest", "-q"), raw_passthrough_argv=("tests/integration",))
    protected = ToolInvocation(
        managed_argv=("deploy",),
        protected_argv_identity=("secret:sha256:abc",),
    )
    assert base.digest != changed_raw.digest
    assert protected.to_dict()["protected_argv_identity"] == ["secret:sha256:abc"]
    assert "password" not in str(protected.to_dict()).lower()


def test_validation_request_semantic_identity_ignores_output_presentation() -> None:
    root = ExecutionRoot(ExecutionRootKind.TARGET, "devtool", target="devtool", runner="python")
    control = ValidationControlIntent(ValidationControlAction.RESUME, scope="remaining")
    text = ValidationRunRequest(action="resume", execution_roots=(root,), control=control, output_format="text")
    machine = ValidationRunRequest(action="resume", execution_roots=(root,), control=control, output_format="json", verbosity="verbose", copy_output=True)
    assert text.semantic_digest == machine.semantic_digest
    assert text.to_dict()["presentation"] != machine.to_dict()["presentation"]


def test_cli_resume_scope_is_output_format_invariant(tmp_path: Path) -> None:
    parser = build_parser()
    config = _config(tmp_path)
    requests = []
    for argv in (["validate", "resume"], ["validate", "resume", "--json"]):
        args = parser.parse_args(argv)
        _normalize_command_aliases(args)
        requests.append(_canonical_validation_run_request(config, args))
    assert requests[0].control is not None
    assert requests[0].control.scope == "remaining"
    assert requests[1].control.scope == "remaining"
    assert requests[0].semantic_digest == requests[1].semantic_digest


def test_invalid_control_scope_is_rejected_for_text_and_json() -> None:
    for argv in (
        ["validate", "run", "--scope", "remaining", "--phase", "compile"],
        ["validate", "run", "--scope", "remaining", "--phase", "compile", "--json"],
    ):
        parser = build_parser()
        args = parser.parse_args(argv)
        _normalize_command_aliases(args)
        _validate_apply_args(args, parser)
        with pytest.raises(ValidationControlError) as exc_info:
            _validate_validation_api_request(args)
        assert exc_info.value.reason == "invalid_request"


def test_validation_control_intent_carries_if_revision(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["validate", "run", "--scope", "next", "--if-revision", "7"])
    _normalize_command_aliases(args)
    request = _canonical_validation_run_request(_config(tmp_path), args)
    assert request.control is not None
    assert request.control.expected_revision == 7


def test_validate_task_is_execution_root_not_test_selection(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["validate", "run", "--task", "lint"])
    args._devtool_cli_test_task_explicit = True
    _normalize_command_aliases(args)
    config = _config(tmp_path)
    _apply_runner_cli_options(config, args)
    selection = resolve_test_selection(config)
    assert selection.tasks == []
    request = _canonical_validation_run_request(config, args)
    assert [root.kind for root in request.execution_roots] == [ExecutionRootKind.NATIVE_TASK]
    assert request.execution_roots[0].value == "lint"
    assert request.execution_roots[0].provenance == "legacy-validate-task-compat"


def test_canonical_check_identity_rejects_untyped_new_checks_and_legacy_path_records_provenance(tmp_path: Path) -> None:
    spec = CommandSpec("mystery-test-command", ["true"], metadata={})
    with pytest.raises(ValueError, match="requires validation_check_id"):
        canonical_validation_check_identity(spec)
    check_id, provenance = canonical_validation_check_identity(spec, allow_legacy=True)
    assert check_id.endswith("spec:mystery-test-command")
    assert provenance == "legacy-exact-spec-name"
    # Legacy session compatibility may still infer the old phase, but records it.
    assert phase_name_for_spec(spec) == "unit-test"
    assert spec.metadata["validation_identity_provenance"] == "legacy-command-name-inference"

    session = create_session(_config(tmp_path), [spec], persist=False)
    assert session.phases[0].validation_identity_provenance == "legacy-command-name-inference"
    assert session.resolved_validation_contract["checks"][0]["identity_provenance"] == "legacy-command-name-inference"


def test_synthetic_finalize_is_not_part_of_canonical_validation_contract() -> None:
    specs = [
        CommandSpec("compile", ["true"], metadata={"validation_phase_name": "compile", "validation_check_id": "compile"}),
        CommandSpec(
            "finalize",
            ["true"],
            metadata={
                "validation_phase_name": "finalize",
                "validation_check_id": "synthetic-finalize",
                "validation_administrative": True,
                "validation_required": False,
            },
        ),
    ]
    contract = resolved_validation_contract_from_specs(specs)
    assert [check.check_id for check in contract.checks] == ["compile"]
    assert contract.checkpoint_policy.required_check_ids == ("compile",)


def test_empty_and_optional_only_required_sets_are_vacuously_satisfied() -> None:
    empty = ResolvedValidationContract(checks=(), checkpoint_policy=__import__(
        "devtool.core.execution_semantics", fromlist=["CheckpointPolicy"]
    ).CheckpointPolicy())
    assert empty.evaluate(()).status is ValidationVerdictStatus.SATISFIED

    optional = ResolvedValidationContract(
        checks=(ValidationCheck("lint", "lint", required=False),),
        checkpoint_policy=__import__("devtool.core.execution_semantics", fromlist=["CheckpointPolicy"]).CheckpointPolicy(),
    )
    assert optional.evaluate(()).status is ValidationVerdictStatus.SATISFIED


def test_new_session_completion_uses_canonical_validation_verdict(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = CommandSpec("check", ["true"], metadata={"validation_phase_name": "check", "validation_check_id": "check"})
    session = create_session(config, [spec], persist=False)
    session.phases[0].status = "passed"
    # A schema-v7 session must not bypass the semantic verdict merely because
    # legacy phase status looks successful.
    session.validation_verdict = {"status": "unsatisfied", "satisfied": False}
    assert session.complete is False
    session.validation_verdict = {"status": "satisfied", "satisfied": True}
    assert session.complete is True


def test_optional_only_session_waits_for_selected_optional_check_to_finish(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = CommandSpec(
        "lint",
        ["true"],
        metadata={
            "validation_phase_name": "lint",
            "validation_check_id": "lint",
            "validation_required": False,
        },
    )
    session = create_session(config, [spec], persist=False)
    assert session.validation_verdict["status"] == "satisfied"
    assert session.complete is False
    record_phase_result(session, session.phases[0].key, OperationResult("lint", ["true"], 0, 0.0, "", []))
    assert session.complete is True
    assert session.validation_verdict["status"] == "satisfied"


def test_generic_skip_does_not_satisfy_required_validation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = CommandSpec("check", ["true"], metadata={"validation_phase_name": "check", "validation_check_id": "check"})
    session = create_session(config, [spec], persist=False)
    result = OperationResult("check", ["true"], 0, 0.0, "", [], skipped=True)
    record_phase_result(session, session.phases[0].key, result)
    assert session.phases[0].status == "skipped"
    assert session.complete is False
    assert session.validation_verdict["status"] == "unsatisfied"


def test_explicit_applicability_evidence_can_satisfy_conditional_required_check(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = CommandSpec(
        "device",
        ["true"],
        metadata={
            "validation_phase_name": "device",
            "validation_check_id": "device",
            "validation_applicability_policy": "conditional",
        },
    )
    session = create_session(config, [spec], persist=False)
    result = OperationResult(
        "device",
        ["true"],
        0,
        0.0,
        "",
        [],
        skipped=True,
        metadata={
            "validation_applicability": "not-applicable",
            "validation_applicability_evidence": ["policy:no-device-required"],
        },
    )
    record_phase_result(session, session.phases[0].key, result)
    assert session.phases[0].status == "not-applicable"
    assert session.complete is True
    assert session.validation_verdict["status"] == "satisfied"


def test_session_persists_typed_request_contract_and_verdict(tmp_path: Path) -> None:
    config = _config(tmp_path)
    spec = CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint", "validation_check_id": "lint"})
    request = ValidationRunRequest(
        action="run",
        execution_roots=(ExecutionRoot(ExecutionRootKind.REPOSITORY, str(tmp_path)),),
        control=ValidationControlIntent(ValidationControlAction.RUN, scope="next"),
    )
    session = create_session(config, [spec], options={"validation_run_request": request.to_dict()}, persist=False)
    assert session.validation_run_request["semantic_digest"] == request.semantic_digest
    assert session.resolved_validation_contract["digest"]
    assert session.validation_verdict["status"] == "unsatisfied"
