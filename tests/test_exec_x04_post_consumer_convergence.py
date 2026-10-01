from pathlib import Path
from devtool.exec_operational_adoption import OperationalContext, ConsumerQualification, qualify_consumer_reuse, SourceProofRef, source_proof_gc_barrier


def q(a, *, portable=True, equivalent=True, refs=("run:proof:sha256:abc",)):
    return ConsumerQualification(a, f"{a.lower()}:identity", refs, portable, equivalent)

def test_transaction_requires_life_equivalence_and_evidence():
    c=OperationalContext(domain="transaction", operation="apply-overlay")
    assert qualify_consumer_reuse(context=c, consumers=[q("LIFE")])["qualified"]
    bad=qualify_consumer_reuse(context=c, consumers=[q("LIFE", equivalent=False)])
    assert not bad["qualified"] and bad["disposition"] == "run"

def test_remote_requires_dist_and_portability():
    c=OperationalContext(domain="remote", operation="resume", provider="ssh", remote_identity="tablet")
    assert qualify_consumer_reuse(context=c, consumers=[q("DIST")])["qualified"]
    bad=qualify_consumer_reuse(context=c, consumers=[q("DIST", portable=False)])
    assert bad["nonportable"] == ["DIST"] and not bad["qualified"]

def test_selected_test_requires_test_receipt():
    c=OperationalContext(domain="selected-test", operation="pytest", selected_tests=("A::test_x",))
    assert qualify_consumer_reuse(context=c, consumers=[q("TEST")])["qualified"]
    assert qualify_consumer_reuse(context=c, consumers=[])["missing"] == ["TEST"]

def test_roadmap_requires_stable_rmp_binding():
    c=OperationalContext(domain="roadmap", operation="gate", workflow_id="gate:x")
    assert qualify_consumer_reuse(context=c, consumers=[q("RMP")])["qualified"]
    assert not qualify_consumer_reuse(context=c, consumers=[ConsumerQualification("RMP", "rmp:x", ())])["qualified"]

def test_device_identity_requires_android_proof():
    c=OperationalContext(domain="device", operation="instrumentation", device_identity="serial:a")
    assert qualify_consumer_reuse(context=c, consumers=[q("ANDROID")])["qualified"]

def test_gc_barrier_protects_pinned_source_proofs():
    p=SourceProofRef("r1","n1","digest1",True,"",("proof:1",))
    u=SourceProofRef("r2","n2","",False,"missing",())
    out=source_proof_gc_barrier([p,u], referenced_digests=[])
    assert out["protected_digests"] == ["digest1"] and out["unavailable"] == ["r2:n2"] and not out["can_prune_all"]
