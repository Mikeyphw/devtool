"""R39-R2: bounded real-subprocess legacy/native serial equivalence evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

from devtool.workflow_parity import (
    CASES, WorkflowParityError, _execute, _specs, compare_evidence, main,
    matrix, verify_matrix,
)


@pytest.fixture(scope="module")
def receipt():
    return matrix()


def test_all_five_serial_real_subprocess_cases_match(receipt):
    assert receipt["schema_version"] == 1
    assert receipt["matrix"] == "r39-r2-legacy-native-bounded-serial-subprocess"
    assert [case["name"] for case in receipt["cases"]] == list(CASES)
    assert all(case["equivalent"] and case["mismatches"] == [] for case in receipt["cases"])
    verify_matrix(receipt)


@pytest.mark.parametrize("case,states,launches", [
    ("pass_chain", ["passed", "passed"], {"a", "b"}),
    ("dependency_block", ["failed", "blocked"], {"a"}),
    ("independent_fail_fast", ["failed", "skipped"], {"a"}),
    ("continue_on_error", ["failed", "passed"], {"a", "b"}),
    ("pre_cancelled", ["cancelled", "cancelled"], set()),
])
def test_terminal_outcomes_side_effects_and_event_sequence(receipt, case, states, launches):
    row = next(x for x in receipt["cases"] if x["name"] == case)
    for lane in ("legacy", "native"):
        payload = row[lane]
        assert [x["node_state"] for x in payload["outcomes"]] == states
        assert set(payload["executed_side_effects"]) == launches
        assert [x["type"] for x in payload["events"]] == [
            "NodeDiscovered", "NodeDiscovered", "TaskStarted", "TaskFinished",
            "TaskStarted", "TaskFinished",
        ]
        assert [x["node"] for x in payload["events"]] == ["a", "b", "a", "a", "b", "b"]


def test_detects_changes_to_returncode_output_and_terminal_event(receipt):
    original = receipt["cases"][0]["legacy"]
    for group, key, alternate in (
        ("outcomes", "returncode", 9),
        ("outcomes", "output", "tampered"),
        ("events", "status", "blocked"),
    ):
        changed = deepcopy(original)
        changed[group][-1][key] = alternate
        assert group in compare_evidence(original, changed)


def test_detects_missing_or_extra_process_side_effect(receipt):
    original = receipt["cases"][0]["legacy"]
    changed = deepcopy(original)
    changed["executed_side_effects"].pop("a")
    assert compare_evidence(original, changed) == ["executed_side_effects"]


def test_missing_lane_or_case_refuses_qualification(receipt):
    truncated = deepcopy(receipt)
    truncated["cases"].pop()
    with pytest.raises(WorkflowParityError, match="INCOMPLETE_MATRIX"):
        verify_matrix(truncated)
    missing_lane = deepcopy(receipt)
    missing_lane["cases"][0].pop("native")
    with pytest.raises(WorkflowParityError, match="MISSING_LANE"):
        verify_matrix(missing_lane)


def test_any_divergence_fails_closed_even_if_claim_says_pass(receipt):
    falsified = deepcopy(receipt)
    falsified["cases"][0]["native"]["outcomes"][0]["returncode"] = 13
    with pytest.raises(WorkflowParityError, match="MISMATCH"):
        verify_matrix(falsified)


def test_unqualified_domains_cannot_be_promoted(receipt):
    for key in ("parallel_worker_parity", "remote_or_android_parity", "device_acceptance",
                "historical_reuse_parity", "release", "legacy_retirement_authorized"):
        changed = deepcopy(receipt)
        changed["qualification"][key] = True
        with pytest.raises(WorkflowParityError, match="UNSAFE_QUALIFICATION"):
            verify_matrix(changed)


def test_caller_environment_preserved_and_source_repo_unmodified(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "original-state"))
    report = matrix(source_root=tmp_path)
    assert report["source"] == {"commit": "", "tree": ""}
    assert Path(os.environ["XDG_STATE_HOME"]) == tmp_path / "original-state"
    assert not (tmp_path / "original-state").exists()
    assert not (tmp_path / "spawned-a.txt").exists()
    assert not (tmp_path / "spawned-b.txt").exists()


def test_case_and_lane_whitelist_is_closed(tmp_path):
    with pytest.raises(WorkflowParityError, match="UNKNOWN_CASE"):
        _specs(tmp_path, "external-command")
    with pytest.raises(WorkflowParityError, match="UNKNOWN_LANE"):
        _execute(tmp_path, lane="arbitrary", case="pass_chain")
    specs = _specs(tmp_path, "pass_chain")
    assert all(x.argv[0] == sys.executable and x.argv[1] == "-c" for x in specs)


def test_cli_produces_valid_json_and_honest_non_release_receipt(capsys, monkeypatch, receipt):
    monkeypatch.setattr("devtool.workflow_parity.matrix", lambda: receipt)
    assert main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    verify_matrix(data)
    assert data["qualification"]["legacy_retirement_authorized"] is False


def test_hosted_workflow_is_manual_pinned_to_source_and_node24():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/devtool-r39-execution-parity.yml").read_text()
    assert "workflow_dispatch:" in source
    for action in ("actions/checkout@v6", "actions/setup-python@v6", "actions/upload-artifact@v7"):
        assert action in source
    assert "python -m devtool.workflow_parity --json" in source
    assert "verify_matrix" in source
    assert "github.sha" in source
    assert "legacy_retirement_authorized" in source
    assert "pull_request:" not in source
