from __future__ import annotations
import pytest
from devtool.android.execution_snapshot import consume_destination_android_environment as consume

EXPECTED = "ssh:tablet"

def evidence(**changes):
    payload = {"destination_id": EXPECTED, "android_environment": {
        "environment_id": "env-1", "component_identities": {"android.platform": "api36"},
    }}
    payload.update(changes)
    return payload

def test_canonical_destination_passes():
    record = consume(evidence(), destination_id=EXPECTED)
    assert record.destination_id == EXPECTED and record.environment_id == "env-1"

def test_nested_destination_identity_passes():
    payload = evidence(); del payload["destination_id"]
    payload["android_environment"]["destination_id"] = EXPECTED
    assert consume(payload, destination_id=EXPECTED).environment_id == "env-1"

@pytest.mark.parametrize("payload", [
    {"android_environment": {"environment_id": "env-1", "component_identities": {"platform": "ok"}}},
    evidence(destination_id="ssh:other"),
    evidence(android_environment={"destination_id": "ssh:other", "environment_id": "env-1", "component_identities": {"platform": "ok"}}),
    evidence(android_environment={"environment_id": "env-1", "component_identities": {"platform": ""}}),
    evidence(android_environment={"environment_id": "env-1", "component_identities": {"platform": 23}}),
    evidence(android_environment={"environment_id": "", "component_identities": {"platform": "ok"}}),
])
def test_incomplete_or_wrong_owner_fails_closed(payload):
    with pytest.raises(ValueError):
        consume(payload, destination_id=EXPECTED)
