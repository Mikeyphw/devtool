from __future__ import annotations

import io
import tarfile

from devtool.oci_image import canonical_android_oci_definition


def _dockerfile() -> str:
    definition = canonical_android_oci_definition()
    with tarfile.open(fileobj=io.BytesIO(definition.context_bytes), mode="r:") as archive:
        return archive.extractfile("Dockerfile").read().decode("utf-8")


def test_arm64_oci_uses_live_multiarch_apt_without_snapshot_skew() -> None:
    dockerfile = _dockerfile()
    assert "FROM ubuntu:24.04" in dockerfile
    assert "apt-get install -y --no-install-recommends ca-certificates" in dockerfile
    assert "APT::Snapshot" not in dockerfile
    assert "/etc/apt/apt.conf.d/50devtool-snapshot" in dockerfile
    assert "DEVTOOL_APT_POLICY=live-noble-multiarch" in dockerfile
    assert "dpkg --add-architecture amd64" in dockerfile
    assert "URIs: http://ports.ubuntu.com/ubuntu-ports" in dockerfile
    assert "URIs: http://archive.ubuntu.com/ubuntu" in dockerfile
    assert "URIs: http://security.ubuntu.com/ubuntu" in dockerfile
    assert "Architectures: arm64" in dockerfile
    assert "Architectures: amd64" in dockerfile
    assert "libc6:amd64" in dockerfile
    assert "libstdc++6:amd64" in dockerfile


def test_contract_documents_live_multiarch_policy() -> None:
    contract = canonical_android_oci_definition().contract
    assert contract["apt_policy"]["mode"] == "live-noble-multiarch"
    assert contract["apt_policy"]["snapshot"] == "disabled"
    assert "libc version skew" in contract["apt_policy"]["reason"]
    policy = contract["base_image"]["multiarch_apt_policy"]
    assert policy["snapshot_policy"] == "disabled-live-noble-multiarch"
    assert "version-aligned" in policy["version_alignment"]
    assert "binary-amd64" in policy["reason"]
