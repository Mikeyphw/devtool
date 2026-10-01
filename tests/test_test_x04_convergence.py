from datetime import datetime, timezone

import pytest

from devtool.test_convergence import (
    DelegatedTestLineage,
    GeneratedSurfaceReceipt,
    HistoricalEvidenceFixture,
    HistoricalTestAlias,
    QuarantineRecord,
    RerTestEvidenceRequest,
    ShuffleRepeatDiagnostic,
    dogfood_lane_plan,
    generated_surface_projection,
    historical_cleanup_projection,
    obligation_disposition,
    presentation_projection,
    rer_reuse_projection,
    repo_local_report_projection,
    roadmap_evidence_binding,
)


def _alias_fixture():
    alias = HistoricalTestAlias(
        "legacy-final-seal/generated-cli",
        "test.generated.cli-contract",
        "generated CLI/help/completion synchronization",
        "TEST-R04",
        ("run://old/evidence/generated-cli",),
    )
    fixture = HistoricalEvidenceFixture(
        "hist-generated-cli-v1",
        alias.check_id,
        "run-old",
        ("run://old/attachment/junit", "run://old/attachment/log"),
    )
    return alias, fixture


def test_historical_cleanup_preserves_behavior_alias_and_run_history_authority():
    alias, fixture = _alias_fixture()
    projection = historical_cleanup_projection((alias,), (fixture,), retired_paths=("tests/legacy/test_generated_cli_bytes.py",))
    assert projection["aliases"][0]["check_id"] == alias.check_id
    assert projection["fixtures"][0]["source_run_id"] == "run-old"
    assert projection["canonical_history_authority"] == "RUN-V3"
    assert projection["repo_local_history_authoritative"] is False


def test_historical_fixture_must_reference_known_alias_check():
    alias, fixture = _alias_fixture()
    wrong = HistoricalEvidenceFixture("bad", "unknown.check", "run-old", ("run://evidence",))
    with pytest.raises(ValueError, match="unknown check"):
        historical_cleanup_projection((alias,), (fixture, wrong))


def test_generated_surface_receipt_links_dependents_without_hiding_raw_results():
    receipt = GeneratedSurfaceReceipt(
        "gsr-1", "test.generated.cli-contract", "src-a", "gen-b",
        ("test.seal.help", "test.seal.completion"), "run://r/attachment/generated-receipt",
    )
    raw = {
        "test.generated.cli-contract": "failed",
        "test.seal.help": "failed",
        "test.seal.completion": "failed",
    }
    projection = generated_surface_projection(receipt, raw)
    assert projection["root_check_id"] == "test.generated.cli-contract"
    assert projection["cluster_annotation_only"] is True
    assert projection["raw_outcomes"] == raw


def test_generated_surface_projection_fails_closed_if_dependent_outcome_missing():
    receipt = GeneratedSurfaceReceipt("gsr", "root", "a", "b", ("dependent",))
    with pytest.raises(ValueError, match="dependent raw outcome"):
        generated_surface_projection(receipt, {"root": "failed"})


def test_rer_reuse_requires_full_test_input_environment_output_policy_freshness_identity():
    request = RerTestEvidenceRequest("check", "sel", "input", "env", "output", "policy", "fresh", "source-run")
    reused = rer_reuse_projection(request, qualified=True, reason="canonical RER proof matched")
    assert reused["authority"] == "RER"
    assert reused["disposition"] == "REUSED"
    assert reused["fabricate_live_events"] is False
    assert reused["test_owns_reuse_decision"] is False
    with pytest.raises(ValueError):
        RerTestEvidenceRequest("check", "sel", "", "env", "output", "policy", "fresh")


def test_unqualified_historical_pass_projects_run_not_private_test_reuse():
    request = RerTestEvidenceRequest("check", "sel", "input", "env", "output", "policy", "fresh", "old-pass")
    projection = rer_reuse_projection(request, qualified=False, reason="input digest changed")
    assert projection["disposition"] == "RUN"
    assert projection["qualified"] is False


