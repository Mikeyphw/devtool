"""Canonical Android instrumentation orchestrator (AOPS03).

This service owns device policy, installation, runner discovery, execution,
failure evidence and cleanup.  The historical :mod:`devtool.android_instrumentation`
module remains as a compatibility surface, but Devtool runners/CLI use this
orchestrator directly.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import hashlib
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
X04_TYPED_RUNNER_ARGS = {"numShards", "shardIndex", "targetInstrumentation"}


def _stable(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8", "surrogatepass")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class InstrumentationIntent:
    intent_id: str
    variant: str
    policy: str
    requested_runner: str
    requested_target_package: str
    classes: tuple[str, ...]
    methods: tuple[str, ...]
    annotations: tuple[str, ...]
    not_annotations: tuple[str, ...]
    size: str
    runner_args: tuple[tuple[str, str], ...]
    cleanup_app: bool
    keep_installed: bool
    capture_logcat_on_failure: bool
    capture_screenshot_on_failure: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "intent_id": self.intent_id, "variant": self.variant, "policy": self.policy,
            "requested_runner": self.requested_runner, "requested_target_package": self.requested_target_package,
            "classes": list(self.classes), "methods": list(self.methods), "annotations": list(self.annotations),
            "not_annotations": list(self.not_annotations), "size": self.size,
            "runner_args": {key: value for key, value in self.runner_args}, "cleanup_app": self.cleanup_app,
            "keep_installed": self.keep_installed, "capture_logcat_on_failure": self.capture_logcat_on_failure,
            "capture_screenshot_on_failure": self.capture_screenshot_on_failure,
        }


@dataclass(frozen=True, slots=True)
class ResolvedInstrumentationPlan:
    plan_id: str
    intent_id: str
    environment_id: str
    artifact_pair_id: str
    device_lease_id: str
    destination_id: str
    runner: str
    target_package: str
    test_package: str
    command: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "schema_version": 1, "command": list(self.command)}


@dataclass(frozen=True, slots=True)
class InstrumentationExecutionReceipt:
    receipt_id: str
    intent_id: str
    plan_id: str
    status: str
    failure_kind: str
    device_lease_id: str
    artifact_pair_id: str
    destination_id: str
    install_results: tuple[Mapping[str, Any], ...]
    execution_result: Mapping[str, Any]
    evidence: Mapping[str, Any]
    cleanup_results: tuple[Mapping[str, Any], ...]
    cleanup_authority: Mapping[str, Any]
    snapshot_id: str = ""
    snapshot_attachment_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "receipt_id": self.receipt_id, "intent_id": self.intent_id, "plan_id": self.plan_id,
            "status": self.status, "failure_kind": self.failure_kind, "device_lease_id": self.device_lease_id,
            "artifact_pair_id": self.artifact_pair_id, "destination_id": self.destination_id,
            "install_results": [dict(item) for item in self.install_results], "execution_result": dict(self.execution_result),
            "evidence": dict(self.evidence), "cleanup_results": [dict(item) for item in self.cleanup_results],
            "cleanup_authority": dict(self.cleanup_authority), "snapshot_id": self.snapshot_id,
            "snapshot_attachment_id": self.snapshot_attachment_id,
        }


def instrumentation_intent(request: "InstrumentationRequest") -> InstrumentationIntent:
    bypass = sorted(X04_TYPED_RUNNER_ARGS.intersection(str(k) for k in request.runner_args))
    if bypass:
        raise InstrumentationError(
            "ANDROID-X04 advanced runner arguments must use typed sharding/orchestrator planning, not raw runner_args: "
            + ", ".join(bypass)
        )
    payload = {
        "variant": str(request.variant), "policy": normalize_instrumentation_policy(request.policy),
        "requested_runner": str(request.runner or ""), "requested_target_package": str(request.target_package or ""),
        "classes": list(request.classes), "methods": list(request.methods), "annotations": list(request.annotations),
        "not_annotations": list(request.not_annotations), "size": str(request.size or ""),
        "runner_args": {str(k): str(v) for k, v in sorted(request.runner_args.items())},
        "cleanup_app": bool(request.cleanup_app), "keep_installed": bool(request.keep_installed),
        "capture_logcat_on_failure": bool(request.capture_logcat_on_failure),
        "capture_screenshot_on_failure": bool(request.capture_screenshot_on_failure),
    }
    return InstrumentationIntent(
        intent_id=_stable(payload), variant=payload["variant"], policy=payload["policy"],
        requested_runner=payload["requested_runner"], requested_target_package=payload["requested_target_package"],
        classes=tuple(payload["classes"]), methods=tuple(payload["methods"]), annotations=tuple(payload["annotations"]),
        not_annotations=tuple(payload["not_annotations"]), size=payload["size"],
        runner_args=tuple(sorted(payload["runner_args"].items())), cleanup_app=payload["cleanup_app"],
        keep_installed=payload["keep_installed"], capture_logcat_on_failure=payload["capture_logcat_on_failure"],
        capture_screenshot_on_failure=payload["capture_screenshot_on_failure"],
    )


def resolve_instrumentation_plan(
    intent: InstrumentationIntent, *, environment_id: str, artifact_pair_id: str, device_lease_id: str,
    destination_id: str, runner: str, target_package: str, test_package: str, command: Sequence[str],
) -> ResolvedInstrumentationPlan:
    if not environment_id or not artifact_pair_id or not device_lease_id:
        raise InstrumentationError("Instrumentation plan requires canonical environment, ArtifactPair and DeviceLease identity")
    payload = {
        "intent_id": intent.intent_id, "environment_id": environment_id, "artifact_pair_id": artifact_pair_id,
        "device_lease_id": device_lease_id, "destination_id": destination_id, "runner": runner,
        "target_package": target_package, "test_package": test_package, "command": list(command),
    }
    return ResolvedInstrumentationPlan(plan_id=_stable(payload), command=tuple(str(x) for x in command), **{k:v for k,v in payload.items() if k != "command"})

_FALSE_STRINGS = {"", "0", "false", "no", "off", "disabled", "none"}
_TRUE_STRINGS = {"1", "true", "yes", "on", "enabled"}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _FALSE_STRINGS:
            return False
        if text in _TRUE_STRINGS:
            return True
        return default
    return bool(value)


def _coerce_float(value: Any, default: float = 0.0, *, field: str = "value") -> float:
    if value in {None, ""}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InstrumentationError(f"{field} must be a number") from exc


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


def gradle_receipts_from_current_run(repo_root: Path, *, run_id: str) -> tuple[dict[str, Any], ...]:
    """Recover canonical Gradle receipts already emitted in the active RUN.

    Project-runner instrumentation executes the Gradle producer spec and the
    device spec as separate processes.  The execution journal is the canonical
    cross-process hand-off: it already contains the TaskFinished metadata with
    the GradleExecutionReceipt, so instrumentation must consume that authority
    rather than reconstructing or dropping producer lineage.
    """
    resolved_run = str(run_id or "").strip()
    if not resolved_run:
        return ()
    try:
        from ..core.execution_recovery import load_execution_journal
        from ..core_runtime import private_run_root
        events, _skipped = load_execution_journal(
            private_run_root(Path(repo_root)) / resolved_run, run_id=resolved_run, strict=False,
        )
    except Exception:
        return ()
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        metadata = getattr(event, "metadata", {})
        if not isinstance(metadata, Mapping):
            continue
        receipt = metadata.get("gradle_execution_receipt")
        if not isinstance(receipt, Mapping):
            continue
        payload = {str(key): value for key, value in dict(receipt).items()}
        receipt_id = str(payload.get("receipt_id") or "").strip()
        key = receipt_id or _stable(payload)
        if key in seen:
            continue
        seen.add(key)
        receipts.append(payload)
    return tuple(receipts)


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
    failure_kind: str = ""
    intent_id: str = ""
    plan_id: str = ""
    receipt_id: str = ""
    artifact_pair_id: str = ""
    device_lease_id: str = ""
    execution_snapshot_id: str = ""
    execution_snapshot_attachment_id: str = ""
    cleanup_status: str = "not-requested"
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


def _module_key_for_apk(project: Path, path: Path) -> tuple[str, ...]:
    try:
        rel = path.resolve(strict=False).relative_to(project.resolve(strict=False))
    except ValueError:
        rel = path
    parts = rel.parts
    lowered = [part.lower() for part in parts]
    if "build" in lowered:
        return tuple(parts[:lowered.index("build")])
    return tuple(parts[:-1])


def _find_apks(project: Path, variant: str) -> tuple[Path, Path]:
    low = str(variant or "debug").lower()
    app_candidates: list[Path] = []
    test_candidates: list[Path] = []
    for path in project.rglob("*.apk"):
        normalized = path.as_posix().lower()
        if "/build/outputs/apk/" not in normalized or low not in normalized:
            continue
        if "/androidtest/" in normalized or "androidtest" in path.name.lower() or path.name.lower().endswith("-test.apk"):
            test_candidates.append(path)
        else:
            app_candidates.append(path)
    if not app_candidates:
        raise InstrumentationError(f"No {variant} application APK was found under build/outputs/apk")
    if not test_candidates:
        raise InstrumentationError(f"No {variant} AndroidTest APK was found under build/outputs/apk/androidTest")
    by_module: dict[tuple[str, ...], list[Path]] = {}
    for app in app_candidates:
        by_module.setdefault(_module_key_for_apk(project, app), []).append(app)
    pairs: list[tuple[Path, Path]] = []
    for test in test_candidates:
        module = _module_key_for_apk(project, test)
        candidates = by_module.get(module, [])
        if candidates:
            pairs.append((max(candidates, key=lambda p: p.stat().st_mtime_ns), test))
    if not pairs:
        app_modules = ", ".join("/".join(key) or "." for key in sorted(by_module))
        test_modules = ", ".join("/".join(_module_key_for_apk(project, item)) or "." for item in test_candidates)
        raise InstrumentationError(
            "No same-module application APK matched the AndroidTest APK "
            f"for variant {variant!r} (app modules: {app_modules}; test modules: {test_modules})"
        )
    return max(pairs, key=lambda pair: max(pair[0].stat().st_mtime_ns, pair[1].stat().st_mtime_ns))

def _parse_runner_list(text: str) -> list[tuple[str, str]]:
    return re.findall(r"instrumentation:([^\s]+)\s+\(target=([^\)]+)\)", text or "")


def _choose_runner(text: str, *, configured: str, target_package: str) -> tuple[str, str]:
    entries = _parse_runner_list(text)
    configured = str(configured or "").strip()
    target_package = str(target_package or "").strip()
    if configured:
        for runner, target in entries:
            if runner == configured:
                if target_package and target != target_package:
                    raise InstrumentationError(
                        f"Configured instrumentation runner {configured!r} targets {target!r}, not {target_package!r}"
                    )
                return runner, target
        rendered = ", ".join(f"{runner} -> {target}" for runner, target in entries) or "none"
        raise InstrumentationError(f"Configured instrumentation runner {configured!r} is not installed. Found: {rendered}")
    if target_package:
        for runner, target in entries:
            if target == target_package:
                return runner, target
        rendered = ", ".join(f"{runner} -> {target}" for runner, target in entries) or "none"
        raise InstrumentationError(f"No installed instrumentation runner targets {target_package!r}. Found: {rendered}")
    if len(entries) == 1:
        return entries[0]
    if not entries:
        raise InstrumentationError("No installed instrumentation runner was reported by package manager")
    rendered = ", ".join(f"{runner} -> {target}" for runner, target in entries)
    raise InstrumentationError(f"Multiple instrumentation runners are installed; configure android.instrumentation.test_runner. Found: {rendered}")

def _parse_counts(text: str) -> dict[str, int]:
    """Parse AndroidJUnitRunner raw status codes without treating skips as failures.

    Android instrumentation status codes are:
      1  start
      0  success
     -1  error
     -2  failure
     -3  ignored
     -4  assumption failure

    Unknown negative completion codes remain fail-closed as failures.
    """
    numtests = [int(value) for value in re.findall(r"INSTRUMENTATION_STATUS:\s*numtests=(\d+)", text or "")]
    codes = [int(value) for value in re.findall(r"INSTRUMENTATION_STATUS_CODE:\s*(-?\d+)", text or "")]
    started = sum(1 for value in codes if value == 1)
    passed = sum(1 for value in codes if value == 0)
    skipped = sum(1 for value in codes if value in {-3, -4})
    failed = sum(1 for value in codes if value < 0 and value not in {-3, -4})
    executed = passed + failed + skipped
    total = max(numtests or [started, executed, 0])
    if "FAILURES!!!" in (text or "") and failed == 0:
        failed = 1
        executed = max(executed, passed + failed + skipped)
        total = max(total, executed)
    return {
        "discovered": total,
        "selected": total,
        "executed": executed,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
    }

def _parse_failed_ids(output: str) -> list[str]:
    """Return only genuine AndroidJUnitRunner failure/error IDs.

    Ignored (-3) and assumption-failure (-4) completions are skips, not failures.
    """
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
            if code < 0 and code not in {-3, -4} and current_test:
                value = f"{current_class}.{current_test}" if current_class else current_test
                failed.append(value)
    return list(dict.fromkeys(failed))

def _instrumentation_process_crash(output: str) -> bool:
    text = str(output or "")
    low = text.lower()
    return (
        "process crashed" in low
        or "instrumentation_result: shortmsg=process crashed" in low
        or "instrumentation aborted due to process crash" in low
    )


def _execution_classification(output: str, returncode: int) -> tuple[dict[str, int], list[str], str, str]:
    """Classify AndroidJUnitRunner output without turning setup crashes into test failures.

    A process crash can be emitted with ``INSTRUMENTATION_STATUS_CODE: -2`` even
    when no test body actually executed.  That is infrastructure/setup failure,
    not a failed test.  Preserve zero executed/failed tests while still failing
    the instrumentation action through ``returncode``/``failure_kind``.
    """
    if _instrumentation_process_crash(output):
        counts = _parse_counts(output)
        counts.update({"executed": 0, "passed": 0, "failed": 0, "skipped": 0})
        return counts, [], "setup-crash", "Instrumentation process crashed before test execution completed"
    counts = _parse_counts(output)
    failed_ids = _parse_failed_ids(output)
    if int(returncode or 0) != 0 and counts["failed"] == 0:
        return counts, failed_ids, "runner-error", "Instrumentation runner exited unsuccessfully"
    if counts["failed"] > 0:
        return counts, failed_ids, "test-failure", ""
    return counts, failed_ids, "", ""


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
        gradle_receipts: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.device_ops = device_ops
        self.env = dict(os.environ if env is None else env)
        self.config = config
        self.gradle_receipts = tuple(dict(item) for item in gradle_receipts if isinstance(item, Mapping))

    @classmethod
    def from_config(
        cls, config: ProjectConfig, *, env: Mapping[str, str] | None = None,
        gradle_receipts: Sequence[Mapping[str, Any]] = (),
    ) -> "AndroidInstrumentationOrchestrator":
        return cls(AndroidDeviceOperations.from_config(config, env=env), env=env, config=config, gradle_receipts=gradle_receipts)

    def _build_with_devtool_builder(self, request: InstrumentationRequest) -> None:
        """Build app/test APKs through the canonical staged Android builder."""
        from ..android_integration import android_builder_arguments_for_tasks, plan_android_command
        from .signing import signing_environment_for_tasks

        variant_title = _variant_title(request.variant)
        config = self.config
        if config is not None:
            from .variants import resolve_android_variant
            profile = resolve_android_variant(config, request.variant)
            variant_title = profile.title
            module_prefix = f":{profile.module}:"
            tasks = [f"{module_prefix}assemble{variant_title}", f"{module_prefix}assemble{variant_title}AndroidTest"]
        else:
            tasks = [f"assemble{variant_title}", f"assemble{variant_title}AndroidTest"]
        if config is None:
            android: dict[str, Any] = {"variant": request.variant, "aapt2": request.aapt2, "aapt2_provider": request.aapt2_provider}
            gradle_options: dict[str, Any] = {"args": list(request.gradle_args)}
            configured_gradle = str(request.gradle or "").strip()
            if configured_gradle and configured_gradle not in {"./gradlew", "gradlew"}:
                gradle_options["command"] = configured_gradle
            config = ProjectConfig(
                repo_root=request.project, project_root=str(request.project), kind="android", runner="android",
                variant=request.variant, runner_options={"android": android, "gradle": gradle_options},
            )
        plan = plan_android_command(config, "build")
        argv = [*plan.argv_prefix, *android_builder_arguments_for_tasks(config, tasks, scan_artifacts=False)]
        child_env = signing_environment_for_tasks(config, tasks, env=self.env)
        build = subprocess.run(argv, cwd=str(plan.project_root), text=True, capture_output=True, check=False, env=child_env)
        if build.returncode:
            detail = (build.stderr or build.stdout).strip()
            raise InstrumentationError(detail or f"Devtool Android instrumentation build failed with {build.returncode}")

    def _skip_or_fail(self, request: InstrumentationRequest, reason: str, started: float, *, intent_id: str = "") -> InstrumentationResult:
        policy = normalize_instrumentation_policy(request.policy)
        if policy == "required":
            return InstrumentationResult(
                status="failed", reason=reason, failure_kind="device-readiness", intent_id=intent_id,
                failed=1, total=0, returncode=1, duration_ms=int((time.monotonic() - started) * 1000),
                device_policy=request.device_policy,
            )
        return InstrumentationResult(
            status="skipped", reason=reason, intent_id=intent_id, skipped=1, total=1, returncode=0,
            duration_ms=int((time.monotonic() - started) * 1000), device_policy=request.device_policy,
        )

    def _canonical_authority(self, request: InstrumentationRequest):
        """Resolve one Android environment/transport/DeviceLease for this attempt.

        The canonical production path has a ProjectConfig. Config-less injected
        orchestrators remain a compatibility seam for unit tests/third-party use.
        """
        if self.config is None:
            if normalize_instrumentation_policy(request.policy) == "required" and request.wait_timeout_seconds > 0:
                device = self.device_ops.wait_for_device(serial=request.serial, policy=request.device_policy, timeout=request.wait_timeout_seconds)
            else:
                device = self.device_ops.select_device(serial=request.serial, policy=request.device_policy)
            return device, None, None, None

        from .environment import EnvironmentView, resolve_android_environment
        from .execution_snapshot import (
            acquire_device_lease, device_requirements_from_config, operations_for_device_lease, resolve_android_transport,
        )
        view = EnvironmentView.from_config(self.config, env=self.env)
        _req, _qualification, environment = resolve_android_environment(
            self.config, action="instrumentation", env=view.values, device_required=True, fail_closed=True,
        )
        if environment is None:
            raise InstrumentationError("Canonical Android instrumentation environment did not resolve")
        requirements = device_requirements_from_config(
            self.config,
            overrides={
                "device_policy": request.device_policy or "exactly-one",
                "serial": request.serial,
            },
        )
        transport = resolve_android_transport(
            self.config, environment, action="instrumentation", env_view=view, device_required=True,
        )
        if transport is None:
            raise InstrumentationError("Android instrumentation requires a device transport")
        lease = acquire_device_lease(
            transport, requirements, env=view.values,
            attempt_id=str(self.env.get("DEVTOOL_RUN_ID") or ""),
        )
        self.device_ops = operations_for_device_lease(transport, lease, env=view.values)
        return self.device_ops.select_device(serial=lease.serial, policy="serial"), environment, transport, lease

    def _artifact_pair(self, request: InstrumentationRequest, app_apk: Path, test_apk: Path, environment):
        if self.config is None or environment is None:
            return None
        from .execution_snapshot import build_artifact_pair
        from .variants import resolve_android_variant_lane
        lane = resolve_android_variant_lane(self.config, "instrumentation")
        module_prefix = f":{lane.module}:"
        return build_artifact_pair(
            repo_root=self.config.repo_root,
            module=lane.module,
            variant_identity=lane.identity,
            environment=environment,
            app={"path": str(app_apk), "type": "apk", "producer_task": f"{module_prefix}assemble{lane.title}"},
            test={"path": str(test_apk), "type": "apk", "producer_task": f"{module_prefix}assemble{lane.title}AndroidTest"},
            gradle_receipt=(self.gradle_receipts[-1] if self.gradle_receipts else None),
            source_identity="",
            config_identity=lane.identity,
        )

    @staticmethod
    def _command_for_intent(intent: InstrumentationIntent, runner: str) -> list[str]:
        args = ["am", "instrument", "-w", "-r"]
        selected_classes = list(intent.classes)
        for method in intent.methods:
            if "#" in method:
                selected_classes.append(method)
            elif "." in method:
                class_name, method_name = method.rsplit(".", 1)
                selected_classes.append(f"{class_name}#{method_name}")
            else:
                selected_classes.append(method)
        if selected_classes:
            args.extend(["-e", "class", ",".join(selected_classes)])
        if intent.annotations:
            args.extend(["-e", "annotation", ",".join(intent.annotations)])
        if intent.not_annotations:
            args.extend(["-e", "notAnnotation", ",".join(intent.not_annotations)])
        if intent.size:
            args.extend(["-e", "size", intent.size])
        for key, value in intent.runner_args:
            args.extend(["-e", key, value])
        args.append(runner)
        return args

    def _cleanup_authority(self, request: InstrumentationRequest, report_path: Path, *, plan_id: str) -> dict[str, Any]:
        """Refresh LIFE ownership/protection roots immediately before cleanup."""
        try:
            from ..cleanup_lifecycle import GeneratedOutputDescriptor, build_cleanup_ownership_view
            descriptors = [
                GeneratedOutputDescriptor.create(
                    report_path, owner="android-instrumentation", provider="android", category="instrumentation-report",
                    producer_id=plan_id or "instrumentation", lifecycle="published", rebuild_cost="high", published=True,
                )
            ]
            evidence_dir = report_path.parent / "android-instrumentation-evidence"
            if evidence_dir.exists():
                descriptors.append(GeneratedOutputDescriptor.create(
                    evidence_dir, owner="android-instrumentation", provider="android", category="instrumentation-failure-evidence",
                    producer_id=plan_id or "instrumentation", lifecycle="published", rebuild_cost="high", published=True,
                ))
            view = build_cleanup_ownership_view(
                request.project, descriptors, policy_generation="android-x03-instrumentation-cleanup",
            )
            return {
                "repository_id": view.repository_id,
                "policy_generation": view.policy_generation,
                "authority_sources": list(view.authority_sources),
                "reference_ids": [ref.reference_id for ref in view.references],
                "refreshed_before_mutation": True,
            }
        except Exception as exc:
            return {"refreshed_before_mutation": False, "detail": str(exc)}

    def _cleanup_packages(
        self, request: InstrumentationRequest, *, device_serial: str, installed_test_package: str,
        target_package: str, cleanup_authority: Mapping[str, Any], primary_failed: bool,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        if request.keep_installed:
            return results, errors
        packages: list[tuple[str, str]] = []
        if installed_test_package:
            packages.append(("test-package", installed_test_package))
        if request.cleanup_app and target_package:
            packages.append(("target-package", target_package))
        for role, package in packages:
            try:
                # Destination-side revalidation on the exact DeviceLease-bound
                # serial occurs immediately before destructive uninstall.
                probe = self.device_ops.shell(["pm", "path", package], serial=device_serial, policy="serial", check=False)
                cleanup = self.device_ops.uninstall(package, serial=device_serial, policy="serial", check=False)
                row = {
                    "role": role, "package": package, "destination": f"android-device:{device_serial}",
                    "revalidation": probe.as_dict(), "result": cleanup.as_dict(),
                    "protection_refresh": dict(cleanup_authority),
                }
                results.append(row)
                if not cleanup.ok:
                    errors.append(cleanup.detail or f"{package} cleanup exited {cleanup.returncode}")
            except Exception as exc:
                results.append({
                    "role": role, "package": package, "destination": f"android-device:{device_serial}",
                    "status": "failed", "detail": str(exc), "protection_refresh": dict(cleanup_authority),
                })
                errors.append(str(exc))
        return results, errors

    def _persist_receipt(self, request: InstrumentationRequest, receipt: InstrumentationExecutionReceipt) -> tuple[Path, str]:
        directory = request.project / ".devtool" / "private" / "android" / "instrumentation"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{receipt.receipt_id}.json"
        encoded = json.dumps(receipt.as_dict(), indent=2, sort_keys=True) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != encoded:
            raise InstrumentationError(f"Instrumentation receipt collision: {path}")
        path.write_text(encoded, encoding="utf-8")
        attachment_id = ""
        run_id = str(self.env.get("DEVTOOL_RUN_ID") or "").strip()
        if run_id:
            try:
                from ..core_runtime import private_run_root
                from ..run_store import AttachmentDescriptor, RunCatalog
                catalog = RunCatalog.open(private_run_root(request.project))
                if run_id not in catalog.runs:
                    catalog.add_run({"run_id": run_id, "command_id": "android.instrumentation", "target": getattr(self.config, "selected_target", ""), "status": "running"})
                attachment = AttachmentDescriptor.from_file(
                    run_id=run_id, path=path, kind="android-instrumentation-receipt", privacy="private", media_type="application/json",
                )
                catalog.add_attachment(attachment)
                catalog.save()
                attachment_id = attachment.attachment_id
            except Exception:
                attachment_id = ""
        return path, attachment_id

    def run(self, request: InstrumentationRequest) -> InstrumentationResult:
        started = time.monotonic()
        policy = normalize_instrumentation_policy(request.policy)
        report_path = request.report or request.project / ".devtool" / "reports" / "android-instrumentation.json"
        intent = instrumentation_intent(request)
        if policy == "disabled":
            result = InstrumentationResult(
                status="skipped", reason="instrumentation policy is disabled", intent_id=intent.intent_id,
                skipped=1, total=1, duration_ms=0,
            )
            _write_report(result, report_path)
            return result

        try:
            device, environment, transport, lease = self._canonical_authority(request)
            destination_id = str(getattr(transport, "destination_id", "local") or "local")
        except Exception as exc:
            result = self._skip_or_fail(request, str(exc), started, intent_id=intent.intent_id)
            _write_report(result, report_path)
            return result

        installed_test_package = ""
        target_package = request.target_package
        if request.runner and "/" in request.runner:
            installed_test_package = request.runner.split("/", 1)[0].strip()
        artifact_pair = None
        plan = None
        install_results: list[dict[str, Any]] = []
        execution_result: dict[str, Any] = {}
        evidence: dict[str, Any] = {}
        result: InstrumentationResult | None = None
        try:
            if not request.skip_build:
                self._build_with_devtool_builder(request)

            app_apk, test_apk = _find_apks(request.project, request.variant)
            artifact_pair = self._artifact_pair(request, app_apk, test_apk, environment)
            app_install = self.device_ops.install(app_apk, serial=device.serial, policy="serial", replace=True, test_only=True)
            install_results.append(app_install.as_dict())
            test_install = self.device_ops.install(test_apk, serial=device.serial, policy="serial", replace=True, test_only=True)
            install_results.append(test_install.as_dict())

            listed = self.device_ops.shell(["pm", "list", "instrumentation"], serial=device.serial, policy="serial")
            runner, target_package = _choose_runner(
                listed.stdout + listed.stderr, configured=intent.requested_runner, target_package=target_package,
            )
            installed_test_package = runner.split("/", 1)[0]
            command = self._command_for_intent(intent, runner)
            if environment is not None and artifact_pair is not None and lease is not None:
                plan = resolve_instrumentation_plan(
                    intent, environment_id=environment.environment_id, artifact_pair_id=artifact_pair.pair_id,
                    device_lease_id=lease.lease_id, destination_id=destination_id, runner=runner,
                    target_package=target_package, test_package=installed_test_package, command=command,
                )
                command = list(plan.command)
            execution = self.device_ops.shell(command, serial=device.serial, policy="serial", timeout=1800, check=False)
            output = execution.stdout + "\n" + execution.stderr
            counts, failed_ids, failure_kind, classification_reason = _execution_classification(output, execution.returncode)
            zero_required = policy == "required" and counts["total"] <= 0 and failure_kind != "setup-crash"
            failed = execution.returncode != 0 or counts["failed"] > 0 or zero_required or bool(failure_kind)
            if zero_required:
                counts["failed"] = max(1, counts["failed"])
                failure_kind = failure_kind or "zero-tests"
                classification_reason = "Required instrumentation executed zero tests"
            result = InstrumentationResult(
                status="failed" if failed else "passed",
                reason=classification_reason,
                failure_kind=failure_kind,
                intent_id=intent.intent_id,
                plan_id=plan.plan_id if plan else "",
                artifact_pair_id=artifact_pair.pair_id if artifact_pair else "",
                device_lease_id=lease.lease_id if lease else "",
                runner=runner, target_package=target_package, test_package=installed_test_package,
                device_serial=device.serial, device_policy=request.device_policy,
                app_apk=str(app_apk), test_apk=str(test_apk), **counts, failed_ids=failed_ids,
                returncode=execution.returncode or (1 if failed else 0),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            execution_result = execution.as_dict()
            if failed and (request.capture_logcat_on_failure or request.capture_screenshot_on_failure):
                evidence = capture_failure_evidence(
                    self.device_ops, report_dir=report_path.parent / "android-instrumentation-evidence",
                    package=target_package, serial=device.serial, policy="serial",
                    capture_logcat=request.capture_logcat_on_failure,
                    capture_screenshot=request.capture_screenshot_on_failure,
                )
                result.evidence = evidence
        except (InstrumentationError, AndroidDeviceOperationError, OSError, RuntimeError, ValueError) as exc:
            if request.capture_logcat_on_failure or request.capture_screenshot_on_failure:
                try:
                    evidence = capture_failure_evidence(
                        self.device_ops, report_dir=report_path.parent / "android-instrumentation-evidence",
                        package=target_package, serial=device.serial, policy="serial",
                        capture_logcat=request.capture_logcat_on_failure,
                        capture_screenshot=request.capture_screenshot_on_failure,
                    )
                except Exception:
                    evidence = {}
            result = InstrumentationResult(
                status="failed", reason=str(exc), failure_kind="setup-error", intent_id=intent.intent_id,
                plan_id=plan.plan_id if plan else "", artifact_pair_id=artifact_pair.pair_id if artifact_pair else "",
                device_lease_id=lease.lease_id if lease else "", target_package=target_package,
                test_package=installed_test_package, device_serial=device.serial, device_policy=request.device_policy,
                failed=1, returncode=1, duration_ms=int((time.monotonic() - started) * 1000), evidence=evidence,
            )

        assert result is not None
        cleanup_authority = self._cleanup_authority(request, report_path, plan_id=result.plan_id or intent.intent_id)
        cleanup_results, cleanup_errors = self._cleanup_packages(
            request, device_serial=device.serial, installed_test_package=installed_test_package,
            target_package=target_package, cleanup_authority=cleanup_authority, primary_failed=result.status == "failed",
        )
        if request.keep_installed or (not installed_test_package and not (request.cleanup_app and target_package)):
            result.cleanup_status = "skipped"
        elif cleanup_errors:
            result.cleanup_status = "failed"
            cleanup_detail = "cleanup failed: " + "; ".join(cleanup_errors)
            if result.status == "failed":
                # Cleanup is secondary evidence; never replace the authoritative
                # instrumentation/test/setup failure classification.
                result.reason = (result.reason + "; " if result.reason else "") + cleanup_detail
            else:
                result.status = "failed"
                result.reason = cleanup_detail
                result.failure_kind = "cleanup"
                result.returncode = 1
        else:
            result.cleanup_status = "passed"

        snapshot_id = ""
        snapshot_attachment_id = ""
        if environment is not None and artifact_pair is not None and lease is not None and self.config is not None:
            try:
                from .execution_snapshot import build_android_execution_snapshot, persist_android_execution_snapshot
                run_id = str(self.env.get("DEVTOOL_RUN_ID") or "").strip()
                snapshot = build_android_execution_snapshot(
                    target=self.config.selected_target, action="instrumentation", environment=environment,
                    gradle_receipts=self.gradle_receipts, artifact_pair=artifact_pair, device_lease=lease,
                    destination_id=destination_id,
                    config_identity=plan.plan_id if plan else intent.intent_id,
                )
                snapshot_id = snapshot.snapshot_id
                if run_id:
                    _snapshot_path, snapshot_attachment_id = persist_android_execution_snapshot(request.project, run_id, snapshot)
                result.execution_snapshot_id = snapshot_id
                result.execution_snapshot_attachment_id = snapshot_attachment_id
            except Exception as exc:
                if result.status == "passed":
                    result.status = "failed"
                    result.failure_kind = "snapshot"
                    result.reason = f"execution snapshot failed: {exc}"
                    result.returncode = 1
                else:
                    result.reason = (result.reason + "; " if result.reason else "") + f"execution snapshot failed: {exc}"

        receipt_payload = {
            "intent_id": intent.intent_id, "plan_id": result.plan_id, "status": result.status,
            "failure_kind": result.failure_kind, "device_lease_id": result.device_lease_id,
            "artifact_pair_id": result.artifact_pair_id, "destination_id": destination_id,
            "install_results": install_results, "execution_result": execution_result, "evidence": evidence,
            "cleanup_results": cleanup_results, "cleanup_authority": cleanup_authority,
            "snapshot_id": snapshot_id, "snapshot_attachment_id": snapshot_attachment_id,
        }
        receipt = InstrumentationExecutionReceipt(receipt_id=_stable(receipt_payload), **receipt_payload)
        try:
            self._persist_receipt(request, receipt)
            result.receipt_id = receipt.receipt_id
        except Exception as exc:
            if result.status == "passed":
                result.status = "failed"
                result.failure_kind = "receipt"
                result.reason = f"instrumentation receipt failed: {exc}"
                result.returncode = 1
            else:
                result.reason = (result.reason + "; " if result.reason else "") + f"instrumentation receipt failed: {exc}"
        result.duration_ms = int((time.monotonic() - started) * 1000)
        _write_report(result, report_path)
        return result

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
    raw_runner_args = runner_args if runner_args is not None else ins.get("runner_args", {})
    if raw_runner_args is None:
        normalized_runner_args: dict[str, str] = {}
    elif isinstance(raw_runner_args, Mapping):
        normalized_runner_args = {str(k): str(v) for k, v in dict(raw_runner_args).items()}
    else:
        raise InstrumentationError("android.instrumentation.runner_args must be a table/object of key/value pairs")
    timeout_default = 30 if resolve_instrumentation_policy(config) == "required" else 0
    timeout = _coerce_float(ins.get("wait_timeout_seconds", timeout_default), timeout_default, field="android.instrumentation.wait_timeout_seconds")
    return InstrumentationRequest(
        project=project,
        variant=__import__("devtool.android.variants", fromlist=["resolve_android_variant_lane"]).resolve_android_variant_lane(config, "instrumentation").name,
        policy=resolve_instrumentation_policy(config),
        serial=str(config.device_serial or adb.get("serial") or android.get("device_serial") or ""),
        device_policy=str(adb.get("device_policy") or "exactly-one"),
        runner=str(ins.get("test_runner") or ""),
        target_package=str(ins.get("target_package") or ""),
        classes=list(classes), methods=list(methods), annotations=list(annotations), not_annotations=list(not_annotations), size=str(size or ins.get("size") or ""),
        runner_args=normalized_runner_args,
        gradle=str(android.get("gradle") if isinstance(android.get("gradle"), str) else "./gradlew"),
        gradle_args=[str(item) for item in gradle_args if str(item).strip()],
        aapt2=str(android.get("aapt2") or ""),
        aapt2_provider=str(android.get("aapt2_provider") or "auto"),
        skip_build=skip_build,
        wait_timeout_seconds=timeout,
        cleanup_app=_coerce_bool(ins.get("cleanup_app", False), False),
        keep_installed=_coerce_bool(ins.get("keep_installed", False), False),
        capture_logcat_on_failure=_coerce_bool(ins.get("capture_logcat_on_failure", True), True),
        capture_screenshot_on_failure=_coerce_bool(ins.get("capture_screenshot_on_failure", True), True),
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
    "InstrumentationIntent", "ResolvedInstrumentationPlan", "InstrumentationExecutionReceipt", "gradle_receipts_from_current_run",
    "InstrumentationUnavailable", "INSTRUMENTATION_POLICIES", "build_parser", "instrumentation_config", "main",
    "instrumentation_intent", "resolve_instrumentation_plan", "normalize_instrumentation_policy",
    "request_from_config", "resolve_instrumentation_policy", "_parse_counts", "_parse_failed_ids", "_execution_classification",
]

if __name__ == "__main__":
    raise SystemExit(main())
