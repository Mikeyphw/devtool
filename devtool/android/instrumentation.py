"""Canonical Android instrumentation orchestrator (AOPS03).

This service owns device policy, installation, runner discovery, execution,
failure evidence and cleanup.  The historical :mod:`devtool.android_instrumentation`
module remains as a compatibility surface, but Devtool runners/CLI use this
orchestrator directly.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from ..models import ProjectConfig
from .device_ops import AndroidDeviceOperationError, AndroidDeviceOperations, capture_failure_evidence
from ._standalone import BuildError, normalize_android_device_test_policy

INSTRUMENTATION_POLICIES = {"disabled", "when-device-present", "required"}


class InstrumentationError(RuntimeError):
    pass


class InstrumentationUnavailable(InstrumentationError):
    pass


def normalize_instrumentation_policy(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("policy") or value.get("mode") or "when-device-present"
    try:
        return normalize_android_device_test_policy(value, default="disabled")
    except BuildError as exc:
        raise InstrumentationError(str(exc)) from exc


def instrumentation_config(config: ProjectConfig | None) -> dict[str, Any]:
    if config is None:
        return {}
    android = config.runner_options.get("android", {})
    android = dict(android) if isinstance(android, dict) else {}
    raw = android.get("instrumentation", {})
    if isinstance(raw, dict):
        result = dict(raw)
    else:
        result = {"policy": raw}
    # Preserve older flat keys as defaults.
    result.setdefault("test_runner", android.get("test_runner"))
    result.setdefault("target_package", android.get("target_package"))
    result.setdefault("keep_installed", android.get("keep_installed", False))
    result.setdefault("capture_logcat_on_failure", android.get("capture_logcat_on_failure", True))
    result.setdefault("capture_screenshot_on_failure", android.get("capture_screenshot_on_failure", True))
    return result


def resolve_instrumentation_policy(config: ProjectConfig | None) -> str:
    return normalize_instrumentation_policy(instrumentation_config(config).get("policy"))


@dataclass(slots=True)
class InstrumentationRequest:
    project: Path
    variant: str = "debug"
    policy: str = "when-device-present"
    serial: str = ""
    device_policy: str = ""
    runner: str = ""
    target_package: str = ""
    classes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    not_annotations: list[str] = field(default_factory=list)
    size: str = ""
    runner_args: dict[str, str] = field(default_factory=dict)
    gradle: str = "./gradlew"
    gradle_args: list[str] = field(default_factory=list)
    aapt2: str = ""
    aapt2_provider: str = "auto"
    skip_build: bool = False
    wait_timeout_seconds: float = 0.0
    cleanup_app: bool = False
    keep_installed: bool = False
    capture_logcat_on_failure: bool = True
    capture_screenshot_on_failure: bool = True
    report: Path | None = None


@dataclass(slots=True)
class InstrumentationResult:
    framework: str = "android-instrumentation"
    status: str = "passed"
    reason: str = ""
    runner: str = ""
    target_package: str = ""
    test_package: str = ""
    device_serial: str = ""
    device_policy: str = ""
    app_apk: str = ""
    test_apk: str = ""
    discovered: int = 0
    selected: int = 0
    executed: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0
    failed_ids: list[str] = field(default_factory=list)
    returncode: int = 0
    duration_ms: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"passed", "skipped"} and self.failed == 0 and self.returncode == 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"ok": self.ok}


def _variant_title(value: str) -> str:
    text = str(value or "debug").strip()
    return text[:1].upper() + text[1:]


def _resolve_gradle(project: Path, configured: str) -> Path | str:
    configured = str(configured or "./gradlew").strip()
    candidate = (project / configured).resolve(strict=False) if not os.path.isabs(configured) else Path(configured)
    if candidate.is_file():
        return candidate
    from shutil import which
    found = which(configured)
    if found:
        return found
    raise InstrumentationError(f"Gradle executable is unavailable: {configured}")


def _find_apks(project: Path, variant: str) -> tuple[Path, Path]:
    low = variant.lower()
    app_candidates = sorted(
        path for path in project.rglob("*.apk")
        if "/build/outputs/apk/" in path.as_posix()
        and "androidtest" not in path.as_posix().lower()
        and low in path.as_posix().lower()
    )
    test_candidates = sorted(
        path for path in project.rglob("*.apk")
        if "/build/outputs/apk/androidtest/" in path.as_posix().lower()
        and low in path.as_posix().lower()
    )
    if not app_candidates:
        raise InstrumentationError(f"No {variant} application APK was found under build/outputs/apk")
    if not test_candidates:
        raise InstrumentationError(f"No {variant} AndroidTest APK was found under build/outputs/apk/androidTest")
    return max(app_candidates, key=lambda p: p.stat().st_mtime_ns), max(test_candidates, key=lambda p: p.stat().st_mtime_ns)


def _parse_runner_list(text: str) -> list[tuple[str, str]]:
    return re.findall(r"instrumentation:([^\s]+)\s+\(target=([^\)]+)\)", text or "")


def _choose_runner(text: str, *, configured: str, target_package: str) -> tuple[str, str]:
    entries = _parse_runner_list(text)
    if configured:
        target = target_package or next((target for runner, target in entries if runner == configured), "")
        return configured, target
    if target_package:
        for runner, target in entries:
            if target == target_package:
                return runner, target
    if len(entries) == 1:
        return entries[0]
    if not entries:
        raise InstrumentationError("No installed instrumentation runner was reported by package manager")
    rendered = ", ".join(f"{runner} -> {target}" for runner, target in entries)
    raise InstrumentationError(f"Multiple instrumentation runners are installed; configure android.instrumentation.test_runner. Found: {rendered}")


def _parse_counts(text: str) -> dict[str, int]:
    numtests = [int(value) for value in re.findall(r"INSTRUMENTATION_STATUS:\s*numtests=(\d+)", text or "")]
    codes = [int(value) for value in re.findall(r"INSTRUMENTATION_STATUS_CODE:\s*(-?\d+)", text or "")]
    started = sum(1 for value in codes if value == 1)
    completed = sum(1 for value in codes if value == 0)
    failed = sum(1 for value in codes if value < 0)
    total = max(numtests or [started, completed + failed, 0])
    passed = max(0, completed - failed)
    if "FAILURES!!!" in (text or "") and failed == 0:
        failed = 1
        passed = max(0, completed - failed)
    return {"discovered": total, "selected": total, "executed": completed + failed, "passed": passed, "failed": failed, "skipped": 0, "total": total}


def _parse_failed_ids(output: str) -> list[str]:
    current_class = ""
    current_test = ""
    failed: list[str] = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if line.startswith("INSTRUMENTATION_STATUS: class="):
            current_class = line.split("=", 1)[1].strip()
        elif line.startswith("INSTRUMENTATION_STATUS: test="):
            current_test = line.split("=", 1)[1].strip()
        elif line.startswith("INSTRUMENTATION_STATUS_CODE:"):
            try:
                code = int(line.split(":", 1)[1].strip())
            except ValueError:
                continue
            if code < 0 and current_test:
                failed.append(f"{current_class}.{current_test}" if current_class else current_test)
    return list(dict.fromkeys(failed))


def _write_report(result: InstrumentationResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


class AndroidInstrumentationOrchestrator:
    def __init__(
        self,
        device_ops: AndroidDeviceOperations,
        *,
        env: Mapping[str, str] | None = None,
        config: ProjectConfig | None = None,
    ) -> None:
        self.device_ops = device_ops
        self.env = dict(os.environ if env is None else env)
        self.config = config

    @classmethod
    def from_config(cls, config: ProjectConfig, *, env: Mapping[str, str] | None = None) -> "AndroidInstrumentationOrchestrator":
        return cls(AndroidDeviceOperations.from_config(config, env=env), env=env, config=config)

    def _build_with_devtool_builder(self, request: InstrumentationRequest) -> None:
        """Build app/test APKs through the canonical staged Android builder."""

        from ..android_integration import android_builder_arguments_for_tasks, plan_android_command
        from .signing import signing_environment_for_tasks

        variant_title = _variant_title(request.variant)
        tasks = [f"assemble{variant_title}", f"assemble{variant_title}AndroidTest"]
        config = self.config
        if config is None:
            android: dict[str, Any] = {
                "variant": request.variant,
                "aapt2": request.aapt2,
                "aapt2_provider": request.aapt2_provider,
            }
            gradle_options: dict[str, Any] = {"args": list(request.gradle_args)}
            configured_gradle = str(request.gradle or "").strip()
            if configured_gradle and configured_gradle not in {"./gradlew", "gradlew"}:
                gradle_options["command"] = configured_gradle
            config = ProjectConfig(
                repo_root=request.project,
                project_root=str(request.project),
                kind="android",
                runner="android",
                variant=request.variant,
                runner_options={"android": android, "gradle": gradle_options},
            )
        plan = plan_android_command(config, "build")
        argv = [
            *plan.argv_prefix,
            *android_builder_arguments_for_tasks(config, tasks, scan_artifacts=False),
        ]
        child_env = signing_environment_for_tasks(config, tasks, env=self.env)
        build = subprocess.run(
            argv,
            cwd=str(plan.project_root),
            text=True,
            capture_output=True,
            check=False,
            env=child_env,
        )
        if build.returncode:
            detail = (build.stderr or build.stdout).strip()
            raise InstrumentationError(detail or f"Devtool Android instrumentation build failed with {build.returncode}")

    def _skip_or_fail(self, request: InstrumentationRequest, reason: str, started: float) -> InstrumentationResult:
        policy = normalize_instrumentation_policy(request.policy)
        if policy == "required":
            return InstrumentationResult(status="failed", reason=reason, failed=1, total=0, returncode=1, duration_ms=int((time.monotonic() - started) * 1000), device_policy=request.device_policy)
        return InstrumentationResult(status="skipped", reason=reason, skipped=1, total=1, returncode=0, duration_ms=int((time.monotonic() - started) * 1000), device_policy=request.device_policy)

    def run(self, request: InstrumentationRequest) -> InstrumentationResult:
        started = time.monotonic()
        policy = normalize_instrumentation_policy(request.policy)
        report_path = request.report or request.project / ".devtool" / "reports" / "android-instrumentation.json"
        if policy == "disabled":
            result = InstrumentationResult(status="skipped", reason="instrumentation policy is disabled", skipped=1, total=1, duration_ms=0)
            _write_report(result, report_path)
            return result

        try:
            if policy == "required" and request.wait_timeout_seconds > 0:
                device = self.device_ops.wait_for_device(serial=request.serial, policy=request.device_policy, timeout=request.wait_timeout_seconds)
            else:
                device = self.device_ops.select_device(serial=request.serial, policy=request.device_policy)
        except Exception as exc:
            result = self._skip_or_fail(request, str(exc), started)
            _write_report(result, report_path)
            return result

        installed_test_package = ""
        target_package = request.target_package
        try:
            if not request.skip_build:
                self._build_with_devtool_builder(request)

            app_apk, test_apk = _find_apks(request.project, request.variant)
            self.device_ops.install(app_apk, serial=device.serial, policy="serial", replace=True, test_only=True)
            self.device_ops.install(test_apk, serial=device.serial, policy="serial", replace=True, test_only=True)

            listed = self.device_ops.shell(["pm", "list", "instrumentation"], serial=device.serial, policy="serial")
            runner, target_package = _choose_runner(listed.stdout + listed.stderr, configured=request.runner, target_package=target_package)
            installed_test_package = runner.split("/", 1)[0]
            args = ["am", "instrument", "-w", "-r"]
            selected_classes = list(request.classes)
            for method in request.methods:
                if "#" in method:
                    selected_classes.append(method)
                elif "." in method:
                    class_name, method_name = method.rsplit(".", 1)
                    selected_classes.append(f"{class_name}#{method_name}")
                else:
                    selected_classes.append(method)
            if selected_classes:
                args.extend(["-e", "class", ",".join(selected_classes)])
            if request.annotations:
                args.extend(["-e", "annotation", ",".join(request.annotations)])
            if request.not_annotations:
                args.extend(["-e", "notAnnotation", ",".join(request.not_annotations)])
            if request.size:
                args.extend(["-e", "size", request.size])
            for key, value in sorted(request.runner_args.items()):
                args.extend(["-e", str(key), str(value)])
            args.append(runner)
            execution = self.device_ops.shell(args, serial=device.serial, policy="serial", timeout=1800, check=False)
            output = execution.stdout + "\n" + execution.stderr
            counts = _parse_counts(output)
            failed_ids = _parse_failed_ids(output)
            failed = execution.returncode != 0 or counts["failed"] > 0
            result = InstrumentationResult(
                status="failed" if failed else "passed",
                runner=runner,
                target_package=target_package,
                test_package=installed_test_package,
                device_serial=device.serial,
                device_policy=request.device_policy,
                app_apk=str(app_apk), test_apk=str(test_apk),
                **counts,
                failed_ids=failed_ids,
                returncode=execution.returncode or (1 if counts["failed"] else 0),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            if failed and (request.capture_logcat_on_failure or request.capture_screenshot_on_failure):
                result.evidence = capture_failure_evidence(
                    self.device_ops,
                    report_dir=report_path.parent / "android-instrumentation-evidence",
                    package=target_package,
                    serial=device.serial,
                    policy="serial",
                    capture_logcat=request.capture_logcat_on_failure,
                    capture_screenshot=request.capture_screenshot_on_failure,
                )
            _write_report(result, report_path)
            return result
        except (InstrumentationError, AndroidDeviceOperationError, OSError) as exc:
            evidence: dict[str, Any] = {}
            if request.capture_logcat_on_failure or request.capture_screenshot_on_failure:
                try:
                    evidence = capture_failure_evidence(
                        self.device_ops,
                        report_dir=report_path.parent / "android-instrumentation-evidence",
                        package=target_package,
                        serial=device.serial,
                        policy="serial",
                        capture_logcat=request.capture_logcat_on_failure,
                        capture_screenshot=request.capture_screenshot_on_failure,
                    )
                except Exception:
                    evidence = {}
            result = InstrumentationResult(status="failed", reason=str(exc), target_package=target_package, test_package=installed_test_package, device_serial=device.serial, device_policy=request.device_policy, failed=1, returncode=1, duration_ms=int((time.monotonic() - started) * 1000), evidence=evidence)
            _write_report(result, report_path)
            return result
        finally:
            if not request.keep_installed:
                if installed_test_package:
                    try:
                        self.device_ops.uninstall(installed_test_package, serial=device.serial, policy="serial", check=False)
                    except Exception:
                        pass
                if request.cleanup_app and target_package:
                    try:
                        self.device_ops.uninstall(target_package, serial=device.serial, policy="serial", check=False)
                    except Exception:
                        pass


def request_from_config(config: ProjectConfig, *, classes: Sequence[str] = (), methods: Sequence[str] = (), annotations: Sequence[str] = (), not_annotations: Sequence[str] = (), size: str = "", runner_args: Mapping[str, str] | None = None, skip_build: bool = False, report: Path | None = None) -> InstrumentationRequest:
    android = config.runner_options.get("android", {})
    android = dict(android) if isinstance(android, dict) else {}
    ins = instrumentation_config(config)
    adb = android.get("adb", {})
    adb = dict(adb) if isinstance(adb, dict) else {}
    project = Path(config.project_root or config.repo_root).expanduser().resolve()
    gradle_options = config.runner_options.get("gradle", {})
    gradle_options = dict(gradle_options) if isinstance(gradle_options, dict) else {}
    gradle_args = gradle_options.get("args", [])
    if isinstance(gradle_args, str):
        gradle_args = [gradle_args]
    return InstrumentationRequest(
        project=project,
        variant=str(config.variant or android.get("variant") or "debug"),
        policy=resolve_instrumentation_policy(config),
        serial=str(config.device_serial or adb.get("serial") or android.get("device_serial") or ""),
        device_policy=str(adb.get("device_policy") or "exactly-one"),
        runner=str(ins.get("test_runner") or ""),
        target_package=str(ins.get("target_package") or ""),
        classes=list(classes), methods=list(methods), annotations=list(annotations), not_annotations=list(not_annotations), size=str(size or ins.get("size") or ""),
        runner_args={str(k): str(v) for k, v in (dict(runner_args) if runner_args is not None else dict(ins.get("runner_args") or {})).items()},
        gradle=str(android.get("gradle") if isinstance(android.get("gradle"), str) else "./gradlew"),
        gradle_args=[str(item) for item in gradle_args if str(item).strip()],
        aapt2=str(android.get("aapt2") or ""),
        aapt2_provider=str(android.get("aapt2_provider") or "auto"),
        skip_build=skip_build,
        wait_timeout_seconds=float(ins.get("wait_timeout_seconds", 30 if resolve_instrumentation_policy(config) == "required" else 0) or 0),
        cleanup_app=bool(ins.get("cleanup_app", False)),
        keep_installed=bool(ins.get("keep_installed", False)),
        capture_logcat_on_failure=bool(ins.get("capture_logcat_on_failure", True)),
        capture_screenshot_on_failure=bool(ins.get("capture_screenshot_on_failure", True)),
        report=report,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Android instrumentation through the Devtool device orchestrator")
    parser.add_argument("project", type=Path)
    parser.add_argument("--variant", default="debug")
    parser.add_argument("--policy", choices=sorted(INSTRUMENTATION_POLICIES), default="when-device-present")
    parser.add_argument("--device-serial", default="")
    parser.add_argument("--device-policy", choices=("exactly-one", "serial", "preferred", "first-physical", "first-emulator"), default="exactly-one")
    parser.add_argument("--runner", default="")
    parser.add_argument("--target-package", default="")
    parser.add_argument("--class", dest="classes", action="append", default=[])
    parser.add_argument("--method", dest="methods", action="append", default=[])
    parser.add_argument("--annotation", action="append", default=[])
    parser.add_argument("--not-annotation", action="append", default=[])
    parser.add_argument("--size", choices=("small", "medium", "large"), default="")
    parser.add_argument("--runner-arg", action="append", default=[])
    parser.add_argument("--gradle", default="./gradlew")
    parser.add_argument("--gradle-arg", action="append", default=[])
    parser.add_argument("--aapt2", default="")
    parser.add_argument("--aapt2-provider", default="auto")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--wait-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--cleanup-app", action="store_true")
    parser.add_argument("--keep-installed", action="store_true")
    parser.add_argument("--no-logcat-on-failure", action="store_true")
    parser.add_argument("--no-screenshot-on-failure", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    return parser


def _runner_args(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise InstrumentationError(f"--runner-arg expects KEY=VALUE, got {raw!r}")
        key, value = raw.split("=", 1)
        if not key.strip():
            raise InstrumentationError("--runner-arg key cannot be empty")
        result[key.strip()] = value
    return result


def main(raw_args: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(raw_args)
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        print(f"Instrumentation project does not exist: {project}", file=sys.stderr)
        return 2
    # Construct a minimal config so canonical ADB/device resolution can still
    # honor environment and project-local SDK discovery for direct module use.
    config = ProjectConfig(repo_root=project, kind="android", runner="android", project_root=str(project), variant=args.variant, runner_options={"android": {"instrumentation": {"policy": args.policy}, "adb": {"device_policy": args.device_policy, "serial": args.device_serial}}})
    try:
        orchestrator = AndroidInstrumentationOrchestrator.from_config(config)
        request = InstrumentationRequest(
            project=project, variant=args.variant, policy=args.policy,
            serial=args.device_serial, device_policy=args.device_policy,
            runner=args.runner, target_package=args.target_package,
            classes=list(args.classes), methods=list(args.methods), annotations=list(args.annotation), not_annotations=list(args.not_annotation), size=args.size,
            runner_args=_runner_args(args.runner_arg), gradle=args.gradle, gradle_args=list(args.gradle_arg), aapt2=args.aapt2, aapt2_provider=args.aapt2_provider, skip_build=args.skip_build,
            keep_installed=args.keep_installed, capture_logcat_on_failure=not args.no_logcat_on_failure,
            wait_timeout_seconds=args.wait_timeout_seconds, cleanup_app=args.cleanup_app,
            capture_screenshot_on_failure=not args.no_screenshot_on_failure, report=args.report,
        )
        result = orchestrator.run(request)
    except (InstrumentationError, AndroidDeviceOperationError) as exc:
        print(f"Instrumentation failed: {exc}", file=sys.stderr)
        return 1
    print("DEVTOOL_INSTRUMENTATION_RESULT " + json.dumps(result.as_dict(), separators=(",", ":")))
    if result.status == "skipped":
        print(f"Instrumentation skipped: {result.reason}")
    elif result.status == "failed":
        print(f"Instrumentation failed: {result.reason or f'{result.failed} test(s) failed'}", file=sys.stderr)
    return 0 if result.ok else (result.returncode or 1)


__all__ = [
    "AndroidInstrumentationOrchestrator", "InstrumentationError", "InstrumentationRequest", "InstrumentationResult",
    "InstrumentationUnavailable", "INSTRUMENTATION_POLICIES", "build_parser", "instrumentation_config", "main",
    "normalize_instrumentation_policy", "request_from_config", "resolve_instrumentation_policy", "_parse_counts", "_parse_failed_ids",
]

if __name__ == "__main__":
    raise SystemExit(main())
