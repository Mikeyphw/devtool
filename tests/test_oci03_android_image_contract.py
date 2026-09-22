from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool.cli import build_parser
from devtool.core.events import EventBus
from devtool.execution_providers import SSH_PROOT_OCI, execution_provider
from devtool.oci_image import canonical_android_oci_definition, android_oci_definition_payload
from devtool import proot_oci
from devtool.ssh_tmux_runner import SSHRunnerConfig


def _config() -> SSHRunnerConfig:
    return SSHRunnerConfig(
        host="192.168.1.8", port=8022, user="u0_a123", profile="tablet",
        identity="/home/me/.ssh/tablet", execution_provider="ssh-proot-oci",
    )


def _patch_bus(monkeypatch: pytest.MonkeyPatch) -> EventBus:
    bus = EventBus()
    monkeypatch.setattr(proot_oci, "publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    return bus


class _QueuePopen:
    outputs: list[tuple[bytes, bytes, int]] = []
    calls: list[list[str]] = []
    kwargs_calls: list[dict[str, object]] = []
    pid_seed = 7000

    def __init__(self, argv, **kwargs):
        type(self).pid_seed += 1
        self.pid = type(self).pid_seed
        self.argv = list(argv)
        type(self).calls.append(self.argv)
        type(self).kwargs_calls.append(dict(kwargs))
        if not type(self).outputs:
            raise AssertionError("unexpected subprocess")
        self._stdout, self._stderr, self.returncode = type(self).outputs.pop(0)

    def communicate(self, input=None, timeout=None):
        self.input = input
        return self._stdout, self._stderr

    def kill(self):
        self.returncode = -9


def test_canonical_definition_pins_promised_android_arm64_toolchain() -> None:
    payload = android_oci_definition_payload()
    contract = payload["contract"]
    android = contract["toolchains"]["android"]
    assert payload["architecture"] == "aarch64"
    assert contract["architecture"]["oci"] == "linux/arm64"
    assert contract["base_image"]["index_digest"].startswith("sha256:")
    assert contract["apt_snapshot"] == "20260911T235959Z"
    assert {item["major"] for item in contract["toolchains"]["jdk"]} == {17, 21}
    assert next(item for item in contract["toolchains"]["jdk"] if item.get("default"))["major"] == 21
    assert contract["toolchains"]["python"]["version_prefix"] == "3.12."
    assert contract["toolchains"]["node"]["version"] == "22.20.0"
    assert android["api"] == 36
    assert android["build_tools"] == "36.0.0"
    assert android["ndk"] == "29.0.14206865"
    assert android["arm64_build_tools_source"]["aosp_source"] == "android-16.0.0_r1"
    assert contract["gradle"]["policy"] == "project-wrapper-first"
    assert contract["gradle"]["baked_distribution"] is False
    assert contract["immutability"]["ordinary_validation_mutates_toolchains"] is False


def test_build_context_is_byte_deterministic_and_self_describing() -> None:
    first = canonical_android_oci_definition()
    second = canonical_android_oci_definition()
    assert first.definition_digest == second.definition_digest
    assert first.context_sha256 == second.context_sha256
    assert first.context_bytes == second.context_bytes
    assert len(first.definition_digest) == len(first.context_sha256) == 64
    import io, tarfile
    with tarfile.open(fileobj=io.BytesIO(first.context_bytes), mode="r:") as archive:
        names = sorted(archive.getnames())
        assert names == ["Dockerfile", "image-manifest.json", "validate-image.sh"]
        for member in archive.getmembers():
            assert member.mtime == 0
            assert member.uid == member.gid == 0


def test_dockerfile_uses_pinned_inputs_build_only_mutation_and_no_baked_gradle() -> None:
    definition = canonical_android_oci_definition()
    import io, tarfile
    with tarfile.open(fileobj=io.BytesIO(definition.context_bytes), mode="r:") as archive:
        dockerfile = archive.extractfile("Dockerfile").read().decode()
        validator = archive.extractfile("validate-image.sh").read().decode()
    assert "FROM ubuntu:noble-20260911@sha256:" in dockerfile
    assert 'APT::Snapshot' in dockerfile
    assert "06907b9c088ce62305bc1530e5c1ae1510245114645768f7750c349c5b6fe667" in dockerfile
    assert "4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583" in dockerfile
    assert "'platforms;android-36' 'build-tools;36.0.0' 'ndk;29.0.14206865'" in dockerfile
    assert "platform-tools-36.0.0" in dockerfile
    assert "qemu-user-static" in dockerfile and "libc6:amd64" in dockerfile
    assert "android.aapt2FromMavenOverride" in dockerfile
    assert "gradle" not in [token.strip() for token in dockerfile.split() if token.strip() == "gradle"]
    assert "apt-get" not in validator
    assert "sdkmanager" not in validator
    assert "pip install" not in validator
    assert "npm install -g" not in validator
    assert "qemu" in validator and "ndk_clang" in validator and "android-probe" in validator
    assert "compile+link" in validator and "clang.x86_64" in validator
    assert "qemu-x86_64-wrapped-verified" in definition.contract["validation"]["ndk_host_mode"]
    assert "expected exactly one OCI manifest" in proot_oci._oci_build_script(definition_digest=definition.definition_digest, context_sha256=definition.context_sha256, tag=definition.reference, no_cache=False)
    assert "OCI manifest blob digest mismatch" in proot_oci._oci_build_script(definition_digest=definition.definition_digest, context_sha256=definition.context_sha256, tag=definition.reference, no_cache=False)


def test_oci03_cli_surface_exposes_definition_build_and_image_validation() -> None:
    parser = build_parser()
    definition = parser.parse_args(["oci", "definition", "--format", "json"])
    assert definition.oci_command == "definition"
    build = parser.parse_args(["oci", "build", "tablet", "--install-as", "android36", "--no-cache", "--dry-run"])
    assert build.oci_command == "build"
    assert build.oci_install_as == "android36"
    assert build.oci_no_cache is True and build.oci_dry_run is True
    validate = parser.parse_args(["oci", "validate-image", "tablet", "android36"])
    assert validate.container == "android36"


def test_build_dry_run_plans_stable_dependency_graph_without_processes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bus = _patch_bus(monkeypatch)
    local_cache = tmp_path / "must-not-be-created"
    monkeypatch.setenv("DEVTOOL_OCI_LOCAL_CACHE", str(local_cache))
    payload = proot_oci.oci_build_payload(
        _config(), run_id="run-oci03-dry", install_as="android36", dry_run=True, no_cache=True,
    )
    assert payload["ok"] is True and payload["dry_run"] is True
    assert len(payload["node_ids"]) == 4
    snap = bus.execution_store.snapshot("run-oci03-dry")
    stage, build, install, validate = [snap.node(node) for node in payload["node_ids"]]
    assert build.depends_on == (stage.node_id,)
    assert install.depends_on == (build.node_id,)
    assert validate.depends_on == (install.node_id,)
    assert all(node.status == "skipped" for node in (stage, build, install, validate))
    assert snap.processes == ()
    assert not local_cache.exists()


def test_real_build_owns_processes_publishes_remote_archive_and_validates_installed_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bus = _patch_bus(monkeypatch)
    monkeypatch.setenv("DEVTOOL_OCI_LOCAL_CACHE", str(tmp_path / "local-cache"))
    definition = canonical_android_oci_definition()
    image_sha = "a" * 64
    metadata = json.dumps({
        "schema_version": 1,
        "definition_digest": definition.definition_digest,
        "context_sha256": definition.context_sha256,
        "apt_snapshot": "20260911T235959Z",
        "architecture": "aarch64",
        "packages": {},
    }, separators=(",", ":"), sort_keys=True)
    _QueuePopen.calls.clear(); _QueuePopen.kwargs_calls.clear()
    _QueuePopen.outputs = [
        (f"context_sha256={definition.context_sha256}\n".encode(), b"", 0),
        ((f"image_reference=devtool/android-build:arm64-api36-v1\n"
          f"image_digest=sha256:{'b' * 64}\n"
          f"image_archive=/home/u/.cache/devtool/oci/builds/image.oci.tar\n"
          f"image_sha256={image_sha}\nimage_size=1234\n"
          f"DEVTOOL_OCI_ARTIFACT\t/home/u/.cache/devtool/oci/builds/image.oci.tar\t{image_sha}\t1234\toci-archive\n").encode(), b"", 0),
        (b"installed_container=android36\n", b"", 0),
        (("capability=android-oci\nstatus=ready\narchitecture=aarch64\npython=3.12.3\n"
          "node=v22.20.0\napi=36\nbuild_tools=36.0.0\nndk=29.0.14206865\n"
          "ndk_host=qemu-x86_64-wrapped-verified\nDEVTOOL_OCI_METADATA=" + metadata + "\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    payload = proot_oci.oci_build_payload(_config(), run_id="run-oci03-build", install_as="android36")
    assert payload["ok"] is True
    assert payload["image_sha256"] == image_sha
    assert payload["image_digest"] == "sha256:" + ("b" * 64)
    assert payload["validation"]["ok"] is True
    assert len(_QueuePopen.calls) == 4
    assert "proot-distro" in " ".join(_QueuePopen.calls[1]) and " build " in " ".join(_QueuePopen.calls[1])
    assert "--architecture aarch64" in " ".join(_QueuePopen.calls[1])
    assert "--build-arg" in " ".join(_QueuePopen.calls[1])
    assert "login android36" in " ".join(_QueuePopen.calls[3])
    snap = bus.execution_store.snapshot("run-oci03-build")
    assert all(snap.node(node).status == "succeeded" for node in payload["node_ids"])
    assert len(snap.processes) == 4
    assert len(snap.artifacts) == 1
    artifact = snap.artifacts[0]
    assert artifact.sha256 == image_sha
    assert artifact.producer_node_id == payload["node_ids"][1]
    assert artifact.process_id
    build_proc = next(proc for proc in snap.processes if proc.node_id == payload["node_ids"][1])
    assert build_proc.provenance["execution_provider"] == "ssh-proot-oci"
    assert build_proc.provenance["definition_digest"] == definition.definition_digest


def test_image_validation_fails_closed_on_capability_or_provenance_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _patch_bus(monkeypatch)
    _QueuePopen.calls.clear()
    _QueuePopen.outputs = [(b"capability=android-ndk-host\nstatus=failed\n", b"NDK host clang failed", 65)]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="failed with exit 65"):
        proot_oci.oci_validate_image_payload(_config(), "android36", run_id="run-oci03-fail")
    snap = bus.execution_store.snapshot("run-oci03-fail")
    node = next(iter(snap.nodes))
    assert node.status == "failed"
    assert any(diag.code == "OCI_IMAGE_CAPABILITY" for diag in snap.diagnostics)


def test_oci03_does_not_activate_repository_execution_provider() -> None:
    provider = execution_provider(SSH_PROOT_OCI, remote=True)
    assert provider.capabilities.containerized is True
    assert provider.capabilities.requires_image is True
    assert provider.executable is False


def test_build_process_without_sha_verified_artifact_evidence_fails_owned_node(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _patch_bus(monkeypatch)
    definition = canonical_android_oci_definition()
    _QueuePopen.calls.clear(); _QueuePopen.kwargs_calls.clear()
    _QueuePopen.outputs = [
        (f"context_sha256={definition.context_sha256}\n".encode(), b"", 0),
        (b"image_reference=devtool/android-build:arm64-api36-v1\n", b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="without required SHA-verified artifact evidence"):
        proot_oci.oci_build_payload(_config(), run_id="run-oci03-missing-artifact")
    snap = bus.execution_store.snapshot("run-oci03-missing-artifact")
    stage, build = [snap.node(node_id) for node_id in [
        proot_oci._stable_node_id(_config(), "stage-image-definition", definition.context_sha256),
        proot_oci._stable_node_id(_config(), "build-android-image", f"{definition.definition_digest}|{definition.reference}|0"),
    ]]
    assert stage.status == "succeeded"
    assert build.status == "failed"
    assert any(diag.code == "OCI_IMAGE_BUILD_ARTIFACT" for diag in snap.diagnostics)
    build_proc = next(proc for proc in snap.processes if proc.node_id == build.node_id)
    assert build_proc.status == "failed"
    assert build_proc.exit_code == 65
    assert snap.artifacts == ()


def test_validation_success_is_owned_by_remote_validator_not_optional_status_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _patch_bus(monkeypatch)
    definition = canonical_android_oci_definition()
    metadata = json.dumps({
        "schema_version": 1,
        "definition_digest": definition.definition_digest,
        "context_sha256": definition.context_sha256,
    }, separators=(",", ":"), sort_keys=True)
    _QueuePopen.outputs = [(f"DEVTOOL_OCI_METADATA={metadata}\n".encode(), b"", 0)]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    payload = proot_oci.oci_validate_image_payload(_config(), "android36", run_id="run-oci03-validator-owned")
    assert payload["ok"] is True
    snap = bus.execution_store.snapshot("run-oci03-validator-owned")
    assert snap.node(payload["node_id"]).status == "succeeded"


def test_oci03_external_gate_harness_is_no_cache_and_checks_real_provenance() -> None:
    script = Path("tools/oci03-fresh-worker-smoke.sh").read_text(encoding="utf-8")
    assert "oci build" in script
    assert "--install-as" in script and "--no-cache" in script
    assert "image_digest" in script and "image_sha256" in script
    assert "definition_digest" in script and "context_sha256" in script
    assert "ndk_wrapped_host_binaries" in script
    assert 'p.get("ok")' in script
    assert 'p.get("healthy")' not in script
    assert "/tmp" not in script
