from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .core_registry import ConfigFieldSpec, CoreRegistry, TrustedInvocationEnvelope, build_core_registry


_SECRET_MARKERS = ("secret", "token", "password", "credential", "apikey", "api_key", "private_key")


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, str) else _stable_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _nested_get(data: Mapping[str, Any], path: Sequence[str]) -> tuple[bool, Any]:
    current: Any = data
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _field_by_id(registry: CoreRegistry) -> dict[str, ConfigFieldSpec]:
    fields: dict[str, ConfigFieldSpec] = {}
    for document in registry.config_documents:
        for field in document.fields:
            fields.setdefault(field.field_id, field)
    return fields


def _is_sensitive(field: ConfigFieldSpec | None, *, option_sensitive: bool = False, env_name: str | None = None) -> bool:
    if option_sensitive or bool(getattr(field, "sensitive", False)):
        return True
    haystack = ".".join(field.path if field is not None else ())
    if env_name:
        haystack = f"{haystack}.{env_name}"
    return any(marker in haystack.lower() for marker in _SECRET_MARKERS)


@dataclass(frozen=True, slots=True)
class SecretRef:
    """Opaque reference to sensitive material.

    SecretRef intentionally serializes the identity/provenance of the secret, not
    the secret value.  Callers that need the real value must opt into `resolve`.
    """

    ref: str
    source: str = "env"
    required: bool = False

    def resolve(self, env: Mapping[str, str] | None = None) -> str | None:
        if self.source != "env":
            raise ValueError(f"unsupported secret source: {self.source}")
        data = env if env is not None else os.environ
        value = data.get(self.ref)
        if self.required and value is None:
            raise KeyError(f"missing required secret reference: {self.ref}")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "secret-ref", "source": self.source, "ref": self.ref, "required": self.required}


@dataclass(frozen=True, slots=True)
class ConfigProvenance:
    field_id: str
    path: tuple[str, ...]
    source: str
    rank: int
    sensitive: bool = False
    value_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "path": list(self.path),
            "source": self.source,
            "rank": self.rank,
            "sensitive": self.sensitive,
            "value_digest": self.value_digest,
        }


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    values: Mapping[str, Any]
    provenance: tuple[ConfigProvenance, ...]
    semantic_digest: str

    def value(self, field_id: str, default: Any = None) -> Any:
        return self.values.get(field_id, default)

    def safe_values(self) -> dict[str, Any]:
        sensitive = {item.field_id for item in self.provenance if item.sensitive}
        result: dict[str, Any] = {}
        for key, value in self.values.items():
            result[key] = {"redacted": True} if key in sensitive else _safe_value(value)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "semantic_digest": self.semantic_digest,
            "values": self.safe_values(),
            "provenance": [item.to_dict() for item in self.provenance],
        }


@dataclass(frozen=True, slots=True)
class CliProjectScope:
    repo_root: str
    canonical_repo_root: str
    repository_id: str
    scope_digest: str

    @classmethod
    def from_repo(cls, repo_root: str | Path) -> "CliProjectScope":
        raw = Path(repo_root).expanduser()
        canonical = raw.resolve(strict=False)
        git_dir = canonical / ".git"
        git_hint = str(git_dir.resolve(strict=False)) if git_dir.exists() else "no-git"
        payload = {"repo_root": str(canonical), "git_dir": git_hint}
        digest = _sha256(payload)
        return cls(repo_root=str(raw), canonical_repo_root=str(canonical), repository_id=digest[:16], scope_digest=digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "canonical_repo_root": self.canonical_repo_root,
            "repository_id": self.repository_id,
            "scope_digest": self.scope_digest,
        }


@dataclass(frozen=True, slots=True)
class OutputRequest:
    format: str = "human"
    copy: bool = False
    stdout_payload: str = "auto"

    def semantic_payload(self) -> dict[str, Any]:
        # Output mode must not alter command/config semantics.  This payload is
        # intentionally separate from RunDescriptor semantic digests.
        return {"format": self.format, "copy": self.copy, "stdout_payload": self.stdout_payload}

    def to_dict(self) -> dict[str, Any]:
        payload = self.semantic_payload()
        payload["digest"] = _sha256(payload)
        return payload


