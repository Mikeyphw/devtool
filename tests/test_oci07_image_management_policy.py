from __future__ import annotations

import json
import subprocess

import pytest

from devtool.execution_providers import SSH_PROOT_OCI
from devtool.core.events import EventBus
from devtool.oci_image import canonical_android_oci_definition
from devtool.proot_oci import (
    ProotOciRuntimeProbe,
    _canonical_image_status,
    _image_update_steps,
    oci_image_status_payload,
    oci_prune_payload,
    oci_update_payload,
)
from devtool.ssh_tmux_runner import SSHRunnerConfig


def _cfg(**overrides) -> SSHRunnerConfig:
    values = dict(
        host="tablet", port=8022, user="u0_a123", profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="~/.cache/devtool/workspaces",
        sync_mode="incremental", sync_delete=True, sync_default_excludes=True,
        sync_excludes=(), bootstrap_policy="missing", execution_provider=SSH_PROOT_OCI,
        oci_container="devtool-android-api36", oci_image="devtool/android-build:arm64-api36-v2",
    )
    values.update(overrides)
    return SSHRunnerConfig(**values)


def _probe(*, containers=(), images=(), available=True, nested=False, containers_rc=0, images_rc=0) -> ProotOciRuntimeProbe:
    return ProotOciRuntimeProbe(
        available=available,
        proot_distro="/data/data/com.termux/files/usr/bin/proot-distro" if available else "",
        proot="/data/data/com.termux/files/usr/bin/proot" if available else "",
        proot_distro_version="1.12.0",
        proot_version="5.1.107",
        platform="Linux",
        architecture="aarch64",
        free_storage_kib=1000000,
        containers=tuple(containers),
        images=tuple(images),
        container_rc=containers_rc,
        image_rc=images_rc,
        container_error="" if containers_rc == 0 else "container failure",
        image_error="" if images_rc == 0 else "image failure",
        nested_proot=nested,
        cache_kib=42,
        runtime_root="/data/data/com.termux/files/usr/var/lib/proot-distro",
        cache_root="/data/data/com.termux/files/home/.cache/proot-distro",
    )


def test_oci07_status_reports_canonical_image_and_container_without_mutation() -> None:
    payload = _canonical_image_status(
        _probe(containers=("devtool-android-api36",), images=("devtool/android-build:arm64-api36-v2",)),
        _cfg(),
    )
    assert payload["kind"] == "oci-image-status"
    assert payload["status"] == "ready"
    assert payload["image_cached"] is True
    assert payload["container_installed"] is True
    assert payload["changed"] is False
    assert payload["recommended_action"] == "validate-before-use"


def test_oci07_update_plan_is_plan_first_and_immutable_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.proot_oci as proot_oci

    monkeypatch.setattr(proot_oci, "_probe_runtime_exo", lambda cfg, run_id, action, strict_inventory="", **kwargs: (_probe(), "node-status"))
    payload = oci_update_payload(_cfg(), run_id="run-1")
    assert payload["kind"] == "oci-image-update-plan"
    assert payload["apply"] is False
    assert payload["changed"] is False
    actions = [step["action"] for step in payload["steps"]]
    assert actions == ["build", "install", "validate"]
    assert all("policy" in step and step["policy"] for step in payload["steps"])


def test_oci07_update_apply_reuses_canonical_build_install_validate_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.proot_oci as proot_oci

    monkeypatch.setattr(proot_oci, "_probe_runtime_exo", lambda cfg, run_id, action, strict_inventory="", **kwargs: (_probe(), "node-status"))
    calls: list[dict[str, object]] = []

    def fake_build(config, *, run_id, tag, install_as, dry_run, no_cache):
        calls.append({"tag": tag, "install_as": install_as, "dry_run": dry_run, "no_cache": no_cache})
        return {"kind": "oci-image-build", "ok": True, "install_as": install_as, "reference": tag}

    monkeypatch.setattr(proot_oci, "oci_build_payload", fake_build)
    payload = oci_update_payload(_cfg(), run_id="run-1", apply=True, force=True)
    assert payload["kind"] == "oci-image-update"
    assert payload["changed"] is True
    assert calls == [{
        "tag": "devtool/android-build:arm64-api36-v2",
        "install_as": "devtool-android-api36",
        "dry_run": False,
        "no_cache": True,
    }]


def test_oci07_prune_plan_keeps_only_canonical_image_and_does_not_remove_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.proot_oci as proot_oci

    monkeypatch.setattr(
        proot_oci,
        "_probe_runtime_exo",
        lambda cfg, run_id, action, strict_inventory="", **kwargs: (
            _probe(images=("devtool/android-build:arm64-api36-v2", "old/image:tag")),
            "node-prune",
        ),
    )
    payload = oci_prune_payload(_cfg(), run_id="run-1")
    assert payload["kind"] == "oci-image-prune-plan"
    assert payload["apply"] is False
    assert payload["changed"] is False
    assert payload["keep"] == ["devtool/android-build:arm64-api36-v2"]
    assert payload["removable"] == ["old/image:tag"]


