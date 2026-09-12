from __future__ import annotations

"""Repository-wide FS03 release seal.

The seal is intentionally deterministic and side-effect free.  It audits the
checked-out repository and public compatibility/configuration contracts; the
actual test/build execution remains owned by Devtool validation/artifact gates.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import fnmatch
import tomllib

from .compatibility import COMPATIBILITY_ENTRIES, compatibility_report
from .completion import ZSH_COMPLETION
from .config_migration import analyze_config_migration_text
from .ui_core.machine import IPC_PROTOCOL_VERSION, MACHINE_SCHEMA_VERSION, TASKER_SCHEMA_VERSION
from .validation_sessions import SESSION_SCHEMA_VERSION

FINAL_SEAL_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SealCheck:
    id: str
    status: str
    summary: str
    details: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_REQUIRED_REPORTS = (
    "MP01_MP02_POLYGLOT_TARGETS_WORKFLOWS_REPORT.md",
    "MP03_MP04_WORKFLOW_DAG_CROSS_RUNNER_REPORT.md",
    "MP05_MP06_CROSS_TARGET_WORKSPACE_GROUPS_REPORT.md",
    "MP07_MP08_AFFECTED_TOOLCHAIN_PREFLIGHT_REPORT.md",
    "MP09_MP10_SAFE_SCHEDULING_GRAPH_OBSERVABILITY_REPORT.md",
    "UI01_UI02_SHARED_UI_STATE_ACTION_NAVIGATION_REPORT.md",
    "UI03_UI04_UI_PREFERENCES_TOKENS_COPY_REPORT.md",
    "UI05_UI06_CAPABILITIES_RESPONSIVE_PROGRESS_DIAGNOSTICS_REPORT.md",
    "UI07_UI08_CONFIRMATION_NOTIFICATION_TERMINAL_MIGRATION_REPORT.md",
    "UI09_UI10_DESKTOP_ANDROID_UI_CORE_MIGRATION_REPORT.md",
    "UI11_UI12_MACHINE_IPC_COMPLETION_CONFIG_REPORT.md",
    "FS01_FS02_COMPATIBILITY_DOCUMENTATION_REPORT.md",
    "HROPS01_COMPLETE_EMBEDDED_HOOK_RUNTIME_REPORT.md",
    "AOPS02_ANDROID_EXECUTION_DEVICE_FABRIC_REPORT.md",
    "AOPS03_ANDROID_SIGNING_INSTRUMENTATION_OPERATIONS_REPORT.md",
    "AOPS04_ANDROID_RELEASE_EVIDENCE_FINAL_SEAL_REPORT.md",
    "PYTEST_REVITALIZATION_REPORT.md",
    "GRADLE_ANDROID_TASK_OPTIMIZATION_REPORT.md",
    "ROADMAP_DELIVERY_AUDIT_REPORT.md",
)

_REQUIRED_DOCS = (
    "docs/BACKWARD_COMPATIBILITY.md",
    "docs/EXAMPLES.md",
    "docs/CONFIGURATION.md",
    "docs/POLYGLOT_TARGETS_AND_WORKFLOWS.md",
    "docs/WORKFLOW_SCHEDULING_AND_PLANNING.md",
    "docs/AFFECTED_TARGETS.md",
    "docs/UI_UX_CONTRACT.md",
    "docs/UI_INTEGRATION.md",
    "docs/architecture/overview.md",
    "docs/architecture/ui-core.md",
    "docs/architecture/ui-machine-completion-config.md",
)

_REQUIRED_EXAMPLES = (
    "examples/python-service.devtool.toml",
    "examples/polyglot-workspace.devtool.toml",
    "examples/ui-profile.devtool.toml",
    "examples/release-validation.devtool.toml",
)

_REQUIRED_TESTS = (
    "tests/test_mp01_mp02_target_job_workflow_contracts.py",
    "tests/test_mp03_mp04_workflow_graph_runtime.py",
    "tests/test_mp05_mp06_cross_target_workspace_groups.py",
    "tests/test_mp07_mp08_affected_toolchain.py",
    "tests/test_mp09_mp10_safe_scheduler_plan.py",
    "tests/test_ui01_ui02_ui_core.py",
    "tests/test_ui03_ui04_preferences_tokens_copy.py",
    "tests/test_ui05_ui06_capabilities_progress_diagnostics.py",
    "tests/test_ui07_ui08_confirmation_notification_terminal.py",
    "tests/test_ui09_ui10_desktop_android_ui_core_migration.py",
    "tests/test_ui11_ui12_machine_completion_config.py",
    "tests/test_fs01_fs02_compatibility_docs.py",
    "tests/test_fs03_final_release_seal.py",
    "tests/test_ts00_pytest_selection_parity.py",
    "tests/test_hrops01_hook_runtime.py",
    "tests/test_aops02_android_execution_device_fabric.py",
    "tests/test_aops03_android_signing_instrumentation_operations.py",
    "tests/test_aops04_android_release_evidence_final_seal.py",
    "tests/test_pytest_revitalization.py",
    "tests/test_gradle_android_task_optimization.py",
    "tests/test_roadmap_delivery_audit.py",
)

_ALLOWED_BACKUP_PATTERNS = (
    ".devtool/**",
    "**/.devtool/**",
)


def _check(id: str, ok: bool, summary: str, details: list[str] | tuple[str, ...] = ()) -> SealCheck:
    return SealCheck(id=id, status="pass" if ok else "fail", summary=summary, details=tuple(details))


def _existing(repo_root: Path, paths: tuple[str, ...]) -> tuple[list[str], list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for rel in paths:
        (present if (repo_root / rel).is_file() else missing).append(rel)
    return present, missing


def _tracked_backup_paths(repo_root: Path) -> list[str]:
    # Keep the seal independent from GitPython and safe outside a git checkout.
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "ls-files"], cwd=repo_root, text=True, capture_output=True, check=False, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    bad: list[str] = []
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        if any(fnmatch.fnmatch(rel, pattern) for pattern in _ALLOWED_BACKUP_PATTERNS):
            continue
        name = Path(rel).name
        if (
            ".before-" in name
            or name.endswith((".bak", ".backup", ".orig", "~"))
            or ".rej" == Path(name).suffix
        ):
            bad.append(rel)
    return sorted(bad)


def final_seal_report(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    checks: list[SealCheck] = []

    cfg_path = repo_root / ".devtool.toml"
    if cfg_path.is_file():
        cfg_text = cfg_path.read_text(encoding="utf-8")
        migrations = analyze_config_migration_text(cfg_text)
        checks.append(_check(
            "config.canonical",
            not migrations,
            "repository configuration uses canonical syntax" if not migrations else "repository configuration still needs migration",
            [f"{item.compatibility_id}: {item.key}" for item in migrations],
        ))
        try:
            parsed_cfg = tomllib.loads(cfg_text)
        except tomllib.TOMLDecodeError as exc:
            checks.append(_check("config.parse", False, "repository configuration is invalid TOML", [str(exc)]))
            parsed_cfg = {}
        else:
            checks.append(_check("config.parse", True, "repository configuration parses as TOML"))
        workspace_default = str((parsed_cfg.get("workspace") or {}).get("default_target") or "") if isinstance(parsed_cfg, dict) else ""
        targets = parsed_cfg.get("targets") or {} if isinstance(parsed_cfg, dict) else {}
        roots_missing: list[str] = []
        if isinstance(targets, dict):
            for name, raw in targets.items():
                if not isinstance(raw, dict):
                    continue
                root = str(raw.get("root") or ".")
                if not (repo_root / root).exists():
                    roots_missing.append(f"{name}:{root}")
        checks.append(_check(
            "workspace.targets",
            bool(workspace_default) and isinstance(targets, dict) and workspace_default in targets and not roots_missing,
            "workspace default target and target roots are valid",
            ([f"default_target={workspace_default!r}"] if not workspace_default or workspace_default not in targets else []) + roots_missing,
        ))
    else:
        parsed_cfg = {}
        checks.append(_check("config.canonical", False, "repository .devtool.toml is missing"))

    compat = compatibility_report(cfg_path if cfg_path.exists() else None)
    compat_ids = [entry.id for entry in COMPATIBILITY_ENTRIES]
    compat_ok = (
        len(compat_ids) == len(set(compat_ids))
        and len(compat_ids) >= 24
        and int(compat.get("summary", {}).get("active_config_migrations", 0)) == 0
    )
    checks.append(_check(
        "compatibility.registry",
        compat_ok,
        "compatibility registry is unique, explicit, and has no active repository migrations",
        [] if compat_ok else [
            f"entries={len(compat_ids)} unique={len(set(compat_ids))}",
            f"active_migrations={compat.get('summary', {}).get('active_config_migrations', 0)}",
        ],
    ))

    schema_details = [
        f"machine={MACHINE_SCHEMA_VERSION}",
        f"ipc={IPC_PROTOCOL_VERSION}",
        f"tasker={TASKER_SCHEMA_VERSION}",
        f"validation_session={SESSION_SCHEMA_VERSION}",
    ]
    checks.append(_check(
        "schemas.versioned",
        all(isinstance(v, int) and v >= 1 for v in (MACHINE_SCHEMA_VERSION, IPC_PROTOCOL_VERSION, TASKER_SCHEMA_VERSION, SESSION_SCHEMA_VERSION)),
        "public machine/IPC/Tasker/session schemas are explicitly versioned",
        schema_details,
    ))

    _, missing_reports = _existing(repo_root, _REQUIRED_REPORTS)
    checks.append(_check(
        "roadmap.reports",
        not missing_reports,
        "all roadmap implementation reports, including HROPS01, AOPS02-AOPS04, pytest revitalization, Gradle optimization, and delivery audit, are present",
        missing_reports,
    ))

    _, missing_docs = _existing(repo_root, _REQUIRED_DOCS)
    checks.append(_check("docs.required", not missing_docs, "canonical release documentation is present", missing_docs))

    example_errors: list[str] = []
    for rel in _REQUIRED_EXAMPLES:
        path = repo_root / rel
        if not path.is_file():
            example_errors.append(f"missing:{rel}")
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            example_errors.append(f"invalid:{rel}:{exc}")
            continue
        project = data.get("project") if isinstance(data, dict) else None
        if isinstance(project, dict) and project:
            if "kind" in project or "type" in project or "runner" not in project:
                example_errors.append(f"noncanonical:{rel}")
    checks.append(_check("examples.canonical", not example_errors, "canonical TOML examples parse and use modern identity fields", example_errors))

    _, missing_tests = _existing(repo_root, _REQUIRED_TESTS)
    checks.append(_check(
        "tests.roadmap-contracts",
        not missing_tests,
        "roadmap, TS00, Android Operations, pytest revitalization, Gradle optimization, and delivery-audit contract suites are present",
        missing_tests,
    ))

    completion_path = repo_root / "completions" / "_devtool"
    completion_ok = completion_path.is_file() and completion_path.read_text(encoding="utf-8") == ZSH_COMPLETION
    checks.append(_check("completion.synchronized", completion_ok, "generated and packaged zsh completion are synchronized"))

    profile_text = (repo_root / "devtool" / "validation_profiles.py").read_text(encoding="utf-8") if (repo_root / "devtool" / "validation_profiles.py").is_file() else ""
    checks.append(_check(
        "validation.final-seal-profile",
        '"final-seal"' in profile_text,
        "final-seal validation workflow profile is available",
    ))

    try:
        from .android.release import audit_android_release_convergence
        android_audit = audit_android_release_convergence(repo_root)
        checks.append(_check(
            "android.operations-roadmap",
            android_audit.get("status") == "pass",
            "HROPS01 and AOPS02-AOPS04 Android/hook execution paths are converged",
            list(android_audit.get("failed") or []),
        ))
    except Exception as exc:
        checks.append(_check("android.operations-roadmap", False, "Android Operations convergence audit failed to execute", [str(exc)]))

    try:
        from .artifacts import SUPPORTED_ARCHIVE_SUFFIXES
        required_suffixes = {".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar.zst", ".tzst"}
        missing_suffixes = sorted(required_suffixes - set(SUPPORTED_ARCHIVE_SUFFIXES))
        completion_text = (repo_root / "completions" / "_devtool").read_text(encoding="utf-8") if (repo_root / "completions" / "_devtool").is_file() else ""
        completion_missing = [suffix for suffix in sorted(required_suffixes) if suffix not in completion_text]
        checks.append(_check(
            "artifacts.archive-formats",
            not missing_suffixes and not completion_missing,
            "ZIP and tar overlay/bundle artifact suffixes are supported and discoverable",
            ([f"missing_supported:{suffix}" for suffix in missing_suffixes] + [f"missing_completion:{suffix}" for suffix in completion_missing]),
        ))
    except Exception as exc:
        checks.append(_check("artifacts.archive-formats", False, "archive-format audit failed to execute", [str(exc)]))

    backups = _tracked_backup_paths(repo_root)
    checks.append(_check("repository.backup-hygiene", not backups, "no transient backup/reject files are tracked", backups))

    readme = (repo_root / "README.md").read_text(encoding="utf-8") if (repo_root / "README.md").is_file() else ""
    seal_doc = repo_root / "docs" / "FINAL_RELEASE_SEAL.md"
    checks.append(_check(
        "release.discoverability",
        seal_doc.is_file() and "final-seal" in readme and "FS03" in readme,
        "FS03 final seal is documented and discoverable from README",
        [str(seal_doc.relative_to(repo_root))] if not seal_doc.is_file() else [],
    ))

    failures = [item for item in checks if item.status == "fail"]
    return {
        "schema_version": FINAL_SEAL_SCHEMA_VERSION,
        "status": "pass" if not failures else "fail",
        "repo_root": str(repo_root),
        "summary": {
            "checks": len(checks),
            "passed": len(checks) - len(failures),
            "failed": len(failures),
            "compatibility_entries": len(COMPATIBILITY_ENTRIES),
            "roadmap_reports": len(_REQUIRED_REPORTS),
            "roadmap_test_files": len(_REQUIRED_TESTS),
        },
        "checks": [item.as_dict() for item in checks],
        "compatibility": compat.get("summary", {}),
    }


def render_final_seal_text(payload: dict[str, Any]) -> str:
    lines = [
        f"FS03 final seal: {str(payload.get('status', 'unknown')).upper()}",
        f"schema: {payload.get('schema_version')}",
    ]
    for item in payload.get("checks", []):
        marker = "PASS" if item.get("status") == "pass" else "FAIL"
        lines.append(f"[{marker}] {item.get('id')}: {item.get('summary')}")
        for detail in item.get("details") or []:
            lines.append(f"  - {detail}")
    summary = payload.get("summary", {})
    lines.append(f"checks: {summary.get('passed', 0)} passed, {summary.get('failed', 0)} failed")
    return "\n".join(lines)