@dataclass(frozen=True, slots=True)
class InteractionPolicy:
    assume_yes: bool = False
    interactive: bool = True
    allow_prompts: bool = True

    @classmethod
    def from_flags(cls, *, yes: bool = False, interactive: bool = True) -> "InteractionPolicy":
        return cls(assume_yes=yes, interactive=interactive, allow_prompts=interactive and not yes)

    def to_dict(self) -> dict[str, Any]:
        return {"assume_yes": self.assume_yes, "interactive": self.interactive, "allow_prompts": self.allow_prompts}


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    argv: tuple[str, ...]
    repo_root: Path
    cli_values: Mapping[str, Any] = field(default_factory=dict)
    repo_config: Mapping[str, Any] = field(default_factory=dict)
    global_config: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    invocation_source: str = "cli"
    output: OutputRequest = field(default_factory=OutputRequest)
    interaction: InteractionPolicy = field(default_factory=InteractionPolicy)

    @classmethod
    def from_argv(
        cls,
        argv: Sequence[str],
        *,
        repo_root: str | Path,
        cli_values: Mapping[str, Any] | None = None,
        repo_config: Mapping[str, Any] | None = None,
        global_config: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
        invocation_source: str = "cli",
        output: OutputRequest | None = None,
        interaction: InteractionPolicy | None = None,
    ) -> "ResolutionContext":
        return cls(
            argv=tuple(str(item) for item in argv),
            repo_root=Path(repo_root),
            cli_values=dict(cli_values or {}),
            repo_config=dict(repo_config or {}),
            global_config=dict(global_config or {}),
            env=dict(env or {}),
            invocation_source=invocation_source,
            output=output or OutputRequest(),
            interaction=interaction or InteractionPolicy(),
        )


@dataclass(frozen=True, slots=True)
class RunDescriptor:
    run_id: str
    schema_version: int
    command_id: str
    command_digest: str
    config_digest: str
    scope_digest: str
    run_root: str
    private: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "command_id": self.command_id,
            "command_digest": self.command_digest,
            "config_digest": self.config_digest,
            "scope_digest": self.scope_digest,
            "run_root": self.run_root,
            "private": self.private,
        }


@dataclass(frozen=True, slots=True)
class RunEnvelope:
    invocation: TrustedInvocationEnvelope
    scope: CliProjectScope
    config: ResolvedConfig
    output: OutputRequest
    interaction: InteractionPolicy
    descriptor: RunDescriptor

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "invocation": self.invocation.to_dict(),
            "scope": self.scope.to_dict(),
            "config": self.config.to_dict(),
            "output": self.output.to_dict(),
            "interaction": self.interaction.to_dict(),
            "descriptor": self.descriptor.to_dict(),
        }


def _safe_value(value: Any) -> Any:
    if isinstance(value, SecretRef):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(k): _safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(v) for v in value]
    return value


def _config_candidates(context: ResolutionContext, field: ConfigFieldSpec) -> list[tuple[str, Any, int]]:
    candidates: list[tuple[str, Any, int]] = []
    # Higher rank wins.  Source labels are deliberately stable for machine use.
    option_by_config = {
        "cfg.cli.output.copy": "cli.option.copy",
        "cfg.cli.output.stdout_payload": "cli.option.stdout_payload",
        "cfg.cli.environment.execution_environment": "cli.option.execution_environment",
        "cfg.project.repo_root": "cli.option.repo",
        "cfg.project.config_file": "cli.option.config",
        "cfg.workspace.target": "cli.option.target",
    }
    option_id = option_by_config.get(field.field_id)
    if option_id and option_id in context.cli_values:
        candidates.append(("cli", context.cli_values[option_id], 40))
    env_by_config = {
        "cfg.cli.output.copy": "DEVTOOL_COPY",
        "cfg.cli.output.stdout_payload": "DEVTOOL_STDOUT_PAYLOAD",
        "cfg.cli.environment.execution_environment": "DEVTOOL_EXECUTION_ENVIRONMENT",
    }
    env_name = env_by_config.get(field.field_id)
    if env_name and env_name in context.env:
        raw = context.env[env_name]
        if field.value_kind == "bool":
            value: Any = str(raw).strip().lower() in {"1", "true", "yes", "on"}
        else:
            value = raw
        candidates.append((f"env:{env_name}", value, 30))
    present, value = _nested_get(context.repo_config, field.path)
    if present:
        candidates.append(("repo", value, 20))
    present, value = _nested_get(context.global_config, field.path)
    if present:
        candidates.append(("global", value, 10))
    candidates.append(("default", field.default, 0))
    return candidates


