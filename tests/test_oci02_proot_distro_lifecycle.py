from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from devtool.cli import build_parser
from devtool.core.events import EventBus
from devtool import proot_oci, ssh_worker
from devtool.proot_oci import ProotOciRuntimeProbe
from devtool.ssh_tmux_runner import SSHRunnerConfig


def _runtime_stdout(*, nested: bool = False) -> str:
    if nested:
        return "\n".join([
            "proot_distro=/data/data/com.termux/files/usr/bin/proot-distro",
            "proot=/data/data/com.termux/files/usr/bin/proot",
            "proot_distro_version=5.9.0",
            "proot_version=proot 5.4.0",
            "platform=termux",
            "architecture=aarch64",
            "free_storage_kib=123456",
            "nested_proot=1",
            "runtime_root=/data/data/com.termux/files/usr/var/lib/proot-distro",
            "cache_root=/data/data/com.termux/files/usr/var/lib/proot-distro/cache",
            "cache_kib=1024",
            "containers_rc=1",
            "container=PRoot-Distro refuses to run inside another proot (nested proot is unsupported)",
            "images_rc=1",
            "image=PRoot-Distro refuses to run inside another proot (nested proot is unsupported)",
        ]) + "\n"
    return "\n".join([
        "proot_distro=/data/data/com.termux/files/usr/bin/proot-distro",
        "proot=/data/data/com.termux/files/usr/bin/proot",
        "proot_distro_version=5.9.0",
        "proot_version=proot 5.4.0",
        "platform=termux",
        "architecture=aarch64",
        "free_storage_kib=654321",
        "nested_proot=0",
        "runtime_root=/data/data/com.termux/files/usr/var/lib/proot-distro",
        "cache_root=/data/data/com.termux/files/usr/var/lib/proot-distro/cache",
        "cache_kib=4096",
        "containers_rc=0",
        "container=devtool-android",
        "images_rc=0",
        "image=ubuntu:24.04",
        "image=ghcr.io/example/devtool-android:36",
    ]) + "\n"


class _FakePopen:
    next_stdout = b""
    next_stderr = b""
    next_returncode = 0
    calls: list[list[str]] = []
    kwargs_calls: list[dict[str, object]] = []
    pid_seed = 4100

    def __init__(self, argv, **kwargs):
        type(self).pid_seed += 1
        self.pid = type(self).pid_seed
        self.returncode = type(self).next_returncode
        self.argv = list(argv)
        type(self).calls.append(self.argv)
        type(self).kwargs_calls.append(dict(kwargs))

    def communicate(self, timeout=None):
        return type(self).next_stdout, type(self).next_stderr

    def kill(self):
        self.returncode = -9


