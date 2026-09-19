"""Canonical Android release orchestration (AOPS04)."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from ..models import CommandSpec, OperationResult, ProjectConfig
from .capabilities import resolve_android_execution_context
from .device_ops import AndroidDeviceOperationError, AndroidDeviceOperations, SmokeGateResult, capture_failure_evidence
from .evidence import AndroidReleaseEvidenceSession
from .signing import AndroidSigningManager, signing_profile, validate_signing_profile_for_tasks

DEVICE_TEST_POLICIES = frozenset({"disabled", "when-device-present", "required"})


@dataclass(frozen=True, slots=True)
class AndroidReleaseOptions:
    variant: str = "release"
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
        config.runner_options["android"] = android
        return config

    def plan(self, *, overrides: Mapping[str, Any] | None = None) -> tuple[ProjectConfig, AndroidReleaseOptions, list[tuple[str, list[CommandSpec]]]]:
        from ..project_runners import AndroidGradleRunner
        options = release_options(self.base_config, overrides)
        config = self._config(options)
        from .variants import resolve_android_variant
        profile = resolve_android_variant(config)
        if not profile.release_gates:
            raise ValueError(
                f"Android variant {profile.name!r} is not enabled for release qualification; "
                "set android.variants.<name>.release_gates = true"
            )
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
            for spec in specs:
                spec.metadata.update({
                    "android_release_pipeline": True,
                    "android_release_phase": phase_name,
                    "release_variant": options.variant,
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
    def _release_artifacts(config: ProjectConfig, artifact_payload: Mapping[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
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
            candidates.append((path.resolve(strict=False), item))
        variant = str(config.variant or "").strip().lower()
        selected = [entry for entry in candidates if variant and (str(entry[1].get("variant") or "").strip().lower() == variant or variant in entry[0].name.lower())]
        if selected:
            return selected
        # Do not infer release identity from filenames. Artifact metadata is
        # authoritative when available; otherwise preserve all valid candidates
        # for the selected execution profile and let signature/type gates decide.
        return candidates

    @classmethod
    def _release_apk(cls, config: ProjectConfig, artifact_payload: Mapping[str, Any]) -> Path | None:
        candidates = [path for path, item in cls._release_artifacts(config, artifact_payload) if str(item.get("type") or "").lower() == "apk"]
        return candidates[0] if candidates else None

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
        context = resolve_android_execution_context(config, env=self.env)
        session = AndroidReleaseEvidenceSession(config.repo_root, config.selected_target, options.variant)
        session.execution = context.as_dict()
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
            if raw:
                raw_chunks.append(raw)
            failed = [item for item in phase_results if _result_failed(item)]
            status = "failed" if failed else "passed"
            if failed and failed[0].output.strip():
                detail = failed[0].output.strip().splitlines()[-1]
            elif failed and _has_error_issue(failed[0]):
                detail = next((issue.message for issue in failed[0].issues if str(issue.severity).lower() in {"error", "fatal"}), "error diagnostic reported")
            else:
                detail = status
            session.add_gate(phase_name, status, required=True, duration_seconds=round(time.monotonic() - started, 3), detail=detail,
                             metadata={"results": [item.name for item in phase_results]})
            if phase_name == "package":
                package_artifacts = self._artifact_payload(phase_results)
                session.add_artifacts(package_artifacts)
                if status == "passed" and not self._release_artifacts(config, package_artifacts):
                    session.add_gate("release-artifacts", "failed", required=True, detail="Android package phase produced no valid release APK/AAB/JAR artifacts")
            if failed and not continue_on_error:
                break

        package_gate_ok = any(g.name == "package" and g.status == "passed" for g in session.gates)
        release_artifacts = self._release_artifacts(config, package_artifacts) if package_gate_ok else []
        apk = self._release_apk(config, package_artifacts) if package_gate_ok else None

        # Signature/certificate verification is an artifact gate, not a device
        # gate. APK and AAB/JAR outputs are all supported and the actual signer
        # fingerprints are promoted into the release evidence. When an expected
        # fingerprint is configured it must match; otherwise identity verification
        # still proves that all produced release artifacts use one signer.
        if package_gate_ok and options.verify_signature and not dry_run:
            started = time.monotonic()
            verifications: list[dict[str, Any]] = []
            identities: set[str] = set()
            failure = ""
            if not release_artifacts:
                failure = "Release pipeline produced no signed APK/AAB artifact"
            else:
                manager = AndroidSigningManager(context, env=self.env)
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

        upgrade_ready = options.upgrade_smoke and bool(options.previous_apk)
        if package_gate_ok and options.upgrade_smoke and not options.previous_apk:
            session.add_gate("upgrade-smoke", "skipped", required=False, detail="No android.release.previous_apk was configured")
        device_needed = options.install_smoke or upgrade_ready or options.cold_start_smoke or options.restart_smoke or options.restore_restart_smoke or options.process_death_smoke
        operations: AndroidDeviceOperations | None = None
        if package_gate_ok and device_needed and not dry_run:
            try:
                operations = AndroidDeviceOperations.from_config(config, env=self.env)
                selected = operations.select_device(enrich=True)
                session.device = selected.as_dict()
            except Exception as exc:
                required = options.device_tests == "required" or device_needed
                session.add_gate("device-readiness", "failed" if required else "skipped", required=required, detail=str(exc))

        def add_smoke(smoke: SmokeGateResult, *, required: bool = True) -> None:
            session.add_gate(smoke.gate, smoke.status, required=required, duration_seconds=smoke.duration_ms / 1000.0, detail=smoke.detail, metadata=smoke.evidence)
            all_results.append(OperationResult(smoke.gate, ["devtool", "android", "smoke", smoke.gate], 0 if smoke.ok else 1, smoke.duration_ms / 1000.0, smoke.detail, [], metadata=smoke.as_dict()))

        if package_gate_ok and apk and not dry_run:
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
        elif package_gate_ok and device_needed and not apk and not dry_run:
            session.add_gate("device-smoke", "failed", detail="Configured device smoke gates require an installable APK; release produced only non-APK artifacts")

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
    checks.append({"id": "release.uses-capability-context", "pass": "resolve_android_execution_context" in release_src})
    failed = [item["id"] for item in checks if not item["pass"]]
    return {"schema_version": 1, "status": "pass" if not failed else "fail", "checks": checks, "failed": failed}


__all__ = ["DEVICE_TEST_POLICIES", "AndroidReleaseOptions", "AndroidReleaseResult", "AndroidReleaseOrchestrator", "release_options", "audit_android_release_convergence"]