def resolve_effective_config(context: ResolutionContext, registry: CoreRegistry | None = None) -> ResolvedConfig:
    registry = registry or build_core_registry()
    fields = _field_by_id(registry)
    option_sensitive = {option.config_field_id: option.sensitive for option in registry.options if option.config_field_id}
    values: dict[str, Any] = {}
    provenance: list[ConfigProvenance] = []
    for field_id, field in sorted(fields.items()):
        source, value, rank = max(_config_candidates(context, field), key=lambda item: item[2])
        sensitive = _is_sensitive(field, option_sensitive=bool(option_sensitive.get(field_id)))
        if sensitive and value is not None and not isinstance(value, SecretRef):
            value = SecretRef(str(value), source="literal", required=False)
        values[field_id] = value
        provenance.append(
            ConfigProvenance(
                field_id=field_id,
                path=field.path,
                source=source,
                rank=rank,
                sensitive=sensitive,
                value_digest=_sha256(_safe_value(value)),
            )
        )
    semantic_payload = {
        "values": {key: _safe_value(value) for key, value in sorted(values.items())},
        "provenance": [item.to_dict() for item in provenance],
    }
    return ResolvedConfig(values=values, provenance=tuple(provenance), semantic_digest=_sha256(semantic_payload))


def private_run_root(repo_root: str | Path) -> Path:
    return Path(repo_root).expanduser().resolve(strict=False) / ".devtool" / "private" / "run-v3"


def build_run_envelope(context: ResolutionContext, registry: CoreRegistry | None = None) -> RunEnvelope:
    registry = registry or build_core_registry()
    invocation = registry.envelope_for(context.argv, source=context.invocation_source)
    scope = CliProjectScope.from_repo(context.repo_root)
    resolved = resolve_effective_config(context, registry)
    descriptor_payload = {
        "command_id": invocation.command_id,
        "command_digest": invocation.digest,
        "config_digest": resolved.semantic_digest,
        "scope_digest": scope.scope_digest,
    }
    run_id = _sha256(descriptor_payload)[:32]
    descriptor = RunDescriptor(
        run_id=run_id,
        schema_version=3,
        command_id=invocation.command_id,
        command_digest=invocation.digest,
        config_digest=resolved.semantic_digest,
        scope_digest=scope.scope_digest,
        run_root=str(private_run_root(context.repo_root) / run_id),
        private=True,
    )
    return RunEnvelope(
        invocation=invocation,
        scope=scope,
        config=resolved,
        output=context.output,
        interaction=context.interaction,
        descriptor=descriptor,
    )


def runtime_payload(context: ResolutionContext, registry: CoreRegistry | None = None) -> dict[str, Any]:
    return build_run_envelope(context, registry).to_dict()


def render_runtime_text(payload: Mapping[str, Any]) -> str:
    descriptor = payload.get("descriptor", {}) if isinstance(payload.get("descriptor"), Mapping) else {}
    invocation = payload.get("invocation", {}) if isinstance(payload.get("invocation"), Mapping) else {}
    return "\n".join(
        [
            f"Core runtime envelope schema v{payload.get('schema_version')}",
            f"command: {invocation.get('command_id')}",
            f"run: {descriptor.get('run_id')}",
            f"private root: {descriptor.get('run_root')}",
        ]
    )
