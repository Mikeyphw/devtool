from __future__ import annotations

from dataclasses import dataclass, field
import copy
from pathlib import Path
import re
from typing import Any

from ..config import load_project_config
from ..models import ProjectConfig
from ..runners import build_validation_specs
from ..validation_control import plan_payload, status_api_payload
from ..validation_sessions import ValidationSessionError, annotate_specs, load_active_session, phase_records


@dataclass(slots=True)
class ValidationCatalog:
    config: ProjectConfig
    session: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    test_profiles: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    configured_tasks: list[str] = field(default_factory=list)
    detected_tasks: list[str] = field(default_factory=list)
    task_error: str = ""
    task_probe: dict[str, Any] = field(default_factory=dict)
    pytest_tests: list[str] = field(default_factory=list)
    pytest_discovery_source: str = ""
    pytest_discovery_error: str = ""
    plan_error: str = ""

    @property
    def has_session(self) -> bool:
        return bool(self.session.get("session_id"))

    @property
    def revision(self) -> int:
        try:
            return int(self.session.get("revision", -1))
        except (TypeError, ValueError):
            return -1

    @property
    def state(self) -> str:
        return str(self.session.get("effective_state") or self.session.get("state") or "")

    @property
    def phases(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.plan.get("phases", []) if isinstance(item, dict)]

    @property
    def pending_phases(self) -> list[dict[str, Any]]:
        return [item for item in self.phases if str(item.get("status")) in {"pending", "interrupted", "failed", "blocked"}]

    @property
    def failed_phases(self) -> list[dict[str, Any]]:
        return [item for item in self.phases if str(item.get("status")) in {"failed", "blocked", "interrupted"}]


def build_catalog(
    config: ProjectConfig,
    *,
    include_runtime_tasks: bool = False,
    include_runtime_tests: bool = False,
) -> ValidationCatalog:
    session_payload: dict[str, Any] = {}
    plan: dict[str, Any] = {}
    plan_error = ""
    try:
        session = load_active_session(config.repo_root)
    except ValidationSessionError:
        session = None
    if session is not None:
        session_payload = status_api_payload(session, include_events=True, event_limit=20)
        plan = plan_payload(session)
    else:
        plan, plan_error = preview_validation_plan(config)

    targets = sorted(config.targets)
    variants: set[str] = set()
    if config.selected_target and config.selected_target in config.targets:
        variants.update(config.targets[config.selected_target].variants)
    for target in config.targets.values():
        variants.update(target.variants)
    if config.variant:
        variants.add(config.variant)

    test_profiles = []
    profiles = config.runner_options.get("test_profiles", {})
    if isinstance(profiles, dict):
        test_profiles = sorted(str(key) for key in profiles)

    configured = configured_gradle_tasks(config)
    detected = static_gradle_tasks(config.repo_root)
    runtime: list[str] = []
    task_error = ""
    task_probe: dict[str, Any] = {}
    if include_runtime_tasks and config.kind in {"android", "gradle"}:
        runtime, task_error, task_probe = runtime_gradle_task_probe(config)
    tasks = sorted(dict.fromkeys([*configured, *detected, *runtime]))

    pytest_tests: list[str] = []
    pytest_discovery_source = ""
    pytest_discovery_error = ""
    if config.kind == "python":
        from ..test_selection import discover_tests, discover_tests_for_ui
        if include_runtime_tests:
            discovery = discover_tests_for_ui(config)
            pytest_tests = [item.id for item in discovery.tests if item.framework == "pytest"]
            pytest_discovery_source = discovery.source
            pytest_discovery_error = discovery.error
        else:
            pytest_tests = [item.id for item in discover_tests(config) if item.framework == "pytest"]
            pytest_discovery_source = "source" if pytest_tests else ""

    return ValidationCatalog(
        config=config,
        session=session_payload,
        plan=plan,
        targets=targets,
        variants=sorted(variants),
        test_profiles=test_profiles,
        tasks=tasks,
        configured_tasks=configured,
        detected_tasks=detected,
        task_error=task_error,
        task_probe=task_probe,
        pytest_tests=pytest_tests,
        pytest_discovery_source=pytest_discovery_source,
        pytest_discovery_error=pytest_discovery_error,
        plan_error=plan_error,
    )


def preview_validation_plan(config: ProjectConfig) -> tuple[dict[str, Any], str]:
    """Resolve the real runner plan without creating a durable session."""
    planning = copy.deepcopy(config)
    validation = planning.runner_options.get("validation", {})
    validation = dict(validation) if isinstance(validation, dict) else {}
    validation["resumable"] = True
    planning.runner_options["validation"] = validation
    try:
        specs = build_validation_specs(planning)
        records = phase_records(annotate_specs(specs))
    except (OSError, SystemExit, ValidationSessionError, ValueError) as exc:
        return {"preview": True, "phases": []}, str(exc)
    phases = [
        {
            "key": item.key,
            "name": item.name,
            "label": item.label,
            "index": item.index,
            "total": item.total,
            "status": item.status,
            "depends_on": list(item.depends_on),
            "input_domains": list(item.input_domains),
            "required": bool(item.required),
            "scope": item.scope,
            "supports_progress": bool(item.supports_progress),
            "attempts": int(item.attempts),
            "last_verdict": item.last_verdict,
        }
        for item in records
    ]
    return {"preview": True, "supported_scopes": ["next", "through", "selected", "remaining"], "phases": phases}, ""


