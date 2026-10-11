import pytest
from devtool.federation_discovery import resolve_federated_repository
from devtool.federation_operations import (capture_federated_resume_checkpoint, validate_federated_resume_checkpoint, FederationOperationalBoundaryError)
from test_frx10_operational_boundaries import _parent, _load

def test_operational_checkpoint_schema_is_mandatory(tmp_path):
    parent, _ = _parent(tmp_path)
    config = _load(parent)
    checkpoint = capture_federated_resume_checkpoint(resolve_federated_repository(config, "child"))
    validate_federated_resume_checkpoint(config, checkpoint)
    for value in (None, False, True, 0, "1", 2):
        altered = dict(checkpoint)
        if value is None:
            altered.pop("schema_version")
        else:
            altered["schema_version"] = value
        with pytest.raises(FederationOperationalBoundaryError, match="schema"):
            validate_federated_resume_checkpoint(config, altered)
