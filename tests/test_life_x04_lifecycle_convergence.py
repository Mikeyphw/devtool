from __future__ import annotations
import pytest
from devtool.lifecycle_convergence import *


def test_transaction_projection_fails_closed_without_post_mutation_checkpoint():
    p = transaction_projection({"id":"tx1","mutation_started":True,"changed_paths":["a"]})
    assert p.status == "blocked" and not p.facts["rollback_ready"]
    q = transaction_projection({"id":"tx1","mutation_started":True,"changed_paths":["a"],"checkpoints":["cp1"],"resume_target_identity":"repo@base"})
    assert q.status == "recoverable" and q.facts["recovery_identity"] == "repo@base"


def test_delivery_projection_preserves_qualification_truth():
    p = delivery_projection({"artifact":{"artifact_id":"a1","assurance":"strict","identities":{"semantic_sha256":"abc"}},"qualification":{"qualified":True,"receipt_id":"r1"},"executable":True})
    assert p.status == "qualified" and p.facts["receipt_id"] == "r1"
    assert delivery_projection({"artifact":{"artifact_id":"a1"},"qualification":{"qualified":False}}).status == "blocked"


def test_signing_projection_never_accepts_secret_material():
    p = signing_projection({"identity_id":"release","provider":"android","key_reference":"alias","secret_provider":{"kind":"fd","reference":"3"}})
    assert p.status == "ready" and "secret" not in str(p.as_dict()).lower().replace("secret_provider", "")
    with pytest.raises(ValueError): signing_projection({"identity_id":"x","key_reference":"k","password":"nope"})


def test_repository_pack_projection_exposes_retention_and_pin():
    p = repository_pack_projection({"pack_id":"p1","published":True,"verified":True,"pin":True,"retention":"release","sha256":"abc"})
    assert p.status == "published" and p.facts["pin"] and p.facts["retention"] == "release"


def test_cleanup_projection_is_explainable_and_fail_closed():
    p = cleanup_projection({"plan_id":"c1","destination":"ssh","candidates":[{"eligible":True,"reclaimable_bytes":10},{"eligible":False,"reclaimable_bytes":20}]})
    assert p.status == "blocked" and p.facts["blocked_count"] == 1 and p.facts["reclaimable_bytes"] == 30
    assert "doctor" in p.actions and "reclaim-plan" in p.actions


def test_remote_envelope_transports_refs_not_secrets():
    p = transaction_projection({"id":"tx","checkpoints":[]})
    env = remote_lifecycle_envelope(repository_identity="repo:abc",operation="cleanup",projection=p,signing_identity_id="release",secret_provider_ref="fd:3")
    assert env["repository_identity"] == "repo:abc" and env["signing_identity_id"] == "release"
    with pytest.raises(ValueError): remote_lifecycle_envelope(repository_identity="repo",operation="x",projection=p,secret_provider_ref="token=abc")


def test_federation_pack_selection_never_flattens_identity():
    p = federation_pack_selection([{"repository_identity":"repo:a","root":"/a"},{"repository_identity":"repo:b","root":"/b"}])
    assert not p["identity_flattening"] and [x["repository_identity"] for x in p["repositories"]] == ["repo:a","repo:b"]
    with pytest.raises(ValueError): federation_pack_selection([{"repository_identity":"repo:a"},{"repository_identity":"repo:a"}])


def test_legacy_migration_is_explicit_and_deterministic():
    a = legacy_migration_record(source_kind="backup",source_identity="old",canonical_identity="new",disposition="retired")
    b = legacy_migration_record(source_kind="backup",source_identity="old",canonical_identity="new",disposition="retired")
    assert a == b and a["migration_id"].startswith("migration:")


def test_dogfood_matrix_covers_self_host_lifecycle_surfaces():
    scenarios = {x["scenario"] for x in dogfood_matrix()}
    assert {"overlay-delivery","bundle-delivery","snapshot-delivery","repository-pack","signing","ssh-cleanup","federation-cleanup","transaction-recovery"} <= scenarios


def test_exported_obligations_keep_authority_boundaries_explicit():
    out = exported_obligations()
    assert {"PRES-X08","DIST-G3","LIFE-G3","RMP","EXEC","CORE"} <= set(out)
