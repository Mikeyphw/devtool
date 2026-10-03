from pathlib import Path
import pytest
from devtool.distributed_operational_adoption import *

def test_delivery_states():
    assert delivery_health(connected=True).state=="live"
    assert delivery_health(connected=True,backlog=2).state=="delayed"
    assert delivery_health(connected=False,reconnecting=True).state=="reconnecting"
    assert delivery_health(connected=False).state=="degraded"
    assert delivery_health(connected=True,terminal_reconciled=True).state=="reconciled"

def test_history_is_never_fake_live_replay():
    p=historical_child_projection({"run_id":"c1","status":"succeeded"})
    assert p["mode"]=="historical" and p["live_replay"] is False

def test_unknown_event_is_opaque_safe():
    p=opaque_safe_event({"event_type":"future.secret.shape","run_id":"r","payload":{"secret":"x"}},known_types={"known"})
    assert p["opaque"] and "payload" not in p and p["original_event_type"]=="future.secret.shape"

def test_backpressure_never_drops_semantic_truth():
    p=backpressure_policy(backlog=999,capacity=1000)
    assert p["pressure"]=="catch-up" and p["drop_semantic_events"] is False and p["raw_stream_policy"]=="reference-only"

def test_machine_receipt_and_retention():
    p=machine_child_receipt({"run_id":"c","parent_run_id":"p","receipt":{"ack":7},"retention":{"pinned":True}})
    assert p["receipt"]["ack"]==7 and p["retention"]["pinned"]
    assert retention_projection(run_id="c",pinned=True,retained=False)["deletion_allowed"] is False

def test_provider_observation_migrates_to_binding():
    assert provider_observation_migration({"provider_observation":{},"provider_binding":{"id":"x"}})["migration"]=="canonical-provider-binding"

def test_doctor_links_are_run_native():
    p=doctor_links(run_id="p",child_run_id="c")
    assert p["delivery"]=="run://p/delivery" and p["child"]=="run://c"

def test_dogfood_requires_every_provider_class():
    assert dogfood_matrix(PROVIDERS)["complete"]
    assert not dogfood_matrix(PROVIDERS[:-1])["complete"]

def test_obligation_disposition_fails_closed():
    assert obligation_disposition([{"id":"a","owner":"dist","source":"x03","status":"delivered"}])["complete"]
    assert not obligation_disposition([{"id":"a","status":"unknown"}])["complete"]


def test_dist_x04_all_helpers_have_production_consumers_in_final_gate_source():
    source = Path("devtool/dist_g3_gate.py").read_text(encoding="utf-8")
    for symbol in (
        "delivery_health",
        "historical_child_projection",
        "machine_child_receipt",
        "provider_observation_migration",
        "retention_projection",
        "doctor_links",
        "dogfood_matrix",
        "obligation_disposition",
    ):
        assert symbol in source


def test_dist_a07_peer_capability_negotiation_rejects_missing_required_feature():
    from devtool.distributed_transport import (
        FEATURE_CURSOR_ACK,
        FEATURE_EVENT_ENVELOPE,
        FEATURE_GAP_CATCHUP,
        ProtocolNegotiationError,
        TransportProtocolCapabilities,
        negotiate_transport_protocol,
    )
    remote = TransportProtocolCapabilities(features=frozenset({FEATURE_EVENT_ENVELOPE, FEATURE_CURSOR_ACK}))
    with pytest.raises(ProtocolNegotiationError, match="gap.catchup"):
        negotiate_transport_protocol(
            TransportProtocolCapabilities(),
            remote,
            required_features={FEATURE_EVENT_ENVELOPE, FEATURE_CURSOR_ACK, FEATURE_GAP_CATCHUP},
        )
