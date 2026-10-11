from __future__ import annotations
from copy import deepcopy
import pytest
from devtool import test_runstore

def valid_receipt(monkeypatch,tmp_path):
    monkeypatch.setattr(test_runstore,"_receipt_root",lambda:tmp_path/"receipts")
    repository=tmp_path/"project";repository.mkdir()
    path,payload=test_runstore.write_canonical_test_run_receipt(repository,{
       "run_id":"run-1", "operations":[{"operation_id":"test-1","test_result":{"id":"result-1","suites":[]}}]
    })
    assert path.exists() and test_runstore.valid_canonical_test_run_receipt(payload)
    return payload

def test_producer_receipt_still_valid(monkeypatch,tmp_path):
    valid_receipt(monkeypatch,tmp_path)

@pytest.mark.parametrize("changed,value", [
    ("schema_version",2), ("schema_version",True), ("receipt_id","trr-deadbeef"),
    ("repo_key","0"*24), ("repository","/different/repository"),
    ("run_id","../../bad"), ("operations",[]), ("operations",[{"operation_id":"ok"},"not-object"]),
    ("selection_refs",{}), ("environment_refs",None), ("authority","other"),
])
def test_resigned_inconsistent_receipt_rejected(monkeypatch,tmp_path,changed,value):
    receipt=deepcopy(valid_receipt(monkeypatch,tmp_path));receipt[changed]=value
    # Self-consistent outer hash alone must never authenticate inner identity.
    receipt["sha256"]=test_runstore._sha256_json(test_runstore._receipt_digest_payload(receipt))
    assert not test_runstore.valid_canonical_test_run_receipt(receipt)
