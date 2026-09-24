from __future__ import annotations

"""Cross-surface repository-wrapper final seal checks.

The wrapper final seal is intentionally read-only.  It ties together the
configuration, launcher, help/discovery, completion, doctor, and resolution
surfaces so wrapper regressions show up as a single auditable report instead of
as disconnected command failures.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .completion_state import build_completion_state
from .doctor import doctor_payload
from .models import ProjectConfig, WrapperCommandConfig
from .core.events import EventBus, RunStarted
from .invocation_origin import build_invocation_metadata
from .wrapper_help import render_wrapper_command_help, render_wrapper_overview, wrapper_discovery_payload
from .wrapper_launcher import launcher_status, launcher_status_as_dict
from .wrapper_resolution import WrapperResolution, WrapperResolutionError, resolve_wrapper_argv, wrapper_resolution_as_dict
from .wrapper_execution import probe_wrapper_execution_bridge, wrapper_resolution_command_token

WRAPPER_FINAL_SEAL_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class WrapperSealCheck:
    section: str
    name: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.data:
            payload.pop("data", None)
        return payload


@dataclass(frozen=True, slots=True)
class WrapperFinalSeal:
    schema_version: int
    repository: str
    status: str
    commands: tuple[str, ...]
    checks: tuple[WrapperSealCheck, ...]

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _status(checks: list[WrapperSealCheck]) -> str:
    if any(item.status == "error" for item in checks):
        return "error"
    if any(item.status == "warning" for item in checks):
        return "warning"
    return "ok"


def _check(section: str, name: str, status: str, detail: str, **data: Any) -> WrapperSealCheck:
    return WrapperSealCheck(section=section, name=name, status=status, detail=detail, data={k: v for k, v in data.items() if v not in (None, "", [], {}, ())})


def _required_inputs(command: WrapperCommandConfig) -> list[str]:
    values: list[str] = []
    for option in command.options.values():
        if option.required and not option.has_default:
            values.append(f"--{option.name}")
    for positional in command.positionals:
        if positional.required:
            values.append(positional.name)
    return values


def _doctor_check_map(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for item in payload.get("checks", []) if isinstance(payload.get("checks"), list) else []:
        if isinstance(item, dict):
            out[(str(item.get("section", "")), str(item.get("name", "")))] = item
    return out


def _synthetic_bridge_resolution(config: ProjectConfig) -> WrapperResolution:
    return WrapperResolution(
        schema=config.wrapper.schema,
        mode="execute",
        command="seal-probe",
        command_spelling="seal-probe",
        execution_kind="devtool",
        devtool_command="status",
        global_argv=("--repo", str(config.repo_root)),
        canonical_argv=("--repo", str(config.repo_root), "status"),
        redacted_canonical_argv=("--repo", str(config.repo_root), "status"),
        repo_root=str(config.repo_root),
        launch_cwd=str(config.repo_root),
    )


def _append_execution_evidence_checks(
    checks: list[WrapperSealCheck],
    config: ProjectConfig,
    resolution: WrapperResolution,
    *,
    name: str,
) -> None:
    try:
        probe = probe_wrapper_execution_bridge(resolution)
    except Exception as exc:
        checks.append(_check("execution-bridge", name, "error", f"bridge probe failed: {exc}"))
        checks.append(_check("execution-origin", name, "error", "bridge probe failed before provenance persistence"))
        return

    expected_command = resolution.command or resolution.command_spelling or probe.command_token
    expected_token = wrapper_resolution_command_token(resolution)
    controls = {flag: count for flag, count in probe.internal_control_counts}
    bridge_ok = (
        probe.ok
        and probe.command_token == expected_token
        and probe.invocation_source == "wrapper"
        and probe.invocation_command == expected_command
        and probe.wrapper_schema == str(resolution.schema)
    )
    checks.append(_check(
        "execution-bridge",
        name,
        "ok" if bridge_ok else "error",
        "canonical bridge re-entered exactly once" if bridge_ok else "canonical wrapper bridge contract mismatch",
        command_token=probe.command_token,
        reentry_count=probe.reentry_count,
        callback_exit_code=probe.callback_exit_code,
        invocation_source=probe.invocation_source,
        invocation_command=probe.invocation_command,
        wrapper_schema=probe.wrapper_schema,
        internal_control_counts=controls,
        nested_wrapper_dispatch=probe.nested_wrapper_dispatch,
    ))

    try:
        metadata = build_invocation_metadata(
            source=probe.invocation_source,
            command=probe.invocation_command,
            wrapper_schema=probe.wrapper_schema,
        )
        run_id = f"wrapper-seal-{name.replace(':', '-')}"
        bus = EventBus()
        bus.publish(RunStarted(
            run_id=run_id,
            command=probe.command_token,
            project=str(config.repo_root),
            source=str(metadata["invocation_source"]),
            metadata=metadata,
        ))
        snapshot = bus.execution_store.snapshot(run_id)
    except Exception as exc:
        checks.append(_check("execution-origin", name, "error", f"EXO persistence probe failed: {exc}"))
        return

    origin_ok = bool(
        bridge_ok
        and snapshot is not None
        and snapshot.invocation_source == "wrapper"
        and snapshot.invocation_command == expected_command
        and str(snapshot.invocation_metadata.get("wrapper_schema", "")) == str(resolution.schema)
        and snapshot.invocation_metadata.get("wrapper_command") == expected_command
    )
    checks.append(_check(
        "execution-origin",
        name,
        "ok" if origin_ok else "error",
        "wrapper provenance persisted through EXO execution store" if origin_ok else "wrapper provenance did not persist through EXO execution store",
        invocation_source=getattr(snapshot, "invocation_source", "") if snapshot is not None else "",
        invocation_command=getattr(snapshot, "invocation_command", "") if snapshot is not None else "",
        invocation_metadata=dict(getattr(snapshot, "invocation_metadata", {}) or {}) if snapshot is not None else {},
    ))


def build_wrapper_final_seal(config: ProjectConfig) -> WrapperFinalSeal:
    checks: list[WrapperSealCheck] = []
    command_names = tuple(sorted(config.wrapper.commands))

    if not config.wrapper.commands:
        checks.append(_check("contract", "configured", "error", "no repository wrapper commands are configured"))
        return WrapperFinalSeal(
            schema_version=WRAPPER_FINAL_SEAL_SCHEMA_VERSION,
            repository=str(config.repo_root),
            status="error",
            commands=(),
            checks=tuple(checks),
        )

    checks.append(_check("contract", "schema", "ok", f"schema {config.wrapper.schema}", schema=config.wrapper.schema))

    _append_execution_evidence_checks(
        checks,
        config,
        _synthetic_bridge_resolution(config),
        name="generic-contract",
    )

    if config.wrapper.default_command:
        status = "ok" if config.wrapper.default_command in config.wrapper.commands else "error"
        checks.append(_check("contract", "default-command", status, config.wrapper.default_command))
    else:
        checks.append(_check("contract", "default-command", "warning", "no default wrapper command configured"))

    missing_descriptions = [name for name, command in sorted(config.wrapper.commands.items()) if not command.description]
    checks.append(_check(
        "contract",
        "command-descriptions",
        "ok" if not missing_descriptions else "warning",
        "all commands documented" if not missing_descriptions else "missing descriptions: " + ", ".join(missing_descriptions),
        missing=missing_descriptions,
    ))

    launchers = launcher_status(config.repo_root)
    checks.append(_check(
        "launchers",
        "current",
        "ok" if launchers.ok else "error",
        "devtoolw launchers current" if launchers.ok else ", ".join(f"{item.name}:{item.reason or 'not current'}" for item in launchers.files),
        launchers=launcher_status_as_dict(launchers),
    ))

    docs_path = config.repo_root / "docs" / "WRAPPER.md"
    checks.append(_check(
        "docs",
        "wrapper-guide",
        "ok" if docs_path.is_file() else "error",
        str(docs_path) if docs_path.is_file() else "docs/WRAPPER.md missing",
    ))

    discovery = wrapper_discovery_payload(config)
    discovery_names = tuple(item.get("name", "") for item in discovery.get("commands", []) if isinstance(item, dict))
    checks.append(_check(
        "discovery",
        "commands",
        "ok" if tuple(sorted(discovery_names)) == command_names else "error",
        ", ".join(discovery_names) or "no commands discovered",
        expected=list(command_names),
        discovered=list(discovery_names),
    ))

    completion = build_completion_state(config).as_dict()
    completion_commands = tuple(sorted(str(item) for item in completion.get("wrapper_commands", [])))
    checks.append(_check(
        "completion",
        "commands",
        "ok" if completion_commands == command_names else "error",
        ", ".join(completion_commands) or "no wrapper commands in completion state",
        expected=list(command_names),
        completed=list(completion_commands),
    ))

    expected_aliases = sorted({alias for command in config.wrapper.commands.values() for alias in command.aliases})
    completion_aliases = sorted(str(item) for item in completion.get("wrapper_aliases", []))
    checks.append(_check(
        "completion",
        "aliases",
        "ok" if completion_aliases == expected_aliases else "error",
        ", ".join(completion_aliases) if completion_aliases else "no aliases",
        expected=expected_aliases,
        completed=completion_aliases,
    ))

    overview = render_wrapper_overview(config)
    missing_overview = [name for name in command_names if name not in overview]
    checks.append(_check(
        "help",
        "overview",
        "ok" if not missing_overview else "error",
        "overview lists all commands" if not missing_overview else "overview missing: " + ", ".join(missing_overview),
    ))

    for name, command in sorted(config.wrapper.commands.items()):
        help_text = render_wrapper_command_help(config, name)
        checks.append(_check(
            "help",
            f"command:{name}",
            "ok" if f"Command: {name}" in help_text and "Usage:" in help_text else "error",
            "command help rendered",
        ))

        help_resolution = resolve_wrapper_argv(config, [name, "--help"], launch_cwd=config.repo_root)
        checks.append(_check(
            "resolution",
            f"help:{name}",
            "ok" if help_resolution.mode == "help" and help_resolution.command == name else "error",
            f"mode={help_resolution.mode}",
        ))

        required = _required_inputs(command)
        if required:
            checks.append(_check(
                "resolution",
                f"execute:{name}",
                "skipped",
                "requires user input: " + ", ".join(required),
                required=required,
            ))
            checks.append(_check(
                "execution-bridge",
                f"command:{name}",
                "skipped",
                "requires user input; generic bridge contract remains sealed",
                required=required,
            ))
            checks.append(_check(
                "execution-origin",
                f"command:{name}",
                "skipped",
                "requires user input; generic EXO persistence contract remains sealed",
                required=required,
            ))
            continue
        try:
            resolution = resolve_wrapper_argv(config, [name], launch_cwd=config.repo_root)
        except WrapperResolutionError as exc:
            checks.append(_check("resolution", f"execute:{name}", "error", str(exc)))
            checks.append(_check("execution-bridge", f"command:{name}", "error", "resolution failed before bridge probe"))
            checks.append(_check("execution-origin", f"command:{name}", "error", "resolution failed before provenance probe"))
            continue
        resolution_ok = resolution.mode == "execute" and bool(resolution.canonical_argv)
        checks.append(_check(
            "resolution",
            f"execute:{name}",
            "ok" if resolution_ok else "error",
            "canonical argv resolved" if resolution_ok else "canonical argv resolution incomplete",
            resolution=wrapper_resolution_as_dict(resolution),
        ))
        _append_execution_evidence_checks(
            checks,
            config,
            resolution,
            name=f"command:{name}",
        )

    try:
        doctor = doctor_payload(config)
    except Exception as exc:  # pragma: no cover - defensive; final seal should report instead of crashing.
        checks.append(_check("doctor", "payload", "error", str(exc)))
    else:
        doctor_checks = _doctor_check_map(doctor)
        for key in (("project", "wrapper"), ("project", "wrapper-launchers"), ("project", "wrapper-docs")):
            item = doctor_checks.get(key)
            checks.append(_check(
                "doctor",
                ":".join(key),
                "ok" if item and item.get("status") == "ok" else "error",
                str(item.get("detail", "ok")) if item else "missing doctor check",
                doctor=item or {},
            ))

    return WrapperFinalSeal(
        schema_version=WRAPPER_FINAL_SEAL_SCHEMA_VERSION,
        repository=str(config.repo_root),
        status=_status(checks),
        commands=command_names,
        checks=tuple(checks),
    )


def wrapper_final_seal_as_dict(seal: WrapperFinalSeal) -> dict[str, Any]:
    return {
        "schema_version": seal.schema_version,
        "repository": seal.repository,
        "status": seal.status,
        "ok": seal.ok,
        "commands": list(seal.commands),
        "checks": [item.as_dict() for item in seal.checks],
    }


def render_wrapper_final_seal(seal: WrapperFinalSeal) -> str:
    lines = [
        f"Wrapper final seal: {seal.status}",
        f"Repository: {seal.repository}",
    ]
    if seal.commands:
        lines.append("Commands: " + ", ".join(seal.commands))
    lines.append("Checks:")
    for item in seal.checks:
        lines.append(f"  [{item.status}] {item.section}/{item.name}: {item.detail}")
    return "\n".join(lines) + "\n"
