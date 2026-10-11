import dataclasses
import pytest
from devtool.federation_execution import FederatedRunReport
from devtool.federation_plan import build_federated_plan
from devtool.federation_resume import (FederationResumeError, checkpoint_report, child_from_checkpoint, build_resume_plan)
from test_frx_y02_federation_resume_checkpoint import _fixture, _children, _state

@pytest.fixture
def sample(tmp_path, monkeypatch):
    _state(monkeypatch, tmp_path)
    config, _, _ = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    return config, plan, _children(config, plan)

def test_duplicate_child_producer_is_rejected_before_checkpoint_is_written(sample):
    config, plan, children = sample
    with pytest.raises(FederationResumeError, match="duplicate"):
        checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, children + (children[0],), plan))

def test_unknown_child_producer_is_rejected(sample):
    config, plan, children = sample
    forged = dataclasses.replace(children[0], step_id="unknown", run_id="child-unknown")
    with pytest.raises(FederationResumeError, match="unknown"):
        checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, children + (forged,), plan))

def test_checkpoint_artifact_identity_recomputed_from_ownership(sample):
    config, plan, children = sample
    checkpoint = checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, children, plan))
    row = checkpoint["children"][0]
    assert child_from_checkpoint(row).run_id == row["run_id"]
    row["artifact_identity"] = "0" * 64
    from devtool.federation_resume import _digest
    row["result_identity"] = _digest({
       "repository": row["repository"], "target": row["target"], "workflow": row["workflow"],
       "child_contract": row["child_contract"], "contract_ref": row["contract_ref"],
       "status": row["status"], "exit_code": row["exit_code"],
       "repository_fingerprint": row["repository_identity"]["fingerprint"],
       "result_commit": str(row["git_lineage"].get("result_commit") or ""),
       "result_ref": str(row["git_lineage"].get("result_ref") or ""),
       "artifact_identity": row["artifact_identity"],
       "consumed_artifact_inputs": __import__("devtool.federation_artifacts",fromlist=["artifact_edges_identity"]).artifact_edges_identity(row["artifact_inputs"]),
    })
    with pytest.raises(FederationResumeError, match="unverified"):
        child_from_checkpoint(row)
    assert build_resume_plan(config, checkpoint, mode="resume").decisions[0].action != "reused"