def _patch_bus(monkeypatch: pytest.MonkeyPatch) -> EventBus:
    bus = EventBus()
    monkeypatch.setattr(proot_oci, "publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    return bus


def _config() -> SSHRunnerConfig:
    return SSHRunnerConfig(
        host="192.168.15.13", port=8022, user="u0_a123", profile="tablet",
        identity="/home/me/.ssh/devtool_tablet", execution_provider="ssh-proot-oci",
    )


def test_oci_cli_surface_matches_roadmap_contract() -> None:
    parser = build_parser()
    assert parser.parse_args(["oci", "doctor", "tablet"]).oci_command == "doctor"
    assert parser.parse_args(["oci", "images", "tablet"]).oci_command == "images"
    assert parser.parse_args(["oci", "containers", "tablet"]).oci_command == "containers"
    boot = parser.parse_args(["oci", "bootstrap", "tablet", "--dry-run"])
    assert boot.oci_dry_run is True
    install = parser.parse_args([
        "oci", "install", "tablet", "ubuntu:24.04", "--name", "android36",
        "--architecture", "aarch64", "--dry-run",
    ])
    assert install.source == "ubuntu:24.04"
    assert install.oci_name == "android36"
    assert install.oci_architecture == "aarch64"
    remove = parser.parse_args(["oci", "remove", "tablet", "ubuntu:24.04", "--image"])
    assert remove.oci_remove_image is True


def test_runtime_probe_parser_tracks_containers_images_cache_and_nested_proot() -> None:
    ready = proot_oci._parse_runtime_probe(_runtime_stdout())
    assert ready.ready is True
    assert ready.platform == "termux"
    assert ready.architecture == "aarch64"
    assert ready.free_storage_kib == 654321
    assert ready.proot_distro_version == "5.9.0"
    assert ready.proot_version == "proot 5.4.0"
    assert ready.containers == ("devtool-android",)
    assert ready.images == ("ubuntu:24.04", "ghcr.io/example/devtool-android:36")
    assert ready.cache_kib == 4096

    nested = proot_oci._parse_runtime_probe(_runtime_stdout(nested=True))
    assert nested.ready is False
    assert nested.nested_proot is True
    assert nested.containers == ()
    assert nested.images == ()


def test_oci_doctor_is_one_canonical_exo_node_with_process_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _patch_bus(monkeypatch)
    _FakePopen.calls.clear()
    _FakePopen.next_stdout = _runtime_stdout().encode()
    _FakePopen.next_stderr = b""
    _FakePopen.next_returncode = 0
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    payload = proot_oci.oci_doctor_payload(_config(), run_id="run-oci02-doctor")
    assert payload["ok"] is True
    assert payload["runtime"]["image_count"] == 2
    snap = bus.execution_store.snapshot("run-oci02-doctor")
    node = snap.node(payload["node_id"])
    assert node.status == "succeeded"
    assert node.progress.indeterminate is True
    assert len(snap.processes) == 1
    proc = snap.processes[0]
    assert proc.node_id == node.node_id
    assert proc.provenance["transport"] == "ssh"
    assert proc.provenance["runtime"] == "proot-distro"
    assert proc.provenance["profile"] == "tablet"


def test_registry_install_uses_stable_exo_node_and_never_forwards_local_registry_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _patch_bus(monkeypatch)
    _FakePopen.calls.clear()
    _FakePopen.kwargs_calls.clear()
    _FakePopen.next_stdout = b"installed\n"
    _FakePopen.next_stderr = b""
    _FakePopen.next_returncode = 0
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)
    monkeypatch.setenv("PD_DOCKER_AUTH", "user:super-secret-token")

    payload = proot_oci.oci_install_payload(
        _config(), "ghcr.io/example/devtool-android:36", run_id="run-install",
        name="android36", architecture="aarch64",
    )
    assert payload["ok"] is True
    assert len(payload["node_ids"]) == 1
    rendered = " ".join(_FakePopen.calls[-1])
    assert "proot-distro" in rendered
    assert "install" in rendered
    assert "--architecture" in rendered
    assert "super-secret-token" not in rendered
    child_env = _FakePopen.kwargs_calls[-1]["env"]
    assert isinstance(child_env, dict)
    assert "PD_DOCKER_AUTH" not in child_env
    snap = bus.execution_store.snapshot("run-install")
    assert snap.node(payload["node_ids"][0]).status == "succeeded"
    assert "super-secret-token" not in str(snap.to_dict())