def refresh_catalog(
    catalog: ValidationCatalog,
    *,
    repo: str = "",
    config_path: str = "",
    target: str = "",
    variant: str = "",
    solution: str = "",
    project: str = "",
    project_type: str = "auto",
    include_runtime_tasks: bool = False,
    include_runtime_tests: bool = False,
) -> ValidationCatalog:
    """Reload project context, durable status, plan, and task catalogs in-place."""
    fresh_config = load_project_config(
        repo or str(catalog.config.repo_root),
        config_path or None,
        target_name=target,
        variant_name=variant,
        solution_override=solution,
        project_override=project,
        kind_override=project_type or "auto",
    )
    fresh = build_catalog(
        fresh_config,
        include_runtime_tasks=include_runtime_tasks,
        include_runtime_tests=include_runtime_tests,
    )
    for name in ValidationCatalog.__dataclass_fields__:
        setattr(catalog, name, getattr(fresh, name))
    return catalog


def configured_gradle_tasks(config: ProjectConfig) -> list[str]:
    tasks: list[str] = []
    raw = config.runner_options.get("tasks", {})
    if isinstance(raw, dict):
        for value in raw.values():
            tasks.extend(_strings(value))
    validation = config.runner_options.get("validation", {})
    if isinstance(validation, dict):
        for key, value in validation.items():
            if "task" in str(key).lower():
                tasks.extend(_strings(value))
    selection = config.runner_options.get("test_selection", {})
    if isinstance(selection, dict):
        tasks.extend(_strings(selection.get("tasks") or selection.get("task")))
    return sorted(dict.fromkeys(task for task in tasks if task))


def static_gradle_tasks(repo_root: Path) -> list[str]:
    patterns = (
        re.compile(r"tasks\.(?:register|named|create)\s*\(\s*[\"']([^\"']+)[\"']"),
        re.compile(r"\btask\s+([A-Za-z_][\w.-]*)\b"),
        re.compile(r"\b(?:dependsOn|finalizedBy)\s*\(?\s*[\"']([^\"']+)[\"']"),
    )
    tasks: list[str] = []
    ignored = {".git", ".gradle", "build", "node_modules", ".devtool"}
    for pattern in ("**/*.gradle", "**/*.gradle.kts"):
        for path in repo_root.glob(pattern):
            if any(part in ignored for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for regex in patterns:
                tasks.extend(match.group(1).strip() for match in regex.finditer(text))
    return sorted(dict.fromkeys(task for task in tasks if task))


def runtime_gradle_task_probe(config: ProjectConfig) -> tuple[list[str], str, dict[str, Any]]:
    root = Path(config.project_root).expanduser() if config.project_root else config.repo_root
    if not root.is_absolute():
        root = config.repo_root / root
    wrapper = root / "gradlew"
    requested_launcher = "wrapper" if wrapper.is_file() else "system"
    try:
        from ..gradle_takeover import run_gradle_probe
        completed, receipt = run_gradle_probe(
            config, ["tasks", "--all", "-q"], cwd=root,
            requested_launcher=requested_launcher, timeout=45,
        )
    except (OSError, TimeoutError) as exc:
        return [], str(exc), {}
    except Exception as exc:
        return [], str(exc), {}
    probe = {
        "intent": "probe",
        "receipt_id": receipt.receipt_id,
        "launcher_identity": receipt.launcher_identity,
        "destination_identity": receipt.destination_identity,
        "configuration_identity": receipt.configuration_identity,
        "returncode": int(completed.returncode),
    }
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        return [], message[-2000:], probe
    tasks: list[str] = []
    for line in completed.stdout.splitlines():
        match = re.match(r"^([:\w.-]+)\s+-\s+", line.strip())
        if match:
            tasks.append(match.group(1))
    return sorted(dict.fromkeys(tasks)), "", probe


def runtime_gradle_tasks(config: ProjectConfig) -> tuple[list[str], str]:
    """Compatibility projection for callers that do not need probe evidence."""
    tasks, error, _probe = runtime_gradle_task_probe(config)
    return tasks, error


def dependency_closure(phases: list[dict[str, Any]], selected_names: list[str]) -> tuple[list[str], list[str]]:
    by_key = {str(item.get("key")): item for item in phases}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in phases:
        by_name.setdefault(str(item.get("name")), []).append(item)
    selected_keys: list[str] = []
    for name in selected_names:
        if name in by_key:
            selected_keys.append(name)
        elif by_name.get(name):
            selected_keys.extend(str(item.get("key")) for item in by_name[name])
    closure: set[str] = set()

    def visit(key: str) -> None:
        if key in closure:
            return
        item = by_key.get(key)
        if item is None:
            return
        for dependency in item.get("depends_on", []) or []:
            visit(str(dependency))
        closure.add(key)

    for key in selected_keys:
        visit(key)
    ordered = [str(item.get("name")) for item in phases if str(item.get("key")) in closure]
    already = [
        str(item.get("name"))
        for item in phases
        if str(item.get("key")) in closure and str(item.get("status")) in {"passed", "not-applicable", "skipped"}
    ]
    return list(dict.fromkeys(ordered)), list(dict.fromkeys(already))


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        output: list[str] = []
        for item in value:
            output.extend(_strings(item))
        return output
    text = str(value).strip()
    return [text] if text else []
