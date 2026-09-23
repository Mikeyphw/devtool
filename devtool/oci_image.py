from __future__ import annotations

"""Canonical deterministic Android OCI image definition (OCI-03)."""

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import io
import json
import tarfile
from typing import Any

_PACKAGE_ROOT = files("devtool")
_RESOURCE_ROOT = _PACKAGE_ROOT.joinpath("resources").joinpath("oci").joinpath("android-arm64")
_SOURCE_RUNTIME = {
    "build-apk": _PACKAGE_ROOT.joinpath("android").joinpath("_standalone.py"),
    "termux_toolchains.py": _PACKAGE_ROOT.joinpath("termux_toolchains.py"),
    "devtool-events.init.gradle": _PACKAGE_ROOT.joinpath("resources").joinpath("gradle").joinpath("devtool-events.init.gradle"),
}
_REQUIRED = ("Dockerfile", "build-apk", "devtool-events.init.gradle", "image-manifest.json", "termux_toolchains.py", "validate-image.sh")


@dataclass(frozen=True, slots=True)
class AndroidOciDefinition:
    contract: dict[str, Any]
    definition_digest: str
    context_sha256: str
    context_size: int
    context_bytes: bytes

    @property
    def reference(self) -> str:
        return str(self.contract["reference"])

    @property
    def architecture(self) -> str:
        return str(self.contract["architecture"]["proot_distro"])


def _resource_bytes(name: str) -> bytes:
    if name not in _REQUIRED:
        raise ValueError(f"Unknown OCI image resource: {name}")
    return _RESOURCE_ROOT.joinpath(name).read_bytes()