def test_local_archive_install_plans_upload_then_install_and_dry_run_executes_neither(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bus = _patch_bus(monkeypatch)
    archive = tmp_path / "devtool-android.oci.tar"
    archive.write_bytes(b"oci-archive-bytes")
    _FakePopen.calls.clear()
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    payload = proot_oci.oci_install_payload(
        _config(), str(archive), run_id="run-local-dry", name="android36", dry_run=True,
    )
    assert payload["source_kind"] == "local-archive"
    assert len(payload["node_ids"]) == 2
    assert _FakePopen.calls == []
    snap = bus.execution_store.snapshot("run-local-dry")
    upload, install = [snap.node(node_id) for node_id in payload["node_ids"]]
    assert upload.status == "skipped"
    assert install.status == "skipped"
    assert install.depends_on == (upload.node_id,)
    assert upload.progress.percent is None
    assert install.progress.percent is None


def test_local_archive_real_install_streams_archive_then_runs_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_bus(monkeypatch)
    archive = tmp_path / "image.oci.tar"
    archive.write_bytes(b"123456789")
    _FakePopen.calls.clear()
    _FakePopen.next_stdout = b"ok\n"
    _FakePopen.next_stderr = b""
    _FakePopen.next_returncode = 0
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    payload = proot_oci.oci_install_payload(_config(), str(archive), run_id="run-local")
    assert payload["ok"] is True
    assert payload["source_size"] == 9
    assert len(_FakePopen.calls) == 2
    assert "cat >" in " ".join(_FakePopen.calls[0])
    assert "proot-distro" in " ".join(_FakePopen.calls[1])
    assert "$HOME/.cache/devtool/oci/uploads/" in " ".join(_FakePopen.calls[1])


def test_bootstrap_is_single_planned_exo_operation_and_reports_change(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _patch_bus(monkeypatch)
    _FakePopen.calls.clear()
    _FakePopen.next_stdout = (
        b"changed=1\nproot_distro=/data/data/com.termux/files/usr/bin/proot-distro\n"
        b"proot=/data/data/com.termux/files/usr/bin/proot\n"
    )
    _FakePopen.next_stderr = b""
    _FakePopen.next_returncode = 0
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    payload = proot_oci.oci_bootstrap_payload(_config(), run_id="run-bootstrap")
    assert payload["changed"] is True
    assert "pkg install -y proot-distro" in " ".join(_FakePopen.calls[0])
    snap = bus.execution_store.snapshot("run-bootstrap")
    assert snap.node(payload["node_ids"][0]).status == "succeeded"
    assert len(snap.processes) == 1


def test_remove_image_uses_documented_remove_image_form(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_bus(monkeypatch)
    _FakePopen.calls.clear()
    _FakePopen.next_stdout = b""
    _FakePopen.next_stderr = b""
    _FakePopen.next_returncode = 0
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)
    payload = proot_oci.oci_remove_payload(
        _config(), "ubuntu:24.04", run_id="run-remove", image=True,
    )
    assert payload["target_kind"] == "image"
    rendered = " ".join(_FakePopen.calls[-1])
    assert "remove --image" in rendered


def test_worker_probe_inventories_proot_distro_as_optional_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "\n".join([
        "platform=termux", "os_name=Linux", "architecture=aarch64", "cpu_count=8",
        "memory_kib=8000000", "free_storage_kib=4000000", "home=/home/u", "prefix=/prefix",
        "shell=/bin/sh", "package_manager=pkg", "tool_python=/prefix/bin/python",
        "tool_tmux=/prefix/bin/tmux", "tool_rsync=/prefix/bin/rsync", "tool_git=/prefix/bin/git",
        "tool_java=", "tool_gradle=", "tool_proot_distro=/prefix/bin/proot-distro",
        "tool_proot=/prefix/bin/proot", "version_python=Python 3.14", "version_tmux=tmux 3.5",
        "version_rsync=rsync 3", "version_git=git 2", "version_java=", "version_gradle=",
        "version_proot_distro=5.9.0", "version_proot=proot 5.4.0",
    ]) + "\n"
    monkeypatch.setattr(
        ssh_worker, "_run_remote",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""),
    )
    probe = ssh_worker.probe_worker(_config())
    assert probe.ready is True
    assert probe.tools["proot_distro"].endswith("proot-distro")
    assert probe.tools["proot"].endswith("proot")
    assert probe.versions["proot_distro"] == "5.9.0"


def test_source_classification_matches_proot_distro_explicit_path_rule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    archive = tmp_path / "ubuntu"
    archive.write_bytes(b"archive")
    monkeypatch.chdir(tmp_path)

    kind, path = proot_oci._source_kind("ubuntu")
    assert kind == "registry"
    assert path is None

    kind, path = proot_oci._source_kind("./ubuntu")
    assert kind == "local-archive"
    assert path == archive.resolve()


def test_runtime_probe_script_refuses_nested_inventory_and_supports_linux_xdg_roots() -> None:
    script = proot_oci._runtime_probe_script(strict_inventory="images")
    nested_guard = script.index('if [ "$nested_proot" = 1 ]')
    list_call = script.index('$pd list --quiet')
    assert nested_guard < list_call
    assert 'data_home="${XDG_DATA_HOME:-$HOME/.local/share}"' in script
    assert 'cache_home="${XDG_CACHE_HOME:-$HOME/.cache}"' in script
    assert 'images) exit "$images_rc"' in script

    linux = proot_oci._parse_runtime_probe("\n".join([
        "proot_distro=/usr/bin/proot-distro",
        "proot=/usr/bin/proot",
        "platform=linux",
        "architecture=x86_64",
        "free_storage_kib=42",
        "nested_proot=0",
        "runtime_root=/home/worker/.local/share/proot-distro",
        "cache_root=/home/worker/.cache/proot-distro",
        "cache_kib=17",
        "containers_rc=0",
        "images_rc=0",
    ]) + "\n")
    assert linux.runtime_root == "/home/worker/.local/share/proot-distro"
    assert linux.cache_root == "/home/worker/.cache/proot-distro"


def test_dry_run_nodes_are_skipped_without_start_or_fake_process_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bus = _patch_bus(monkeypatch)
    archive = tmp_path / "dry.oci.tar"
    archive.write_bytes(b"dry-run")
    _FakePopen.calls.clear()
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    payload = proot_oci.oci_install_payload(
        _config(), str(archive), run_id="run-dry-truth", dry_run=True,
    )
    snap = bus.execution_store.snapshot("run-dry-truth")
    assert _FakePopen.calls == []
    assert snap.processes == ()
    for node_id in payload["node_ids"]:
        node = snap.node(node_id)
        assert node.status == "skipped"
        assert not node.started_at
        assert node.progress.percent is None
        assert node.progress.indeterminate is False
        assert not node.progress.detail


def test_local_archive_staging_uses_full_sha_verification_atomic_publish_and_nested_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _patch_bus(monkeypatch)
    archive = tmp_path / "image.oci.tar"
    archive.write_bytes(b"verified-content")
    digest = proot_oci._sha256_file(archive)
    _FakePopen.calls.clear()
    _FakePopen.kwargs_calls.clear()
    _FakePopen.next_stdout = b"ok\n"
    _FakePopen.next_stderr = b""
    _FakePopen.next_returncode = 0
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    payload = proot_oci.oci_install_payload(_config(), str(archive), run_id="run-stage-integrity")
    upload = " ".join(_FakePopen.calls[0])
    install = " ".join(_FakePopen.calls[1])
    assert f"sha256-{digest}" in upload
    assert f"sha256-{digest}" in install
    assert digest[:16] + "-image.oci.tar" not in upload
    assert 'tmp="$p.part.$$"' in upload
    assert "sha256sum" in upload
    assert "SHA-256 mismatch" in upload
    assert 'mv -f "$tmp" "$p"' in upload
    assert "PROOT_TMP_DIR" in upload and "PROOT_LOADER" in upload
    assert "PROOT_TMP_DIR" in install and "PROOT_LOADER" in install
    assert payload["source_sha256"] == digest
    assert payload["source_size"] == len(b"verified-content")
    assert payload["staged_source"].endswith(f"sha256-{digest}")


def test_upload_failure_blocks_dependent_install_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bus = _patch_bus(monkeypatch)
    archive = tmp_path / "broken.oci.tar"
    archive.write_bytes(b"broken")
    _FakePopen.calls.clear()
    _FakePopen.next_stdout = b""
    _FakePopen.next_stderr = b"transfer failed"
    _FakePopen.next_returncode = 65
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    with pytest.raises(ValueError, match="transfer failed"):
        proot_oci.oci_install_payload(_config(), str(archive), run_id="run-upload-block")
    snap = bus.execution_store.snapshot("run-upload-block")
    upload = next(node for node in snap.nodes if "upload-archive" in node.node_id)
    install = next(node for node in snap.nodes if ":install:" in node.node_id)
    assert upload.status == "failed"
    assert install.status == "blocked"
    assert install.blocked_by == (upload.node_id,)
    assert "archive staging" in install.blocked_reason.lower()
    assert len(_FakePopen.calls) == 1


def test_process_spawn_failure_is_typed_and_closes_node(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _patch_bus(monkeypatch)

    class _SpawnFailure:
        def __init__(self, *args, **kwargs):
            raise OSError("ssh executable unavailable")

    monkeypatch.setattr(proot_oci.subprocess, "Popen", _SpawnFailure)
    with pytest.raises(ValueError, match="process launch error"):
        proot_oci.oci_remove_payload(_config(), "container1", run_id="run-spawn-fail")

    snap = bus.execution_store.snapshot("run-spawn-fail")
    node = snap.nodes[0]
    assert node.status == "failed"
    assert node.exit_code == 127
    assert len(snap.processes) == 0
    assert len(snap.diagnostics) == 1
    assert snap.diagnostics[0].code == "OCI_REMOVE_SPAWN"
    assert snap.diagnostics[0].failure_kind == "process_launch_error"


def test_runtime_failure_evidence_is_redacted_before_execution_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _patch_bus(monkeypatch)
    _FakePopen.calls.clear()
    _FakePopen.next_stdout = b""
    _FakePopen.next_stderr = b"registry rejected https://alice:super-secret-token@example.invalid/v2/\n"
    _FakePopen.next_returncode = 1
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    with pytest.raises(ValueError) as excinfo:
        proot_oci.oci_remove_payload(_config(), "container1", run_id="run-redacted")
    snap = bus.execution_store.snapshot("run-redacted")
    serialized = str(snap.to_dict())
    assert "super-secret-token" not in serialized
    assert "super-secret-token" not in str(excinfo.value)
    assert snap.diagnostics
    assert "super-secret-token" not in snap.diagnostics[0].message
    assert "super-secret-token" not in " ".join(snap.diagnostics[0].evidence)


def test_bootstrap_and_remove_scripts_refuse_nested_proot_before_runtime_use(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_bus(monkeypatch)
    _FakePopen.calls.clear()
    _FakePopen.next_stdout = b"changed=0\nproot_distro=/bin/proot-distro\nproot=/bin/proot\n"
    _FakePopen.next_stderr = b""
    _FakePopen.next_returncode = 0
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    proot_oci.oci_bootstrap_payload(_config(), run_id="run-bootstrap-guard")
    bootstrap = " ".join(_FakePopen.calls[-1])
    assert bootstrap.index("PROOT_TMP_DIR") < bootstrap.index("pkg install -y proot-distro")
    assert "termux_score" in bootstrap
    assert "native Termux worker" in bootstrap

    proot_oci.oci_remove_payload(_config(), "container1", run_id="run-remove-guard")
    remove = " ".join(_FakePopen.calls[-1])
    assert remove.index("PROOT_TMP_DIR") < remove.index("remove container1")


def test_remove_container_name_is_validated_before_remote_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_bus(monkeypatch)
    _FakePopen.calls.clear()
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)
    with pytest.raises(ValueError, match="container name"):
        proot_oci.oci_remove_payload(_config(), "bad/name", run_id="run-invalid-remove")
    assert _FakePopen.calls == []


def test_proot_detection_honors_upstream_pd_proot_bin_override() -> None:
    runtime_script = proot_oci._runtime_probe_script()
    worker_script = ssh_worker._probe_script()
    assert 'proot_bin="${PD_PROOT_BIN:-}"' in runtime_script
    assert 'tool_proot="${PD_PROOT_BIN:-}"' in worker_script
    assert 'if [ -z "$proot_bin" ] || [ ! -x "$proot_bin" ]' in runtime_script
    assert 'if [ -z "$tool_proot" ] || [ ! -x "$tool_proot" ]' in worker_script


def test_oci_doctor_guidance_does_not_offer_termux_bootstrap_on_regular_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_bus(monkeypatch)
    probe = ProotOciRuntimeProbe(
        available=False,
        proot_distro="",
        proot="",
        proot_distro_version="",
        proot_version="",
        platform="linux",
        architecture="x86_64",
        free_storage_kib=100,
        containers=(),
        images=(),
        container_rc=127,
        image_rc=127,
        container_error="",
        image_error="",
        nested_proot=False,
        cache_kib=0,
        runtime_root="/home/u/.local/share/proot-distro",
        cache_root="/home/u/.cache/proot-distro",
    )
    monkeypatch.setattr(proot_oci, "_probe_runtime_exo", lambda *a, **k: (probe, "node"))
    payload = proot_oci.oci_doctor_payload(_config(), run_id="run-linux-guidance")
    assert payload["ok"] is False
    assert payload["bootstrap_supported"] is False
    assert "provision proot-distro/proot" in payload["detail"]
    assert "oci bootstrap" not in payload["detail"]


def test_oci_doctor_marks_partial_inventory_degraded_and_emits_typed_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _patch_bus(monkeypatch)
    _FakePopen.calls.clear()
    _FakePopen.next_stdout = "\n".join([
        "proot_distro=/usr/bin/proot-distro",
        "proot=/usr/bin/proot",
        "proot_distro_version=5.9.0",
        "proot_version=proot 5.1",
        "platform=linux",
        "architecture=x86_64",
        "free_storage_kib=999",
        "nested_proot=0",
        "runtime_root=/home/u/.local/share/proot-distro",
        "cache_root=/home/u/.cache/proot-distro",
        "cache_kib=123",
        "containers_rc=1",
        "container=permission denied",
        "images_rc=0",
        "image=ubuntu:24.04",
    ]).encode() + b"\n"
    _FakePopen.next_stderr = b""
    _FakePopen.next_returncode = 0
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    payload = proot_oci.oci_doctor_payload(_config(), run_id="run-doctor-degraded")
    assert payload["ok"] is False
    assert payload["runtime"]["ready"] is True
    assert payload["runtime"]["inventory_healthy"] is False
    assert payload["runtime"]["container_rc"] == 1
    assert payload["runtime"]["container_error"] == "permission denied"
    assert "inventory is incomplete" in payload["detail"]
    snap = bus.execution_store.snapshot("run-doctor-degraded")
    assert snap.node(payload["node_id"]).status == "succeeded"
    assert any(diag.code == "OCI_CONTAINER_INVENTORY" for diag in snap.diagnostics)
    warning = next(diag for diag in snap.diagnostics if diag.code == "OCI_CONTAINER_INVENTORY")
    assert warning.severity == "warning"
    assert warning.failure_kind == "oci_inventory_error"


def test_local_source_disappearance_gets_typed_source_failure_and_blocks_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bus = _patch_bus(monkeypatch)
    archive = tmp_path / "vanish.oci.tar"
    archive.write_bytes(b"vanish")
    real_hash = proot_oci._sha256_file

    def _hash_then_remove(path: Path) -> str:
        digest = real_hash(path)
        path.unlink()
        return digest

    monkeypatch.setattr(proot_oci, "_sha256_file", _hash_then_remove)
    _FakePopen.calls.clear()
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _FakePopen)

    with pytest.raises(ValueError, match="input open error"):
        proot_oci.oci_install_payload(_config(), str(archive), run_id="run-source-vanished")
    snap = bus.execution_store.snapshot("run-source-vanished")
    upload = next(node for node in snap.nodes if "upload-archive" in node.node_id)
    install = next(node for node in snap.nodes if ":install:" in node.node_id)
    assert upload.status == "failed"
    assert upload.exit_code == 66
    assert install.status == "blocked"
    assert any(diag.code == "OCI_ARCHIVE_UPLOAD_SOURCE" for diag in snap.diagnostics)
    assert _FakePopen.calls == []
