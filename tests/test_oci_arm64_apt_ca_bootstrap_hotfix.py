from __future__ import annotations

import io
import tarfile

from devtool.oci_image import canonical_android_oci_definition


def _dockerfile() -> str:
    definition = canonical_android_oci_definition()
    with tarfile.open(fileobj=io.BytesIO(definition.context_bytes), mode="r:") as archive:
        return archive.extractfile("Dockerfile").read().decode()


def test_arm64_image_bootstraps_ca_before_enabling_snapshot_https() -> None:
    dockerfile = _dockerfile()
    assert "apt-get install -y --no-install-recommends ca-certificates" in dockerfile
    assert dockerfile.index("apt-get install -y --no-install-recommends ca-certificates") < dockerfile.index('APT::Snapshot')
    assert dockerfile.index('APT::Snapshot') < dockerfile.index("apt-get update", dockerfile.index('APT::Snapshot'))


def test_arm64_image_keeps_amd64_packages_on_primary_archive_not_ports() -> None:
    dockerfile = _dockerfile()
    assert "dpkg --add-architecture amd64" in dockerfile
    assert "Architectures: amd64" in dockerfile
    assert "URIs: http://archive.ubuntu.com/ubuntu" in dockerfile
    assert "URIs: http://security.ubuntu.com/ubuntu" in dockerfile
    assert "libc6:amd64" in dockerfile
    assert "libstdc++6:amd64" in dockerfile
    assert "ports.ubuntu.com/ubuntu-ports/dists/noble" not in dockerfile
    assert "binary-amd64" not in dockerfile
