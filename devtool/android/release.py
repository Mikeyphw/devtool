"""Canonical Android release orchestration (AOPS04)."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import os
import re
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from ..models import CommandSpec, OperationResult, ProjectConfig
from .capabilities import AndroidExecutionContext, AndroidToolResolution, resolve_android_execution_context, resolved_android_tool
from .device_ops import AndroidDeviceOperationError, AndroidDeviceOperations, SmokeGateResult, capture_failure_evidence
from .evidence import AndroidReleaseEvidenceSession
from .environment import EnvironmentView, resolve_android_environment
from .execution_snapshot import (
    acquire_device_lease,
    build_android_execution_snapshot,
    build_artifact_pair,
    device_requirements_from_config,
    operations_for_device_lease,
    persist_android_execution_snapshot,
    resolve_android_transport,
)
from .signing import AndroidSigningManager, signing_profile, validate_signing_profile_for_tasks
from .variants import resolve_android_variant, resolve_android_variant_lane, validate_variant_bound_tasks

DEVICE_TEST_POLICIES = frozenset({"disabled", "when-device-present", "required"})


@dataclass(frozen=True, slots=True)
class AndroidReleaseOptions:
    variant: str = "release"
    module: str = ""
    unit_tests: bool = True
    lint: bool = True
    device_tests: str = "required"
    install_smoke: bool = True
    upgrade_smoke: bool = True
    cold_start_smoke: bool = False
    restart_smoke: bool = False
    restore_restart_smoke: bool = False
    process_death_smoke: bool = False
    build_artifacts: bool = True
    verify_signature: bool = True
    verify_certificate_identity: bool = True
    previous_apk: str = ""
    package: str = ""
    activity: str = ""
    keep_installed: bool = True


def _bool(options: Mapping[str, Any], name: str, default: bool) -> bool:
    value = options.get(name, default)
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "0", "false", "no", "off", "disabled", "none"}:
            return False
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        return default
    return bool(value)


def _has_error_issue(result: OperationResult) -> bool:
    return any(str(issue.severity).strip().lower() in {"error", "fatal"} for issue in result.issues)


def _result_failed(result: OperationResult) -> bool:
    if result.returncode != 0:
        return True
    if _has_error_issue(result):
        return True
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    return str(metadata.get("status") or "").strip().lower() in {"failed", "error", "fatal"}


def _strict_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def release_options(config: ProjectConfig, overrides: Mapping[str, Any] | None = None) -> AndroidReleaseOptions:
    android = config.runner_options.get("android", {})
    android = dict(android) if isinstance(android, dict) else {}
    raw = android.get("release", {})
    values = dict(raw) if isinstance(raw, dict) else {}
    explicit_overrides = dict(overrides or {})
    values.update(explicit_overrides)
    profiles = android.get("variants", android.get("variant_profiles", {}))
    configured_variant = bool(
        config.variant
        and isinstance(profiles, dict)
        and config.variant in profiles
    )
    if not configured_variant and config.variant and config.selected_target in config.targets:
        configured_variant = config.variant in config.targets[config.selected_target].variants
    if "variant" not in values and configured_variant:
        values["variant"] = config.variant
    device_tests = str(values.get("device_tests") or "required").strip().lower().replace("_", "-")
    if device_tests not in DEVICE_TEST_POLICIES:
        raise ValueError(f"Unsupported Android release device_tests policy: {device_tests}")
    return AndroidReleaseOptions(
        # Release qualification is intentionally independent from the variant
        # used by ordinary build/test commands. A project commonly defaults
        # to ``debug``; inheriting that value silently downgrades the release
        # pipeline and makes the release-gate policy reject legacy configs.
        # Custom release variants remain available through
        # android.release.variant or an explicit command override.
        variant=str(values.get("variant") or "release").strip() or "release",
        module=str(values.get("module") or "").strip().strip(":"),
        unit_tests=_bool(values, "unit_tests", True),
        lint=_bool(values, "lint", True),
        device_tests=device_tests,
        install_smoke=_bool(values, "install_smoke", True),
        upgrade_smoke=_bool(values, "upgrade_smoke", True),
        cold_start_smoke=_bool(values, "cold_start_smoke", False),
        restart_smoke=_bool(values, "restart_smoke", False),
        restore_restart_smoke=_bool(values, "restore_restart_smoke", False),
        process_death_smoke=_bool(values, "process_death_smoke", False),
        build_artifacts=_bool(values, "build_artifacts", True),
        verify_signature=_bool(values, "verify_signature", True),
        verify_certificate_identity=_bool(values, "verify_certificate_identity", True),
        previous_apk=str(values.get("previous_apk") or "").strip(),
        package=str(values.get("package") or "").strip(),
        activity=str(values.get("activity") or "").strip(),
        keep_installed=_bool(values, "keep_installed", True),
    )


@dataclass(slots=True)
class AndroidReleaseResult:
    session_id: str
    results: list[OperationResult]
    evidence_path: str
    evidence: dict[str, Any]

    @property
    def passed(self) -> bool:
        return all(item.returncode == 0 for item in self.results) and self.evidence.get("verdict") == "passed"


class AndroidReleaseOrchestrator:
    """Run Android release gates through existing Devtool services only."""

    def __init__(
        self,
        config: ProjectConfig,
        *,
        env: Mapping[str, str] | None = None,
        executor: Callable[[ProjectConfig, list[CommandSpec]], tuple[list[OperationResult], str]] | None = None,
    ) -> None:
        if config.kind != "android" and config.runner != "android":
            raise ValueError("Android release requires an Android target/runner")
        self.base_config = config
        self.env = dict(os.environ if env is None else env)
        self.executor = executor

    def _config(self, options: AndroidReleaseOptions) -> ProjectConfig:
        from .variants import activate_android_variant
        config = activate_android_variant(self.base_config, options.variant)
        android = config.runner_options.get("android", {})
        android = dict(android) if isinstance(android, dict) else {}
        instrumentation = android.get("instrumentation", {})
        instrumentation = dict(instrumentation) if isinstance(instrumentation, dict) else {}
        instrumentation["policy"] = options.device_tests
        android["instrumentation"] = instrumentation
        release_raw = android.get("release", {})
        release = dict(release_raw) if isinstance(release_raw, dict) else {}
        release["variant"] = options.variant
        if options.module:
            release["module"] = options.module
        android["release"] = release
        config.runner_options["android"] = android

        # Canonical release qualification must not accidentally reuse a target's
        # default/debug task bindings. Repositories may provide explicit
        # release_* task overrides; project runners consume the ordinary task
        # keys, so project those overrides onto the activated release config.
        tasks_raw = config.runner_options.get("tasks", {})
        tasks = dict(tasks_raw) if isinstance(tasks_raw, dict) else {}
        release_task_overrides = {
            "test": tasks.get("release_test"),
            "lint": tasks.get("release_lint"),
            "build": tasks.get("release_build"),
            "package": tasks.get("release_build"),
            "instrumentation": tasks.get("release_instrumentation"),
        }
        for key, value in release_task_overrides.items():
            if value:
                tasks[key] = value
        if tasks:
            config.runner_options["tasks"] = tasks
        return config

    def plan(self, *, overrides: Mapping[str, Any] | None = None) -> tuple[ProjectConfig, AndroidReleaseOptions, list[tuple[str, list[CommandSpec]]]]:
        from ..project_runners import AndroidGradleRunner
        options = release_options(self.base_config, overrides)
        config = self._config(options)
        release_lane = resolve_android_variant_lane(config, "release")
        profile = resolve_android_variant(config, release_lane.name)
        if not profile.release_gates:
            raise ValueError(
                f"Android variant {profile.name!r} is not enabled for release qualification; "
                "set android.variants.<name>.release_gates = true"
            )
        instrumentation_lane = resolve_android_variant_lane(config, "instrumentation")
        runner = AndroidGradleRunner()
        phases: list[tuple[str, list[CommandSpec]]] = []
        if options.unit_tests:
            phases.append(("unit-tests", runner.build_specs(config, "test")))
        if options.lint:
            phases.append(("lint", runner.build_specs(config, "lint")))
        if options.build_artifacts:
            phases.append(("package", runner.build_specs(config, "package")))
        if options.device_tests != "disabled":
            phases.append(("instrumentation", runner.build_specs(config, "instrumentation")))
        for phase_name, specs in phases:
            phase_lane = instrumentation_lane if phase_name == "instrumentation" else release_lane
            for spec in specs:
                metadata = spec.metadata if isinstance(spec.metadata, dict) else {}
                gradle_tasks = tuple(str(item) for item in metadata.get("gradle_tasks", []) or [])
                if gradle_tasks:
                    validate_variant_bound_tasks(gradle_tasks, phase_lane)
                spec.metadata.update({
                    "android_release_pipeline": True,
                    "android_release_phase": phase_name,
                    "release_variant": options.variant,
                    "android_variant_lane": phase_lane.as_dict(),
                    "android_release_lane": release_lane.as_dict(),
                })
        return config, options, phases

    def _run_specs(self, config: ProjectConfig, specs: list[CommandSpec], *, verbose: bool, dry_run: bool, continue_on_error: bool, log_path: Path | str | None) -> tuple[list[OperationResult], str]:
        if self.executor is not None:
            return self.executor(config, specs)
        from ..runners import run_specs
        return run_specs(config, specs, verbose=verbose, dry_run=dry_run, continue_on_error=continue_on_error, log_path=log_path)

    @staticmethod
    def _artifact_payload(results: Sequence[OperationResult]) -> dict[str, Any]:
        for result in reversed(results):
            payload = result.metadata.get("artifacts") if isinstance(result.metadata, dict) else None
            if isinstance(payload, dict):
                return payload
        return {}

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _release_artifacts(cls, config: ProjectConfig, artifact_payload: Mapping[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
        candidates: list[tuple[Path, dict[str, Any]]] = []
        for raw in artifact_payload.get("items", []) or []:
            if not isinstance(raw, dict) or not _strict_bool(raw.get("valid", False), False):
                continue
            item = dict(raw)
            kind = str(item.get("type") or "").lower()
            value = str(item.get("path") or "").strip()
            if not value or kind not in {"apk", "aab", "jar"}:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = config.repo_root / path
            path = path.resolve(strict=False)
            # Release qualification owns materialization truth, not producer claims.
            if not path.is_file():
                continue
            actual_sha = cls._sha256(path)
            # A verifier must never manufacture missing producer evidence by
            # hashing an unbound filesystem candidate and promoting it to a
            # claimed build artifact. Distinct producer/current fields also
            # cannot contradict each other without failing release selection.
            declared = [str(item[key]).strip().lower() for key in
                        ("sha256", "current_sha256", "producer_sha256")
                        if key in item and item[key] is not None]
            if (not declared or any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in declared)
                or len(set(declared)) != 1 or declared[0] != actual_sha):
                continue
            item["sha256"] = actual_sha
            item["current_sha256"] = actual_sha
            item["materialized"] = True
            item["verified_path"] = str(path)
            candidates.append((path, item))
        # The orchestrator activates the selected release variant on config;
        # use that execution variant here so custom release lanes do not fall
        # back to the conventional literal ``release`` name.
        variant = str(config.variant or resolve_android_variant_lane(config, "release").name).strip().lower()
        selected = []
        for entry in candidates:
            declared_variant = str(entry[1].get("variant") or "").strip().lower()
            if declared_variant:
                if declared_variant == variant:
                    selected.append(entry)
                continue
            if variant in entry[0].name.lower():
                selected.append(entry)
        return selected

    @classmethod
    def _release_apk(cls, config: ProjectConfig, artifact_payload: Mapping[str, Any]) -> Path | None:
        candidates = [path for path, item in cls._release_artifacts(config, artifact_payload) if str(item.get("type") or "").lower() == "apk"]
        return candidates[0] if candidates else None

    def _signing_context(self, config: ProjectConfig, resolved_environment: object | None) -> AndroidExecutionContext:
        """Build a narrow signing context without probing device transport."""
        from ..termux_toolchains import detect_runtime_identity
        runtime = detect_runtime_identity(self.env)
        tools: dict[str, AndroidToolResolution] = {}
        java = resolved_android_tool("java", config, env=self.env)
        apksigner = resolved_android_tool("apksigner", config, env=self.env)
        tools["java"] = AndroidToolResolution("java", "path" if java else "none", bool(java), path=str(java or ""), compatible=bool(java))
        tools["apksigner"] = AndroidToolResolution("apksigner", "resolved" if apksigner else "none", bool(apksigner), path=str(apksigner or ""), compatible=bool(apksigner), rejection_reason="" if apksigner else "apksigner is unavailable")
        tools["adb"] = AndroidToolResolution("adb", "not-requested", False, compatible=True, rejection_reason="device transport not requested for signing")
        sdk_root = str(getattr(resolved_environment, "sdk_root", "") or "")
        sdk_source = str(getattr(resolved_environment, "sdk_source", "") or "")
        lane = resolve_android_variant_lane(config, "release")
        return AndroidExecutionContext(runtime, str(config.project_root or config.repo_root), config.selected_target, lane.name, sdk_root, sdk_source, tools, "none")

    def run(
        self,
        *,
        overrides: Mapping[str, Any] | None = None,
        verbose: bool = False,
        dry_run: bool = False,
        continue_on_error: bool = False,
        log_path: Path | str | None = None,
        output: Path | None = None,
    ) -> AndroidReleaseResult:
        config, options, phases = self.plan(overrides=overrides)
        session = AndroidReleaseEvidenceSession(config.repo_root, config.selected_target, options.variant)
        first_metadata = next((spec.metadata for _phase, specs in phases for spec in specs if isinstance(spec.metadata, dict)), {})
        resolved_environment = None
        environment_view = EnvironmentView.from_config(config, env=self.env)
        gradle_receipts: list[dict[str, Any]] = []
        package_gradle_receipt: dict[str, Any] = {}
        artifact_pair = None
        device_lease = None
        if self.executor is not None:
            # Injected executors are an explicit external execution/test seam.
            # Preserve the legacy compatibility context there without claiming
            # destination Android environment qualification. Production release
            # execution below is canonical and fail-closed.
            compatibility_context = resolve_android_execution_context(config, env=self.env)
            session.execution = {
                "authority": "external-executor",
                "compatibility_context": compatibility_context.as_dict(),
                "android_environment": None,
            }
        else:
            request, qualification, resolved_environment = resolve_android_environment(
                config, action="release", env=self.env, gradle_metadata=first_metadata,
                device_required=False, fail_closed=True,
            )
            session.execution = {
                "authority": "android-environment-v1",
                "android_environment_request": request.as_dict(),
                "android_environment_qualification": qualification.as_dict(),
                "android_environment": resolved_environment.as_dict() if resolved_environment is not None else None,
            }
        try:
            from .variants import resolve_semantic_tasks
            signing_tasks = resolve_semantic_tasks(config, ["assemble"])
            profile = validate_signing_profile_for_tasks(config, signing_tasks, env=self.env) if (options.verify_signature or options.verify_certificate_identity) else signing_profile(config)
            session.signing = {
                "mode": profile.mode,
                "policy": profile.policy,
                "keystore": str(profile.keystore or ""),
                "alias": profile.alias,
                "expected_cert_sha256": profile.expected_cert_sha256,
            }
        except Exception as exc:
            session.signing = {"error": str(exc)}
            if options.verify_signature or options.verify_certificate_identity:
                session.add_gate("signing-config", "failed", required=True, detail=str(exc))

        if not phases:
            session.add_gate("release-plan", "failed", required=True, detail="Android release produced zero configured gates")

        for phase_name, specs in phases:
            if not specs:
                session.add_gate(phase_name, "failed", required=True, detail=f"Android release phase {phase_name} produced zero command specs")

        all_results: list[OperationResult] = []
        raw_chunks: list[str] = []
        package_artifacts: dict[str, Any] = {}
        for phase_name, specs in phases:
            if not specs:
                if not continue_on_error:
                    break
                continue
            started = time.monotonic()
            phase_results, raw = self._run_specs(config, specs, verbose=verbose, dry_run=dry_run, continue_on_error=continue_on_error, log_path=log_path)
            all_results.extend(phase_results)
            phase_receipts = [
                dict(item.metadata.get("gradle_execution_receipt") or {})
                for item in phase_results
                if isinstance(item.metadata, dict) and isinstance(item.metadata.get("gradle_execution_receipt"), Mapping)
            ]
            gradle_receipts.extend(item for item in phase_receipts if item)
            if phase_name == "package" and phase_receipts:
                package_gradle_receipt = phase_receipts[-1]
            if raw:
                raw_chunks.append(raw)
            failed = [item for item in phase_results if _result_failed(item)]
            # A phase is not successful merely because its executor returned no
            # explicit failure. Every planned command must yield one operation
            # result; incomplete/duplicate result streams fail closed before
            # release evidence or qualification consumers see a passed gate.
            incomplete_results = len(phase_results) != len(specs)
            status = "failed" if failed or incomplete_results else "passed"
            if incomplete_results:
                detail = (
                    f"Android release phase {phase_name} returned {len(phase_results)} "
                    f"operation results for {len(specs)} planned commands"
                )
            elif failed and failed[0].output.strip():
                detail = failed[0].output.strip().splitlines()[-1]
            elif failed and _has_error_issue(failed[0]):
                detail = next((issue.message for issue in failed[0].issues if str(issue.severity).lower() in {"error", "fatal"}), "error diagnostic reported")
            else:
                detail = status
            session.add_gate(phase_name, status, required=True, duration_seconds=round(time.monotonic() - started, 3), detail=detail,
                             metadata={"results": [item.name for item in phase_results]})
            if phase_name == "package":
                package_artifacts = self._artifact_payload(phase_results)
                selected_release_artifacts = self._release_artifacts(config, package_artifacts)
                session.add_artifacts({"items": [item for _path, item in selected_release_artifacts]})
                if status == "passed" and not selected_release_artifacts:
                    session.add_gate(
                        "release-artifacts",
                        "failed",
                        required=True,
                        detail="Android package phase produced no valid artifact for the selected release variant",
                    )
            if (failed or incomplete_results) and not continue_on_error:
                break

        def required_failure_present() -> bool:
            return any(g.required and g.status == "failed" for g in session.gates)

        package_gate_ok = any(g.name == "package" and g.status == "passed" for g in session.gates)
        downstream_ok = package_gate_ok and not required_failure_present()
        release_artifacts = self._release_artifacts(config, package_artifacts) if downstream_ok else []
        apk = self._release_apk(config, package_artifacts) if downstream_ok else None

        # Signature/certificate verification is an artifact gate, not a device
        # gate. APK and AAB/JAR outputs are all supported and the actual signer
        # fingerprints are promoted into the release evidence. When an expected
        # fingerprint is configured it must match; otherwise identity verification
        # still proves that all produced release artifacts use one signer.
        if downstream_ok and options.verify_signature and not dry_run:
            started = time.monotonic()
            verifications: list[dict[str, Any]] = []
            identities: set[str] = set()
            failure = ""
            if not release_artifacts:
                failure = "Release pipeline produced no signed APK/AAB artifact"
            else:
                manager = AndroidSigningManager(self._signing_context(config, resolved_environment), env=self.env)
                expected = str(session.signing.get("expected_cert_sha256") or "") if options.verify_certificate_identity else ""
                for artifact, _item in release_artifacts:
                    try:
                        verification = manager.verify_artifact(artifact, expected_sha256=expected)
                        payload = verification.as_dict()
                        verifications.append(payload)
                        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
                        sha = str(identity.get("sha256") or "").lower()
                        if sha:
                            identities.add(sha)
                        if not verification.ok and not failure:
                            failure = verification.detail or f"Signature verification failed: {artifact.name}"
                    except Exception as exc:
                        verifications.append({"artifact": str(artifact), "status": "failed", "detail": str(exc)})
                        if not failure:
                            failure = str(exc)
            if options.verify_certificate_identity and not failure and len(identities) > 1:
                failure = "Release artifacts are signed by different certificate identities"
            session.signing["verified_artifacts"] = verifications
            if len(identities) == 1:
                session.signing["certificate_sha256"] = next(iter(identities))
            session.add_gate(
                "signature-verification", "failed" if failure else "passed", required=True,
                duration_seconds=round(time.monotonic() - started, 3),
                detail=failure or "Release artifact signatures verified",
                metadata={"artifacts": verifications, "certificate_sha256": session.signing.get("certificate_sha256", "")},
            )

        # Bind the exact materialized APK to canonical Gradle/RUN evidence.
        # This happens only after signature verification so signing identity can
        # be part of the lineage record. Non-APK release artifacts remain in the
        # release evidence but do not masquerade as an ArtifactPair.
        if downstream_ok and resolved_environment is not None and apk is not None and not dry_run:
            try:
                from ..core_runtime import private_run_root
                from ..run_store import RunCatalog
                catalog = RunCatalog.open(private_run_root(config.repo_root))
                if session.run_id not in catalog.runs:
                    catalog.add_run({
                        "run_id": session.run_id, "command_id": "android.release",
                        "target": config.selected_target, "variant": options.variant, "status": "running",
                    })
                release_lane = resolve_android_variant_lane(config, "release")
                app_item = next(
                    (item for path, item in release_artifacts if path == apk),
                    {},
                )
                artifact_pair = build_artifact_pair(
                    repo_root=config.repo_root,
                    module=release_lane.module,
                    variant_identity=release_lane.identity,
                    environment=resolved_environment,
                    app=app_item,
                    gradle_receipt=package_gradle_receipt,
                    source_identity=str(session.source.get("git_head") or ""),
                    config_identity=release_lane.identity,
                    signing_ref=str(session.signing.get("certificate_sha256") or session.signing.get("expected_cert_sha256") or ""),
                    run_catalog=catalog,
                    run_id=session.run_id,
                )
                catalog.save()
                session.execution["artifact_pair"] = artifact_pair.as_dict()
            except Exception as exc:
                session.add_gate("artifact-lineage", "failed", required=True, detail=str(exc))

        # Signature/certificate verification and exact artifact lineage are
        # required release gates. Re-evaluate before any operation capable of
        # touching or launching a device.
        downstream_ok = downstream_ok and not required_failure_present()
        upgrade_ready = options.upgrade_smoke and bool(options.previous_apk)
        if downstream_ok and options.upgrade_smoke and not options.previous_apk:
            session.add_gate("upgrade-smoke", "skipped", required=False, detail="No android.release.previous_apk was configured")
        device_needed = options.install_smoke or upgrade_ready or options.cold_start_smoke or options.restart_smoke or options.restore_restart_smoke or options.process_death_smoke
        operations: AndroidDeviceOperations | None = None
        if downstream_ok and device_needed and not dry_run:
            try:
                if resolved_environment is not None and self.executor is None:
                    requirements = device_requirements_from_config(config)
                    transport = resolve_android_transport(
                        config, resolved_environment, action="release-device", env_view=environment_view,
                        device_required=True,
                    )
                    if transport is None:
                        raise RuntimeError("Android release device work requires an Android transport")
                    device_lease = acquire_device_lease(
                        transport, requirements, env=environment_view.values, attempt_id=session.run_id,
                    )
                    operations = operations_for_device_lease(transport, device_lease, env=environment_view.values)
                    session.device = {
                        "authority": "android-device-lease-v1",
                        "requirements": requirements.as_dict(),
                        "transport": transport.as_dict(),
                        "lease": device_lease.as_dict(),
                    }
                else:
                    # Explicit external executor compatibility seam.
                    operations = AndroidDeviceOperations.from_config(config, env=self.env)
                    selected = operations.select_device(enrich=True)
                    session.device = selected.as_dict()
            except Exception as exc:
                required = options.device_tests == "required" or device_needed
                session.add_gate("device-readiness", "failed" if required else "skipped", required=required, detail=str(exc))

        # Device readiness is a prerequisite barrier. Recompute after selection
        # so a readiness failure can never leak into install/lifecycle side effects.
        downstream_ok = downstream_ok and not required_failure_present()

        def add_smoke(smoke: SmokeGateResult, *, required: bool = True) -> None:
            session.add_gate(smoke.gate, smoke.status, required=required, duration_seconds=smoke.duration_ms / 1000.0, detail=smoke.detail, metadata=smoke.evidence)
            all_results.append(OperationResult(smoke.gate, ["devtool", "android", "smoke", smoke.gate], 0 if smoke.ok else 1, smoke.duration_ms / 1000.0, smoke.detail, [], metadata=smoke.as_dict()))

        if downstream_ok and apk and not dry_run:
            if operations is not None:
                try:
                    if options.install_smoke:
                        add_smoke(operations.install_smoke(apk, package=options.package, activity=options.activity, keep_installed=options.keep_installed))
                    if upgrade_ready:
                        previous = Path(options.previous_apk).expanduser()
                        if not previous.is_absolute():
                            previous = config.repo_root / previous
                        add_smoke(operations.upgrade_smoke(previous, apk, package=options.package, activity=options.activity))
                    package = options.package
                    if not package and (options.cold_start_smoke or options.restart_smoke or options.restore_restart_smoke or options.process_death_smoke):
                        package = operations.apk_metadata(apk).get("package", "")
                    if options.cold_start_smoke:
                        add_smoke(operations.cold_start_smoke(package, activity=options.activity))
                    if options.restart_smoke:
                        add_smoke(operations.restart_smoke(package, activity=options.activity))
                    if options.restore_restart_smoke:
                        add_smoke(operations.restore_restart_smoke(package, activity=options.activity))
                    if options.process_death_smoke:
                        add_smoke(operations.process_death_smoke(package, activity=options.activity))
                except Exception as exc:
                    session.add_gate("device-smoke", "failed", detail=str(exc))
                    try:
                        evidence_dir = config.repo_root / ".devtool" / "reports" / "android-release" / session.session_id
                        session.device["failure_evidence"] = capture_failure_evidence(operations, report_dir=evidence_dir, package=options.package)
                    except Exception:
                        pass
        elif downstream_ok and device_needed and not apk and not dry_run:
            session.add_gate("device-smoke", "failed", detail="Configured device smoke gates require an installable APK; release produced only non-APK artifacts")

        # One immutable AndroidExecutionSnapshot composes the actual environment,
        # Gradle receipts, exact artifact lineage and stable DeviceLease used by
        # this release. It is private RUN evidence; the release report only
        # projects its identity/reference.
        if resolved_environment is not None and self.executor is None and not dry_run:
            try:
                signing_ref = str(session.signing.get("certificate_sha256") or session.signing.get("expected_cert_sha256") or "")
                release_lane = resolve_android_variant_lane(config, "release")
                snapshot = build_android_execution_snapshot(
                    target=config.selected_target, action="release", environment=resolved_environment,
                    gradle_receipts=gradle_receipts, artifact_pair=artifact_pair, device_lease=device_lease,
                    source_identity=str(session.source.get("git_head") or ""), config_identity=release_lane.identity,
                    signing_ref=signing_ref,
                )
                snapshot_path, attachment_id = persist_android_execution_snapshot(config.repo_root, session.run_id, snapshot)
                session.execution["android_execution_snapshot"] = {
                    "snapshot_id": snapshot.snapshot_id,
                    "run_attachment_id": attachment_id,
                    "path": str(snapshot_path),
                }
            except Exception as exc:
                session.add_gate("execution-snapshot", "failed", required=True, detail=str(exc))

        evidence_path = session.seal(output)
        payload = session.to_dict()
        evidence_result = OperationResult(
            "android-release-evidence", ["devtool", "release"], 0 if session.passed else 1, 0.0,
            f"Android release evidence: {evidence_path}\nVerdict: {session.verdict}", [],
            metadata={"release_session_id": session.session_id, "release_evidence": str(evidence_path), "release_verdict": session.verdict},
        )
        all_results.append(evidence_result)
        return AndroidReleaseResult(session.session_id, all_results, str(evidence_path), payload)


def audit_android_release_convergence(repo_root: Path) -> dict[str, Any]:
    """Audit high-level Android release code for bypasses of shared services."""
    root = Path(repo_root).resolve()
    checks: list[dict[str, Any]] = []
    rq = (root / "devtool" / "release_qualification.py").read_text(encoding="utf-8")
    checks.append({"id": "release-qualification.shared-orchestrator", "pass": "AndroidReleaseOrchestrator" in rq})
    checks.append({"id": "release-qualification.no-raw-gradlew", "pass": "connectedDebugAndroidTest" not in rq and '_run("android-release"' not in rq and '_run("android-instrumentation"' not in rq})
    checks.append({"id": "release-qualification.no-adb-discovery", "pass": "shutil.which(\"adb\")" not in rq and "adb devices" not in rq})
    release_src = (root / "devtool" / "android" / "release.py").read_text(encoding="utf-8")
    checks.append({"id": "release.uses-project-runner", "pass": "AndroidGradleRunner" in release_src and ".build_specs(" in release_src})
    checks.append({"id": "release.uses-device-ops", "pass": "AndroidDeviceOperations" in release_src})
    checks.append({"id": "release.uses-canonical-environment", "pass": "resolve_android_environment" in release_src})
    failed = [item["id"] for item in checks if not item["pass"]]
    return {"schema_version": 1, "status": "pass" if not failed else "fail", "checks": checks, "failed": failed}


__all__ = ["DEVICE_TEST_POLICIES", "AndroidReleaseOptions", "AndroidReleaseResult", "AndroidReleaseOrchestrator", "release_options", "audit_android_release_convergence"]