def test_oci07_status_blocks_when_inventory_is_not_trustworthy(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.proot_oci as proot_oci

    monkeypatch.setattr(
        proot_oci,
        "_probe_runtime_exo",
        lambda cfg, run_id, action, strict_inventory="", **kwargs: (_probe(images_rc=2), "node-status"),
    )
    payload = oci_image_status_payload(_cfg(), run_id="run-1")
    assert payload["ok"] is False
    assert payload["status"] == "inventory-blocked"
    assert payload["recommended_action"] == "repair-runtime-inventory"


class _QueuePopen:
    outputs: list[tuple[bytes, bytes, int]] = []
    calls: list[list[str]] = []
    pid_seed = 12000

    def __init__(self, argv, **kwargs):
        type(self).pid_seed += 1
        self.pid = type(self).pid_seed
        self.argv = list(argv)
        type(self).calls.append(self.argv)
        if not type(self).outputs:
            raise AssertionError("unexpected subprocess")
        self._stdout, self._stderr, self.returncode = type(self).outputs.pop(0)

    def communicate(self, input=None, timeout=None):
        self.input = input
        return self._stdout, self._stderr

    def kill(self):
        self.returncode = -9


def _patch_event_bus(monkeypatch: pytest.MonkeyPatch) -> EventBus:
    import devtool.proot_oci as proot_oci

    bus = EventBus()
    monkeypatch.setattr(proot_oci, "publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    return bus


def _runtime_probe_stdout(*, containers=(), images=()) -> bytes:
    lines = [
        "proot_distro=/data/data/com.termux/files/usr/bin/proot-distro",
        "proot=/data/data/com.termux/files/usr/bin/proot",
        "proot_distro_version=1.12.0",
        "proot_version=5.1.107",
        "platform=termux",
        "architecture=aarch64",
        "free_storage_kib=1000000",
        "containers_rc=0",
        "images_rc=0",
        "runtime_root=/data/data/com.termux/files/usr/var/lib/proot-distro",
        "cache_root=/data/data/com.termux/files/home/.cache/proot-distro",
    ]
    lines.extend(f"container={value}" for value in containers)
    lines.extend(f"image={value}" for value in images)
    return ("\n".join(lines) + "\n").encode()


def test_oci07_update_apply_plans_build_nodes_before_graph_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.proot_oci as proot_oci

    bus = _patch_event_bus(monkeypatch)
    definition = canonical_android_oci_definition()
    image_sha = "a" * 64
    metadata = json.dumps({
        "schema_version": 1,
        "definition_digest": definition.definition_digest,
        "context_sha256": definition.context_sha256,
    }, separators=(",", ":"), sort_keys=True)
    _QueuePopen.calls.clear()
    _QueuePopen.outputs = [
        (_runtime_probe_stdout(), b"", 0),
        (f"context_path=/home/u/.cache/devtool/oci/definitions/sha256-{definition.definition_digest}\ncontext_sha256={definition.context_sha256}\ndefinition_digest={definition.definition_digest}\n".encode(), b"", 0),
        ((f"image_reference={definition.reference}\n"
          f"image_digest=sha256:{'b' * 64}\n"
          f"image_archive=/home/u/.cache/devtool/oci/builds/image.oci.tar\n"
          f"image_sha256={image_sha}\nimage_size=1234\n"
          f"DEVTOOL_OCI_ARTIFACT\t/home/u/.cache/devtool/oci/builds/image.oci.tar\t{image_sha}\t1234\toci-archive\n").encode(), b"", 0),
        (b"installed_container=devtool-android-api36\n", b"", 0),
        (("status=ready\narchitecture=aarch64\npython=3.12.3\nnode=v22.20.0\napi=36\n"
          "build_tools=36.0.0\nndk=29.0.14206865\nndk_host=qemu-x86_64-wrapped-verified\n"
          "DEVTOOL_OCI_METADATA=" + metadata + "\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)

    payload = oci_update_payload(_cfg(), run_id="run-oci07-update-apply", apply=True)

    assert payload["kind"] == "oci-image-update"
    assert payload["changed"] is True
    snap = bus.execution_store.snapshot("run-oci07-update-apply")
    assert snap.graph_sealed is True
    expected = {
        proot_oci._stable_node_id(_cfg(), "image-status-runtime-probe"),
        proot_oci._stable_node_id(_cfg(), "stage-image-definition", definition.context_sha256),
        proot_oci._stable_node_id(_cfg(), "build-android-image", f"{definition.definition_digest}|{definition.reference}|0"),
        proot_oci._stable_node_id(_cfg(), "install-built-image", f"{definition.context_sha256}|devtool-android-api36"),
        proot_oci._stable_node_id(_cfg(), "validate-android-image", f"devtool-android-api36|{definition.definition_digest}"),
    }
    node_ids = {node.node_id for node in snap.nodes}
    assert expected.issubset(node_ids)
    assert all(snap.node(node_id).status == "succeeded" for node_id in expected)


def test_oci09_diagnostics_composes_doctor_and_image_status_before_graph_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.proot_oci as proot_oci

    bus = _patch_event_bus(monkeypatch)
    _QueuePopen.calls.clear()
    _QueuePopen.outputs = [
        (_runtime_probe_stdout(), b"", 0),
        (_runtime_probe_stdout(containers=("devtool-android-api36",), images=("devtool/android-build:arm64-api36-v2",)), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)

    doctor = proot_oci.oci_doctor_payload(_cfg(), run_id="run-oci09-diag", seal_graph=False)
    status = proot_oci.oci_image_status_payload(_cfg(), run_id="run-oci09-diag")

    assert doctor["ok"] is True
    assert status["status"] == "ready"
    snap = bus.execution_store.snapshot("run-oci09-diag")
    assert snap.graph_sealed is True
    node_ids = {node.node_id for node in snap.nodes}
    assert proot_oci._stable_node_id(_cfg(), "doctor-runtime-probe") in node_ids
    assert proot_oci._stable_node_id(_cfg(), "image-status-runtime-probe") in node_ids
