from __future__ import annotations

"""ANDROID-G1 authoritative Android environment/device milestone proof.

The gate composes the X01/X02 authorities and exercises process boundaries on
native Termux.  It intentionally uses a deterministic process-level ADB fixture
for topology/lease semantics because one physical host cannot simultaneously be
USB, TCP, emulator and root-loopback; the host's real ADB binary is still probed
separately.  The Gradle producer process is the host's real Gradle executable.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .android.environment import EnvironmentView, resolve_android_environment
from .android.execution_snapshot import (
    acquire_device_lease,
    build_android_execution_snapshot,
    build_artifact_pair,
    device_requirements_from_config,
    resolve_android_transport,
)
from .gradle_ingress import converge_gradle_spec
from .gradle_takeover import GradleOrigin
from .models import CommandSpec, ProjectConfig
from .runners import run_specs


class AndroidG1Error(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AndroidG1Error(message)


def _is_termux() -> bool:
    prefix = str(os.environ.get("PREFIX") or "")
    return prefix.startswith("/data/data/com.termux/") or "/com.termux/" in sys.executable


def _tool(name: str) -> str:
    allow_test = os.environ.get("DEVTOOL_ANDROID_G1_ALLOW_TEST_HOST") == "1"
    override = str(os.environ.get(f"DEVTOOL_ANDROID_G1_{name.upper()}") or "").strip()
    if not _is_termux() and not allow_test:
        raise AndroidG1Error("ANDROID-G1 authoritative process proof requires native Termux")
    if override:
        if not allow_test:
            raise AndroidG1Error(f"DEVTOOL_ANDROID_G1_{name.upper()} is test-only and forbidden for authoritative validation")
        candidate = Path(override).expanduser().resolve(strict=False)
        _require(candidate.is_file() and os.access(candidate, os.X_OK), f"{name} test override is not executable: {candidate}")
        return str(candidate)
    resolved = shutil.which(name)
    _require(bool(resolved), f"ANDROID-G1 requires {name} on PATH")
    return str(Path(str(resolved)).resolve(strict=False))


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_sdk(root: Path) -> Path:
    sdk = root / "sdk"
    platform = sdk / "platforms" / "android-36"
    platform.mkdir(parents=True)
    (platform / "android.jar").write_bytes(b"android-g1")
    bt = sdk / "build-tools" / "36.0.0"
    (bt / "lib").mkdir(parents=True)
    (bt / "lib" / "d8.jar").write_bytes(b"d8")
    (bt / "source.properties").write_text("Pkg.Revision=36.0.0\n", encoding="utf-8")
    for name in ("aapt2", "aidl", "zipalign", "split-select", "apksigner"):
        _exe(bt / name, "exit 0\n")
    return sdk


def _write_project(root: Path) -> None:
    (root / "settings.gradle").write_text("rootProject.name='android-g1'\n", encoding="utf-8")
    manifest = root / "src" / "main" / "AndroidManifest.xml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('<manifest package="devtool.gate"/>\n', encoding="utf-8")
    (root / "build.gradle").write_text(
        """
