"""Reusable Android device lifecycle operations and smoke gates (AOPS03)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping, Sequence

from ..models import ProjectConfig
from .devices import AndroidDevice, AndroidDeviceError, AndroidDeviceManager
from .capabilities import AndroidExecutionContext
from .operations import resolve_android_operations
from .signing import AndroidSigningManager


class AndroidDeviceOperationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceOperationResult:
    operation: str
    status: str
    serial: str
    returncode: int = 0
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"ok": self.ok}


@dataclass(frozen=True, slots=True)
class SmokeGateResult:
    gate: str
    status: str
    serial: str
    package: str = ""
    artifact: str = ""
    duration_ms: int = 0
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"ok": self.ok}


class AndroidDeviceOperations:
    def __init__(
        self,
        manager: AndroidDeviceManager,
        *,
        context: AndroidExecutionContext | None = None,
        env: Mapping[str, str] | None = None,
        default_policy: str = "exactly-one",
        default_serial: str = "",
        preferred: str = "",
    ) -> None:
        self.manager = manager
        self.context = context
        self.env = dict(os.environ if env is None else env)
        self.default_policy = default_policy
        self.default_serial = default_serial
        self.preferred = preferred

    @classmethod
    def from_config(cls, config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> "AndroidDeviceOperations":
        android = config.runner_options.get("android", {})
        android = dict(android) if isinstance(android, dict) else {}
        adb = android.get("adb", {})
        adb = dict(adb) if isinstance(adb, dict) else {}
        operations = resolve_android_operations(
            config,
            env=env,
            adb_provider=str(adb.get("provider") or "") or None,
            adb_path=str(adb.get("path") or "") or None,
        )
        return cls(
            operations.devices,
            context=operations.context,
            env=env,
            default_policy=str(adb.get("device_policy") or "exactly-one"),
            default_serial=str(adb.get("serial") or config.device_serial or ""),
            preferred=str(adb.get("preferred") or ""),
        )

    def select_device(self, *, serial: str = "", policy: str = "", enrich: bool = False) -> AndroidDevice:
        selected = self.manager.select(
            policy or ("serial" if (serial or self.default_serial) else self.default_policy),
            serial=serial or self.default_serial,
            preferred=self.preferred,
        )
        if len(selected) != 1:
            raise AndroidDeviceOperationError("Operation requires exactly one selected Android device")
        device = selected[0]
        if enrich:
            try:
                enriched = [item for item in self.manager.list_devices(enrich=True) if item.serial == device.serial]
                if enriched:
                    device = enriched[0]
            except AndroidDeviceError:
                pass
        return device

    def wait_for_device(self, *, serial: str = "", policy: str = "", timeout: float = 30.0, poll_interval: float = 1.0) -> AndroidDevice:
        """Wait for a usable selected device while surfacing authorization failures clearly."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        last_detail = "No usable Android device is connected"
        while True:
            try:
                return self.select_device(serial=serial, policy=policy)
            except (AndroidDeviceError, AndroidDeviceOperationError) as exc:
                last_detail = str(exc)
            try:
                visible = self.manager.list_devices()
            except AndroidDeviceError as exc:
                visible = []
                last_detail = str(exc)
            wanted = serial or self.default_serial
            if wanted:
                visible = [item for item in visible if item.serial == wanted or item.transport_id == wanted]
            unauthorized = [item for item in visible if item.state == "unauthorized"]
            if unauthorized:
                names = ", ".join(item.serial for item in unauthorized)
                raise AndroidDeviceOperationError(f"Android device authorization is required for {names}; accept the ADB authorization prompt and retry")
            if time.monotonic() >= deadline:
                states = ", ".join(f"{item.serial}={item.state}" for item in visible) or "none"
                raise AndroidDeviceOperationError(f"Timed out waiting for a usable Android device ({last_detail}; visible states: {states})")
            time.sleep(max(0.05, float(poll_interval)))

    def _run(
        self,
        operation: str,
        args: Sequence[str],
        *,
        serial: str = "",
        policy: str = "",
        timeout: float = 60,
        check: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> DeviceOperationResult:
        device = self.select_device(serial=serial, policy=policy)
        argv = [*self.manager.command_prefix(device.serial), *[str(item) for item in args]]
        started = time.monotonic()
        try:
            result = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=timeout, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AndroidDeviceOperationError(f"{operation} failed to execute: {exc}") from exc
        duration = int((time.monotonic() - started) * 1000)
        detail = ""
        status = "passed" if result.returncode == 0 else "failed"
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()[-1] if (result.stderr or result.stdout).strip() else f"exit {result.returncode}"
        outcome = DeviceOperationResult(
            operation, status, device.serial, result.returncode, duration,
            result.stdout, result.stderr, detail, metadata or {},
        )
        if check and not outcome.ok:
            raise AndroidDeviceOperationError(f"{operation} failed on {device.serial}: {detail}")
        return outcome

    def _run_bytes(self, operation: str, args: Sequence[str], *, serial: str = "", policy: str = "", timeout: float = 30) -> tuple[AndroidDevice, bytes]:
        device = self.select_device(serial=serial, policy=policy)
        argv = [*self.manager.command_prefix(device.serial), *[str(item) for item in args]]
        try:
            result = subprocess.run(argv, capture_output=True, check=False, timeout=timeout, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AndroidDeviceOperationError(f"{operation} failed to execute: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip() or f"exit {result.returncode}"
            raise AndroidDeviceOperationError(f"{operation} failed on {device.serial}: {detail}")
        return device, result.stdout

    def install(self, apk: str | Path, *, serial: str = "", policy: str = "", replace: bool = True, test_only: bool = True, downgrade: bool = False, grant_all: bool = False) -> DeviceOperationResult:
        path = Path(apk).expanduser().resolve(strict=False)
        if not path.is_file():
            raise AndroidDeviceOperationError(f"APK does not exist: {path}")
        args = ["install"]
        if replace:
            args.append("-r")
        if test_only:
            args.append("-t")
        if downgrade:
            args.append("-d")
        if grant_all:
            args.append("-g")
        args.append(str(path))
        return self._run("install", args, serial=serial, policy=policy, timeout=180, metadata={"apk": str(path)})

    def uninstall(self, package: str, *, serial: str = "", policy: str = "", keep_data: bool = False, check: bool = True) -> DeviceOperationResult:
        args = ["uninstall"]
        if keep_data:
            args.append("-k")
        args.append(package)
        return self._run("uninstall", args, serial=serial, policy=policy, timeout=60, check=check, metadata={"package": package})

    def shell(self, argv: Sequence[str], *, serial: str = "", policy: str = "", timeout: float = 60, check: bool = True) -> DeviceOperationResult:
        return self._run("shell", ["shell", *argv], serial=serial, policy=policy, timeout=timeout, check=check, metadata={"argv": list(argv)})

    def push(self, source: str | Path, destination: str, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        source_path = Path(source).expanduser().resolve(strict=False)
        if not source_path.exists():
            raise AndroidDeviceOperationError(f"Push source does not exist: {source_path}")
        return self._run("push", ["push", str(source_path), destination], serial=serial, policy=policy, timeout=180)

    def pull(self, source: str, destination: str | Path, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        destination_path = Path(destination).expanduser().resolve(strict=False)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        return self._run("pull", ["pull", source, str(destination_path)], serial=serial, policy=policy, timeout=180)

    def force_stop(self, package: str, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        return self.shell(["am", "force-stop", package], serial=serial, policy=policy)

    def clear_data(self, package: str, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        return self.shell(["pm", "clear", package], serial=serial, policy=policy)

    def launch(self, package: str, *, activity: str = "", serial: str = "", policy: str = "", wait: bool = True) -> DeviceOperationResult:
        if activity:
            component = activity if "/" in activity else f"{package}/{activity}"
            args = ["am", "start"]
            if wait:
                args.append("-W")
            args.extend(["-n", component])
        else:
            # monkey uses the launcher category without needing to know an activity.
            args = ["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]
        result = self.shell(args, serial=serial, policy=policy, timeout=60)
        return DeviceOperationResult("launch", result.status, result.serial, result.returncode, result.duration_ms, result.stdout, result.stderr, result.detail, {"package": package, "activity": activity})

    def restart(self, package: str, *, activity: str = "", serial: str = "", policy: str = "") -> list[DeviceOperationResult]:
        stopped = self.force_stop(package, serial=serial, policy=policy)
        launched = self.launch(package, activity=activity, serial=serial, policy=policy)
        return [stopped, launched]

    def grant(self, package: str, permission: str, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        return self.shell(["pm", "grant", package, permission], serial=serial, policy=policy)

    def revoke(self, package: str, permission: str, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        return self.shell(["pm", "revoke", package, permission], serial=serial, policy=policy)

    def package_info(self, package: str, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        return self.shell(["dumpsys", "package", package], serial=serial, policy=policy, timeout=30)

    def process_state(self, package: str, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        return self.shell(["pidof", package], serial=serial, policy=policy, check=False)

    def logcat(self, *, serial: str = "", policy: str = "", lines: int = 400, clear: bool = False) -> DeviceOperationResult:
        if clear:
            self._run("logcat-clear", ["logcat", "-c"], serial=serial, policy=policy, check=False)
        return self._run("logcat", ["logcat", "-d", "-t", str(max(1, int(lines)))], serial=serial, policy=policy, timeout=30, check=False)

    def screenshot(self, destination: str | Path, *, serial: str = "", policy: str = "") -> DeviceOperationResult:
        target = Path(destination).expanduser().resolve(strict=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        device, data = self._run_bytes("screenshot", ["exec-out", "screencap", "-p"], serial=serial, policy=policy)
        target.write_bytes(data)
        return DeviceOperationResult("screenshot", "passed", device.serial, 0, int((time.monotonic() - started) * 1000), detail=str(target), metadata={"path": str(target), "bytes": len(data)})

    def apk_metadata(self, apk: str | Path) -> dict[str, str]:
        path = Path(apk).expanduser().resolve(strict=False)
        context_tool = None
        # AndroidDeviceManager does not retain the full execution context; its
        # ADB resolution metadata may carry no AAPT2. Resolve from the active
        # environment for metadata-only inspection.
        context = self.context or resolve_android_operations(None, env=self.env).context
        for name in ("aapt2", "aapt"):
            try:
                tool = context.tool(name)
            except KeyError:
                continue
            if tool.path and tool.compatible is not False:
                context_tool = tool.path
                break
        if not context_tool:
            raise AndroidDeviceOperationError("aapt2/aapt is required to infer APK package metadata")
        try:
            result = subprocess.run([context_tool, "dump", "badging", str(path)], text=True, capture_output=True, check=False, timeout=30, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AndroidDeviceOperationError(f"Could not inspect APK metadata: {exc}") from exc
        if result.returncode:
            raise AndroidDeviceOperationError((result.stderr or result.stdout).strip() or f"aapt2 exit {result.returncode}")
        text = result.stdout + "\n" + result.stderr
        package = re.search(r"package:\s+name='([^']+)'", text)
        launch = re.search(r"launchable-activity:\s+name='([^']+)'", text)
        version_code = re.search(r"versionCode='([^']*)'", text)
        version_name = re.search(r"versionName='([^']*)'", text)
        return {
            "package": package.group(1) if package else "",
            "activity": launch.group(1) if launch else "",
            "version_code": version_code.group(1) if version_code else "",
            "version_name": version_name.group(1) if version_name else "",
        }

    def install_smoke(self, apk: str | Path, *, package: str = "", activity: str = "", serial: str = "", policy: str = "", keep_installed: bool = True) -> SmokeGateResult:
        started = time.monotonic()
        path = Path(apk).expanduser().resolve(strict=False)
        metadata: dict[str, str] = {}
        if not package:
            metadata = self.apk_metadata(path)
            package = metadata.get("package", "")
            activity = activity or metadata.get("activity", "")
        if not package:
            raise AndroidDeviceOperationError("install-smoke requires a package ID or an APK whose package can be inspected")
        install = self.install(path, serial=serial, policy=policy)
        launch = self.launch(package, activity=activity, serial=install.serial, policy="serial")
        process = self.process_state(package, serial=install.serial, policy="serial")
        ok = install.ok and launch.ok
        evidence: dict[str, Any] = {
            "install": install.as_dict(), "launch": launch.as_dict(), "process": process.as_dict(), "apk_metadata": metadata,
        }
        if not keep_installed:
            evidence["cleanup"] = self.uninstall(package, serial=install.serial, policy="serial", check=False).as_dict()
        return SmokeGateResult("install-smoke", "passed" if ok else "failed", install.serial, package, str(path), int((time.monotonic() - started) * 1000), "installed and launched" if ok else "install/launch failed", evidence)

    def upgrade_smoke(self, old_apk: str | Path, new_apk: str | Path, *, package: str = "", activity: str = "", serial: str = "", policy: str = "") -> SmokeGateResult:
        started = time.monotonic()
        if not package:
            package = self.apk_metadata(new_apk).get("package", "")
        if not package:
            raise AndroidDeviceOperationError("upgrade-smoke requires a package ID")
        initial = self.install(old_apk, serial=serial, policy=policy, replace=True)
        upgraded = self.install(new_apk, serial=initial.serial, policy="serial", replace=True)
        launched = self.launch(package, activity=activity, serial=initial.serial, policy="serial")
        ok = initial.ok and upgraded.ok and launched.ok
        return SmokeGateResult("upgrade-smoke", "passed" if ok else "failed", initial.serial, package, str(Path(new_apk)), int((time.monotonic() - started) * 1000), "upgrade and launch succeeded" if ok else "upgrade smoke failed", {"initial_install": initial.as_dict(), "upgrade_install": upgraded.as_dict(), "launch": launched.as_dict()})

    def cold_start_smoke(self, package: str, *, activity: str = "", serial: str = "", policy: str = "") -> SmokeGateResult:
        started = time.monotonic()
        device = self.select_device(serial=serial, policy=policy)
        stop = self.force_stop(package, serial=device.serial, policy="serial")
        launch = self.launch(package, activity=activity, serial=device.serial, policy="serial")
        ok = stop.ok and launch.ok
        return SmokeGateResult("cold-start-smoke", "passed" if ok else "failed", device.serial, package, duration_ms=int((time.monotonic() - started) * 1000), detail="cold start succeeded" if ok else "cold start failed", evidence={"force_stop": stop.as_dict(), "launch": launch.as_dict()})

    def restart_smoke(self, package: str, *, activity: str = "", serial: str = "", policy: str = "") -> SmokeGateResult:
        started = time.monotonic()
        device = self.select_device(serial=serial, policy=policy)
        operations = self.restart(package, activity=activity, serial=device.serial, policy="serial")
        ok = all(item.ok for item in operations)
        return SmokeGateResult("restart-smoke", "passed" if ok else "failed", device.serial, package, duration_ms=int((time.monotonic() - started) * 1000), detail="restart succeeded" if ok else "restart failed", evidence={"operations": [item.as_dict() for item in operations]})

    def restore_restart_smoke(self, package: str, *, activity: str = "", serial: str = "", policy: str = "") -> SmokeGateResult:
        """Restart without clearing app data and prove the package remains installed/readable."""
        started = time.monotonic()
        device = self.select_device(serial=serial, policy=policy)
        before = self.package_info(package, serial=device.serial, policy="serial")
        operations = self.restart(package, activity=activity, serial=device.serial, policy="serial")
        after = self.package_info(package, serial=device.serial, policy="serial")
        ok = before.ok and after.ok and all(item.ok for item in operations)
        return SmokeGateResult("restore-restart-smoke", "passed" if ok else "failed", device.serial, package, duration_ms=int((time.monotonic() - started) * 1000), detail="restart preserved installed state" if ok else "restore/restart smoke failed", evidence={"before": before.as_dict(), "operations": [item.as_dict() for item in operations], "after": after.as_dict()})

    def process_death_smoke(self, package: str, *, activity: str = "", serial: str = "", policy: str = "") -> SmokeGateResult:
        started = time.monotonic()
        device = self.select_device(serial=serial, policy=policy)
        # `am kill` simulates background process death without clearing app data.
        killed = self.shell(["am", "kill", package], serial=device.serial, policy="serial", check=False)
        launched = self.launch(package, activity=activity, serial=device.serial, policy="serial")
        ok = launched.ok
        return SmokeGateResult("process-death-smoke", "passed" if ok else "failed", device.serial, package, duration_ms=int((time.monotonic() - started) * 1000), detail="process-death relaunch succeeded" if ok else "process-death relaunch failed", evidence={"kill": killed.as_dict(), "launch": launched.as_dict()})

    def signed_artifact_smoke(self, artifact: str | Path, *, expected_sha256: str = "") -> SmokeGateResult:
        started = time.monotonic()
        context = self.context or resolve_android_operations(None, env=self.env).context
        verification = AndroidSigningManager(context, env=self.env).verify_artifact(artifact, expected_sha256=expected_sha256)
        return SmokeGateResult("signed-artifact-smoke", "passed" if verification.ok else "failed", "", artifact=str(Path(artifact)), duration_ms=int((time.monotonic() - started) * 1000), detail=verification.detail, evidence={"signature": verification.as_dict()})


def capture_failure_evidence(
    operations: AndroidDeviceOperations,
    *,
    report_dir: str | Path,
    package: str = "",
    serial: str = "",
    policy: str = "",
    capture_logcat: bool = True,
    capture_screenshot: bool = True,
) -> dict[str, Any]:
    root = Path(report_dir).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {}
    if capture_logcat:
        try:
            logcat = operations.logcat(serial=serial, policy=policy, lines=600)
            path = root / "logcat.txt"
            path.write_text(logcat.stdout + logcat.stderr, encoding="utf-8", errors="replace")
            evidence["logcat"] = str(path)
        except Exception as exc:
            evidence["logcat_error"] = str(exc)
    if capture_screenshot:
        try:
            screenshot = root / "screenshot.png"
            result = operations.screenshot(screenshot, serial=serial, policy=policy)
            evidence["screenshot"] = result.metadata.get("path", str(screenshot))
        except Exception as exc:
            evidence["screenshot_error"] = str(exc)
    if package:
        try:
            info = operations.package_info(package, serial=serial, policy=policy)
            path = root / "package.txt"
            path.write_text(info.stdout + info.stderr, encoding="utf-8", errors="replace")
            evidence["package"] = str(path)
        except Exception as exc:
            evidence["package_error"] = str(exc)
    return evidence


__all__ = [
    "AndroidDeviceOperationError", "AndroidDeviceOperations", "DeviceOperationResult", "SmokeGateResult",
    "capture_failure_evidence",
]