def _validate_contract(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != 1 or value.get("kind") != "devtool-android-oci-image-contract":
        raise ValueError("Unsupported Devtool Android OCI image-manifest schema")
    required = ("definition_id", "reference", "architecture", "base_image", "apt_policy", "toolchains", "gradle", "devtool_runtime", "immutability", "validation")
    missing = [name for name in required if not value.get(name)]
    if missing:
        raise ValueError("Android OCI image manifest is missing required fields: " + ", ".join(missing))

    def require_sha256(label: str, raw: object, *, prefixed: bool = False) -> str:
        text = str(raw or "").strip().lower()
        candidate = text[7:] if prefixed and text.startswith("sha256:") else text
        if len(candidate) != 64 or any(ch not in "0123456789abcdef" for ch in candidate):
            raise ValueError(f"Android OCI contract {label} must be an exact SHA-256 digest")
        return text

    architecture = value["architecture"]
    if architecture.get("proot_distro") != "aarch64" or architecture.get("oci") != "linux/arm64":
        raise ValueError("OCI-03 canonical image must target linux/arm64 (aarch64)")
    base = value["base_image"]
    if base.get("reference") != "ubuntu:24.04":
        raise ValueError("OCI-03 canonical base image must use the resolvable Ubuntu 24.04 multi-arch tag")
    if base.get("resolution_policy") != "registry-resolve-stable-lts-tag-at-build-time":
        raise ValueError("OCI-03 canonical base image must declare build-time registry resolution policy")
    if base.get("fallback_reference") != "ubuntu:noble":
        raise ValueError("OCI-03 canonical base image must document ubuntu:noble as the equivalent LTS fallback")
    rejected = str(base.get("rejected_reference") or "")
    if "ubuntu:noble-20260911" not in rejected or "sha256:c5ddb18c368db61a2f5b0971380f0ff1a1bb50e1da176cc48c9e5c2dbf3afb5b" not in rejected:
        raise ValueError("OCI-03 base-image contract must record the rejected stale pinned Ubuntu reference")
    multiarch_apt = base.get("multiarch_apt_policy", {})
    if multiarch_apt.get("arm64_sources") != "ports.ubuntu.com/ubuntu-ports only":
        raise ValueError("OCI-03 ARM64 apt policy must keep arm64 packages on ubuntu-ports only")
    if multiarch_apt.get("amd64_sources") != "archive.ubuntu.com/ubuntu plus security.ubuntu.com/ubuntu only":
        raise ValueError("OCI-03 amd64 apt policy must avoid ubuntu-ports binary-amd64 indexes")
    if "binary-amd64" not in str(multiarch_apt.get("reason", "")):
        raise ValueError("OCI-03 multiarch apt policy must document the ports.ubuntu.com binary-amd64 failure mode")
    if multiarch_apt.get("snapshot_policy") != "disabled-live-noble-multiarch":
        raise ValueError("OCI-03 multiarch APT policy must disable snapshots for live Ubuntu 24.04 libc alignment")
    if "version-aligned" not in str(multiarch_apt.get("version_alignment", "")):
        raise ValueError("OCI-03 multiarch APT policy must document arm64/amd64 libc version alignment")
    apt_policy = value.get("apt_policy", {})
    if not isinstance(apt_policy, dict) or apt_policy.get("mode") != "live-noble-multiarch" or apt_policy.get("snapshot") != "disabled":
        raise ValueError("OCI-03 APT policy must use live noble multiarch repositories with snapshot disabled")
    if "libc version skew" not in str(apt_policy.get("reason", "")):
        raise ValueError("OCI-03 APT policy must document why APT::Snapshot is disabled for multiarch libc alignment")

    toolchains = value["toolchains"]
    jdks = {int(item.get("major", 0)): item for item in toolchains.get("jdk", []) if isinstance(item, dict)}
    if set(jdks) != {17, 21} or jdks[21].get("default") is not True:
        raise ValueError("Android OCI contract must provide JDK 17 and JDK 21 with JDK 21 as default")
    python = toolchains.get("python", {})
    if python.get("version_prefix") != "3.12." or python.get("minimum") != "3.11":
        raise ValueError("Android OCI contract must pin Python 3.12.x while preserving Devtool >=3.11 compatibility")
    node = toolchains.get("node", {})
    if node.get("version") != "22.20.0":
        raise ValueError("Android OCI contract must pin Node 22.20.0")
    require_sha256("Node archive", node.get("sha256"))
    if node.get("sha256") != "06907b9c088ce62305bc1530e5c1ae1510245114645768f7750c349c5b6fe667":
        raise ValueError("OCI-03 Node 22.20.0 archive digest changed unexpectedly")

    android = toolchains["android"]
    expected = {"api": 36, "build_tools": "36.0.0", "ndk": "29.0.14206865"}
    for key, want in expected.items():
        if android.get(key) != want:
            raise ValueError(f"Android OCI contract {key} must be pinned to {want!r}")
    if android.get("command_line_tools_revision") != "15859902":
        raise ValueError("Android OCI command-line tools revision must remain pinned to 15859902")
    require_sha256("Android command-line tools archive", android.get("command_line_tools_sha256"))
    if android.get("command_line_tools_sha256") != "4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583":
        raise ValueError("OCI-03 Android command-line tools archive digest changed unexpectedly")
    arm_source = android.get("arm64_build_tools_source", {})
    if arm_source.get("release") != "platform-tools-36.0.0" or arm_source.get("aosp_source") != "android-16.0.0_r1":
        raise ValueError("ARM64 Android build-tools source must match the pinned Android 16 / 36.0.0 release")
    assets = arm_source.get("assets", {})
    if set(assets) != {"aapt2", "aidl", "zipalign", "split-select"}:
        raise ValueError("ARM64 Android build-tools asset digest set is incomplete")
    expected_assets = {
        "aapt2": "7512ff7e381bea6fd310b6f6e347422c8fda21e07c6e3f0162742e95eb9d7f98",
        "aidl": "8c97356b8bba8f7aad44cfd408e3ee24c66a1258244b5d08af1c9249f50dd659",
        "split-select": "fb7f0c3c87dbd4243d7e1277ba64ded2399389244058b6a4c969cc615360766c",
        "zipalign": "e8856fb24b10095eb6e940c577ce96d89ddbfc45aa0f7eeaef1597ef68f11a12",
    }
    if assets != expected_assets:
        raise ValueError("ARM64 Android build-tool asset digests changed unexpectedly")
    for name, digest in assets.items():
        require_sha256(f"ARM64 Android build-tool {name}", digest)
    if arm_source.get("release_commit") != "58f06a2":
        raise ValueError("ARM64 Android build-tools release commit changed unexpectedly")
    require_sha256("ARM64 Android build-tools SHA256SUMS asset", arm_source.get("sha256sums_asset_sha256"))

    if value.get("definition_id") != "android-arm64-api36-v2" or value.get("reference") != "devtool/android-build:arm64-api36-v2":
        raise ValueError("OCI-05 canonical Android image must use the v2 embedded-runtime identity")
    runtime = value.get("devtool_runtime", {})
    embedded = runtime.get("embedded_android_builder", {}) if isinstance(runtime, dict) else {}
    expected_embedded = {
        "path": "/usr/local/lib/devtool/build-apk",
        "sha256": "c35521e80c7f05b734e23c444922fa7b02820d9318c17494d9f0076fc23f70fc",
        "toolchains_path": "/usr/local/lib/devtool/termux_toolchains.py",
        "toolchains_sha256": "46bff66338eee52d297b90cbfe6773e979d9a27d0b3c93f7816a139cf0e255fe",
        "gradle_events_init_path": "/usr/local/lib/devtool/devtool-events.init.gradle",
        "gradle_events_init_sha256": "da7550a61bae530a8bb407c64574a456f77398db29332470623b47028bd9a50f",
    }
    if embedded != expected_embedded:
        raise ValueError("OCI-05 embedded Devtool Android runtime contract changed unexpectedly")
    for label, resource, digest in (
        ("Android builder", "build-apk", expected_embedded["sha256"]),
        ("toolchain companion", "termux_toolchains.py", expected_embedded["toolchains_sha256"]),
        ("Gradle telemetry init", "devtool-events.init.gradle", expected_embedded["gradle_events_init_sha256"]),
    ):
        payload = _resource_bytes(resource)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError(f"OCI-05 embedded {label} resource digest mismatch")
        canonical_source = _SOURCE_RUNTIME[resource].read_bytes()
        if payload != canonical_source:
            raise ValueError(f"OCI-05 embedded {label} resource is stale relative to canonical Devtool source")

    required_commands = set(value.get("devtool_runtime", {}).get("required_commands", []))
    for command in ("timeout", "mktemp", "grep", "head", "sha256sum"):
        if command not in required_commands:
            raise ValueError(f"Android OCI runtime command inventory is missing {command}")

    if value["gradle"].get("policy") != "project-wrapper-first" or value["gradle"].get("baked_distribution") is not False:
        raise ValueError("Android OCI contract must preserve project Gradle Wrapper authority and bake no Gradle distribution")
    if value["immutability"].get("ordinary_validation_mutates_toolchains") is not False:
        raise ValueError("Android OCI ordinary validation must be immutable")
    if value["validation"].get("ndk_host_mode") != "qemu-x86_64-wrapped-verified":
        raise ValueError("Android OCI NDK host capability must be transparently wrapped and verified through qemu-x86_64")
    return value


def load_android_oci_contract() -> dict[str, Any]:
    value = json.loads(_resource_bytes("image-manifest.json").decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Android OCI image manifest root must be an object")
    return _validate_contract(value)


def _context_bytes() -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(_REQUIRED):
            payload = _resource_bytes(name)
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(payload))
    return out.getvalue()


def canonical_android_oci_definition() -> AndroidOciDefinition:
    contract = load_android_oci_contract()
    dockerfile = _resource_bytes("Dockerfile")
    validator = _resource_bytes("validate-image.sh")
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    definition_digest = hashlib.sha256(b"\0".join((canonical, dockerfile, validator))).hexdigest()
    context = _context_bytes()
    return AndroidOciDefinition(
        contract=contract,
        definition_digest=definition_digest,
        context_sha256=hashlib.sha256(context).hexdigest(),
        context_size=len(context),
        context_bytes=context,
    )


def android_oci_definition_payload() -> dict[str, Any]:
    definition = canonical_android_oci_definition()
    return {
        "schema_version": 1,
        "kind": "oci-image-definition",
        "ok": True,
        "definition_id": definition.contract["definition_id"],
        "definition_digest": definition.definition_digest,
        "context_sha256": definition.context_sha256,
        "context_size": definition.context_size,
        "reference": definition.reference,
        "architecture": definition.architecture,
        "contract": definition.contract,
    }