if (false) {
    compileSdk 36
    buildToolsVersion '36.0.0'
}
tasks.register('gateProducer') {
    doLast {
        def out = file('build/outputs/apk/release/app-release.apk')
        out.parentFile.mkdirs()
        out.text = 'android-g1-app'
    }
}
""",
        encoding="utf-8",
    )


def _write_adb_fixture(root: Path) -> Path:
    log = root / "adb.log"
    body = f'''printf '%s\\n' "$*" >> {str(log)!r}
if [ "${{1:-}}" = "version" ]; then
  printf 'Android Debug Bridge version 1.0.41\\nVersion 36.0.0\\n'; exit 0
fi
if [ "${{1:-}}" = "devices" ]; then
  printf 'List of devices attached\\n127.0.0.1:5555 device product:gate model:Gate device:gate transport_id:7\\n'; exit 0
fi
if [ "${{1:-}}" = "-s" ]; then
  shift 2
  if [ "${{1:-}}" = "shell" ]; then
    shift
    case "$*" in
      'getprop ro.build.version.sdk') printf '35\\n' ;;
      'getprop ro.product.cpu.abi') printf 'arm64-v8a\\n' ;;
      'getprop ro.product.manufacturer') printf 'Devtool\\n' ;;
      'getprop sys.boot_completed') printf '1\\n' ;;
      'dumpsys battery') printf 'level: 88\\n' ;;
      'dumpsys power') printf 'mWakefulness=Awake\\n' ;;
      *) printf '\\n' ;;
    esac
    exit 0
  fi
fi
exit 0
'''
    return _exe(root / "adb", body)


def _config(root: Path, sdk: Path, gradle: str, adb: Path) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="android",
        runner="android",
        selected_target="app",
        variant="release",
        execution_environment="native-termux" if _is_termux() else "native-linux",
        runner_options={
            "android": {
                "sdk_root": str(sdk),
                "build_tools": {"version": "36.0.0"},
                "release": {"variant": "release", "module": "app"},
                "adb": {
                    "provider": "path",
                    "path": str(adb),
                    "device_policy": "serial",
                    "serial": "127.0.0.1:5555",
                    "min_api": 34,
                    "abi": "arm64-v8a",
                    "transport": "tcp",
                },
            },
            "gradle": {
                "command": gradle,
                "configuration_cache": "off",
                "build_cache": False,
                "daemon": False,
                "vfs": False,
                "parallel": False,
                "max_workers": 1,
                "heap_mb": 512,
                "takeover": {"enabled": True, "mode": "manage", "scope": "devtool"},
            },
        },
    )


def run_gate() -> dict[str, Any]:
    real_gradle = _tool("gradle")
    real_java = _tool("java")
    real_adb = _tool("adb")
    adb_version = subprocess.run([real_adb, "version"], text=True, capture_output=True, check=False, timeout=20)
    _require(adb_version.returncode == 0, f"real ADB probe failed: {(adb_version.stderr or adb_version.stdout)[-1000:]}")
    connected = subprocess.run([real_adb, "devices", "-l"], text=True, capture_output=True, check=False, timeout=20)
    _require(connected.returncode == 0, f"real ADB inventory probe failed: {(connected.stderr or connected.stdout)[-1000:]}")

    with tempfile.TemporaryDirectory(prefix="devtool-android-g1-") as td:
        root = Path(td)
        project = root / "project"
        project.mkdir()
        _write_project(project)
        sdk = _write_sdk(root)
        adb_fixture = _write_adb_fixture(root)
        config = _config(project, sdk, real_gradle, adb_fixture)

        raw = CommandSpec(
            "android-g1-producer",
            [real_gradle, "gateProducer", "--offline", "--console=plain"],
            cwd=str(project),
            env={"GRADLE_USER_HOME": str(root / "gradle-home")},
        )
        rendered = converge_gradle_spec(config, raw, default_origin=GradleOrigin.ANDROID)
        results, _ = run_specs(config, rendered, verbose=False, continue_on_error=False)
        _require(len(results) == 1 and results[0].returncode == 0, "canonical Android-G1 Gradle producer failed")
        result = results[0]
        receipt = dict(result.metadata.get("gradle_execution_receipt") or {})
        _require(receipt.get("process_executed") is True and receipt.get("success") is True, "Gradle receipt did not prove a real successful process")

        apk = project / "build" / "outputs" / "apk" / "release" / "app-release.apk"
        _require(apk.is_file(), "real Gradle producer did not materialize the expected APK fixture")
        digest = sha256(apk.read_bytes()).hexdigest()
        receipt["artifact_refs"] = [str(apk)]
        receipt["producer_artifact_refs"] = {"gateProducer": [str(apk)]}

        env = dict(os.environ)
        env["ANDROID_SDK_ROOT"] = str(sdk)
        env["PATH"] = os.pathsep.join([str(sdk / "build-tools" / "36.0.0"), str(adb_fixture.parent), env.get("PATH", "")])
        _request, qualification, environment = resolve_android_environment(
            config, action="release", env=env, gradle_metadata=result.metadata, device_required=False, fail_closed=True,
        )
        _require(qualification.ok and environment is not None, "canonical Android environment did not qualify")

        pair = build_artifact_pair(
            repo_root=project,
            module="app",
            variant_identity=environment.lanes["release"].identity,
            environment=environment,
            app={"type": "apk", "path": str(apk), "sha256": digest, "producer_task": "gateProducer"},
            gradle_receipt=receipt,
            source_identity="android-g1-source",
            config_identity="android-g1-config",
        )
        view = EnvironmentView.from_config(config, env=env)
        transport = resolve_android_transport(config, environment, action="smoke", env_view=view, device_required=True)
        _require(transport is not None, "device-required action did not resolve AndroidTransport")
        requirements = device_requirements_from_config(config)
        lease = acquire_device_lease(transport, requirements, env=env, attempt_id="android-g1")
        _require(lease.serial == "127.0.0.1:5555" and lease.connection_type == "tcp", "DeviceLease did not retain the selected TCP serial")

        # Fail readiness before any install/lifecycle side effect can execute.
        strict = device_requirements_from_config(config, overrides={"min_api": 99})
        blocked = False
        try:
            acquire_device_lease(transport, strict, env=env, attempt_id="android-g1-blocked")
        except RuntimeError:
            blocked = True
        _require(blocked, "device readiness mismatch did not fail closed")
        adb_log = (root / "adb.log").read_text(encoding="utf-8")
        _require(" install " not in f" {adb_log} ", "device side effect occurred after failed readiness")

        snapshot = build_android_execution_snapshot(
            target="app", action="release-device", environment=environment,
            gradle_receipts=[receipt], artifact_pair=pair, device_lease=lease,
            source_identity="android-g1-source", config_identity="android-g1-config",
        )
        _require(snapshot.artifact_pair is not None and snapshot.device_lease is not None, "execution snapshot lost artifact/device lineage")
        _require(environment.gradle_jdk_identity in snapshot.jdk_identities, "Gradle/JDK authority was not linked into AndroidExecutionSnapshot")

        authority = Path(__file__).resolve().parent / "android" / "_standalone.py"
        staged = Path(__file__).resolve().parent / "resources" / "oci" / "android-arm64" / "build-apk"
        _require(authority.read_bytes() == staged.read_bytes(), "staged Android runtime diverged from authoritative standalone runtime")

        return {
            "schema_version": 1,
            "gate": "ANDROID-G1",
            "status": "passed",
            "host": {"termux": _is_termux(), "gradle": real_gradle, "java": real_java, "adb": real_adb},
            "real_adb_inventory": connected.stdout.strip(),
            "environment_id": environment.environment_id,
            "environment_components": dict(environment.component_identities),
            "gradle_receipt_id": receipt.get("receipt_id"),
            "artifact_pair_id": pair.pair_id,
            "artifact_sha256": pair.app.sha256,
            "transport_id": transport.transport_id,
            "device_lease_id": lease.lease_id,
            "device_serial": lease.serial,
            "snapshot_id": snapshot.snapshot_id,
            "readiness_side_effect_blocked": blocked,
            "standalone_runtime_sealed": True,
        }


def main() -> int:
    try:
        print(json.dumps(run_gate(), indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ANDROID-G1 failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
