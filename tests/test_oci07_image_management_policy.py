from __future__ import annotations

import subprocess

import pytest

from devtool.execution_providers import SSH_PROOT_OCI
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

    monkeypatch.setattr(proot_oci, "_probe_runtime_exo", lambda cfg, run_id, action, strict_inventory="": (_probe(), "node-status"))
    payload = oci_update_payload(_cfg(), run_id="run-1")
    assert payload["kind"] == "oci-image-update-plan"
    assert payload["apply"] is False
    assert payload["changed"] is False
    actions = [step["action"] for step in payload["steps"]]
    assert actions == ["build", "install", "validate"]
    assert all("policy" in step and step["policy"] for step in payload["steps"])


def test_oci07_update_apply_reuses_canonical_build_install_validate_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.proot_oci as proot_oci

    monkeypatch.setattr(proot_oci, "_probe_runtime_exo", lambda cfg, run_id, action, strict_inventory="": (_probe(), "node-status"))
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
        lambda cfg, run_id, action, strict_inventory="": (
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
        lambda cfg, run_id, action, strict_inventory="": (_probe(images_rc=2), "node-status"),
    )
    payload = oci_image_status_payload(_cfg(), run_id="run-1")
    assert payload["ok"] is False
    assert payload["status"] == "inventory-blocked"
    assert payload["recommended_action"] == "repair-runtime-inventory"
