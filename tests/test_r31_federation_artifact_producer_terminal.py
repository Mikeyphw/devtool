from types import SimpleNamespace
import hashlib
import pytest
from devtool.federation_artifacts import _verify_record, FederationArtifactError
from devtool.models import FederatedArtifactInput


def test_artifact_producer_exit_must_be_observed_integer_zero(tmp_path):
    root = tmp_path / "child"; root.mkdir()
    payload = root / "artifact.bin"; payload.write_bytes(b"trusted bytes")
    row = {"id": "build-1", "path": "artifact.bin", "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(), "type": "binary", "valid": True}
    spec = FederatedArtifactInput(name="release", ref="build::artifact", producer_step="build", selector="build-1")
    child = SimpleNamespace(step_id="build", status="succeeded", exit_code=0, operational_ownership={"root": str(root)})
    assert _verify_record(child, spec, row)[0] == payload
    for value in (None, False, True, "0", "", 0.0, -1, 1):
        child.exit_code = value
        with pytest.raises(FederationArtifactError, match="terminal result"):
            _verify_record(child, spec, row)
    child.exit_code = 0
    child.status = "cancelled"
    with pytest.raises(FederationArtifactError, match="terminal result"):
        _verify_record(child, spec, row)