def test_dist_lineage_preserves_known_child_failure_when_transport_degrades():
    lineage = DelegatedTestLineage("parent", "child", ("check.a",), "ssh", "degraded", "failed", "run://child/receipt")
    assert lineage.effective_test_status == "failed"
    assert lineage.as_dict()["transport_overwrites_test_truth"] is False
    assert lineage.as_dict()["authority"] == "DIST/EXO/RUN"


def test_shuffle_repeat_is_diagnostic_not_correctness_retry():
    row = ShuffleRepeatDiagnostic("sel", seed=90210, repetition=2, total_repetitions=5, original_failure_ref="run://r/failure/1").as_dict()
    assert row["diagnostic_only"] is True
    assert row["retry_semantics"] is False
    assert row["seed"] == 90210 and row["repetition"] == 2


def test_quarantine_requires_owner_reason_expiry_and_never_converts_failure_to_pass():
    record = QuarantineRecord("check.flaky", "mikey", "tracked flake", "2030-01-01T00:00:00Z", "issue://123")
    row = record.disposition("failed", now=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert row["quarantined"] is True
    assert row["final_status"] == "failed"
    assert row["truth_preserved"] is True
    expired = record.disposition("failed", now=datetime(2031, 1, 1, tzinfo=timezone.utc))
    assert expired["quarantined"] is False
    with pytest.raises(ValueError):
        QuarantineRecord("check", "", "why", "2030-01-01T00:00:00Z")


def test_presentation_projection_is_structured_and_keeps_raw_results_queryable():
    projection = presentation_projection(
        {"a": "failed", "b": "passed"},
        failure_clusters=({"cluster_id": "fc-1", "members": ["a"]},),
        profile_ref="run://r/attachment/profile",
        raw_result_refs={"a": "run://r/result/a", "b": "run://r/result/b"},
    )
    assert projection["counts"] == {"failed": 1, "passed": 1}
    assert projection["raw_outcomes_preserved"] is True
    assert projection["presentation_owns_test_truth"] is False
    assert projection["authority"] == "TEST-facts/PRES-rendering"


def test_roadmap_binding_uses_stable_check_ids_and_receipts_not_pytest_paths():
    row = roadmap_evidence_binding(gate_id="CORE-G3", check_ids=("test.core.identity", "test.core.run-store"), receipt_refs=("run://r/test-receipt",), rer_refs=("rer://proof",))
    assert row["pytest_paths"] == []
    assert row["check_ids"] == ["test.core.identity", "test.core.run-store"]
    with pytest.raises(ValueError, match="stable check IDs"):
        roadmap_evidence_binding(gate_id="bad", check_ids=("tests/test_core.py",), receipt_refs=("run://r/receipt",))


def test_dogfood_lane_plan_keeps_selection_execution_delegation_and_gate_authorities_distinct():
    plan = dogfood_lane_plan(fast_group="test.fast", system_group="test.system", full_group="test.full", delegated_group="test.delegated")
    assert plan["selection_authority"] == "TEST"
    assert plan["execution_authority"] == "EXO"
    assert plan["delegation_authority"] == "DIST"
    assert plan["gate_policy_authority"] == "RMP"


def test_obligation_disposition_is_final_only_with_evidence_and_no_duplicates():
    rows = [
        {"id": "TEST-P085", "status": "SATISFIED", "evidence": "tests/test_test_x04_convergence.py"},
        {"id": "TEST-P091", "status": "EXPORTED", "evidence": "DIST-X04"},
        {"id": "TEST-P104", "status": "EXPORTED", "evidence": "TEST-G3"},
    ]
    assert obligation_disposition(rows)["complete"] is True
    pending = obligation_disposition(({"id": "x", "status": "PRESERVED_PENDING_IMPLEMENTATION", "evidence": "ledger"},))
    assert pending["complete"] is False and pending["unresolved"] == ["x"]
    dup = obligation_disposition((rows[0], rows[0]))
    assert dup["complete"] is False and dup["duplicates"] == ["TEST-P085"]


def test_repo_local_junit_is_replaceable_projection_of_run_v3_history():
    row = repo_local_report_projection(run_id="run-1", junit_ref="run://run-1/attachment/junit", local_path=".devtool/reports/junit.xml")
    assert row["canonical_history_authority"] == "RUN-V3"
    assert row["local_projection_replaceable"] is True
