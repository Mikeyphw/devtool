
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CliOptionSpec:
    """Canonical description for one Devtool CLI option.

    The stable_id is durable and intentionally distinct from the currently
    accepted spelling.  Spellings and aliases may move; stable IDs are used by
    wrappers, completions, docs, config links, and future generated frontends.
    """

    stable_id: str
    canonical: str
    aliases: tuple[str, ...] = ()
    scope: str = "global"
    value_kind: str = "flag"
    config_field_id: str | None = None
    sensitive: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def all_spellings(self) -> tuple[str, ...]:
        return (self.canonical, *self.aliases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_id": self.stable_id,
            "canonical": self.canonical,
            "aliases": list(self.aliases),
            "scope": self.scope,
            "value_kind": self.value_kind,
            "config_field_id": self.config_field_id,
            "sensitive": self.sensitive,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CliCommandSpec:
    """Canonical description for one command path."""

    stable_id: str
    canonical_path: tuple[str, ...]
    aliases: tuple[tuple[str, ...], ...] = ()
    options: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def all_paths(self) -> tuple[tuple[str, ...], ...]:
        return (self.canonical_path, *self.aliases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_id": self.stable_id,
            "canonical_path": list(self.canonical_path),
            "aliases": [list(alias) for alias in self.aliases],
            "options": list(self.options),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ConfigFieldSpec:
    field_id: str
    path: tuple[str, ...]
    value_kind: str
    default: Any = None
    choices: tuple[str, ...] = ()
    reserved_namespace: str | None = None
    sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "path": list(self.path),
            "value_kind": self.value_kind,
            "default": self.default,
            "choices": list(self.choices),
            "reserved_namespace": self.reserved_namespace,
            "sensitive": self.sensitive,
        }


@dataclass(frozen=True, slots=True)
class ConfigDiagnostic:
    path: tuple[str, ...]
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": ".".join(self.path), "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ConfigDocument:
    name: str
    fields: tuple[ConfigFieldSpec, ...]
    allow_unknown_top_level: tuple[str, ...] = ()

    @property
    def field_by_path(self) -> dict[tuple[str, ...], ConfigFieldSpec]:
        return {field.path: field for field in self.fields}

    def validate(self, data: Mapping[str, Any]) -> tuple[ConfigDiagnostic, ...]:
        diagnostics: list[ConfigDiagnostic] = []
        field_by_path = self.field_by_path
        allowed_exact = set(field_by_path)
        allowed_prefixes: set[tuple[str, ...]] = {()}
        for path in allowed_exact:
            for index in range(1, len(path)):
                allowed_prefixes.add(path[:index])
        allowed_prefixes.update((section,) for section in self.allow_unknown_top_level)

        def walk(value: Any, path: tuple[str, ...]) -> None:
            if path in field_by_path:
                _validate_field_value(field_by_path[path], value, diagnostics)
                return
            if isinstance(value, Mapping):
                if path and path not in allowed_prefixes and path not in allowed_exact:
                    diagnostics.append(ConfigDiagnostic(path, "unknown-key", "unknown config key or table"))
                    return
                for key, child in value.items():
                    walk(child, (*path, str(key)))
                return
            if path and path not in allowed_exact:
                diagnostics.append(ConfigDiagnostic(path, "unknown-key", "unknown config key"))

        walk(data, ())
        return tuple(diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": [field.to_dict() for field in self.fields],
            "allow_unknown_top_level": list(self.allow_unknown_top_level),
        }


@dataclass(frozen=True, slots=True)
class EnvFieldSpec:
    env_id: str
    name: str
    value_kind: str = "string"
    sensitive: bool = False
    protected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_id": self.env_id,
            "name": self.name,
            "value_kind": self.value_kind,
            "sensitive": self.sensitive,
            "protected": self.protected,
        }


class TrustedInvocationError(ValueError):
    pass


class _TrustedEnvelopeSeal:
    pass


@dataclass(frozen=True, slots=True, init=False)
class TrustedInvocationEnvelope:
    """Internal provenance envelope that cannot be constructed by spoofing env vars.

    The object intentionally hides its constructor behind `from_invocation` so
    callers must go through the canonical command registry and receive a digest
    bound to the canonical command ID, invoked path, canonical path, and source.
    """

    command_id: str
    canonical_path: tuple[str, ...]
    invoked_path: tuple[str, ...]
    source: str
    digest: str

    def __init__(self, *, command_id: str, canonical_path: Sequence[str], invoked_path: Sequence[str], source: str, digest: str, _seal: _TrustedEnvelopeSeal | None = None) -> None:
        if not isinstance(_seal, _TrustedEnvelopeSeal):
            raise TrustedInvocationError("TrustedInvocationEnvelope must be created through the command registry")
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "canonical_path", tuple(canonical_path))
        object.__setattr__(self, "invoked_path", tuple(invoked_path))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "digest", digest)

    @classmethod
    def from_invocation(cls, registry: "CoreRegistry", argv: Sequence[str], *, source: str) -> "TrustedInvocationEnvelope":
        resolution = registry.resolve_command(argv)
        payload = {
            "command_id": resolution["command"].stable_id,
            "canonical_path": list(resolution["command"].canonical_path),
            "invoked_path": list(resolution["invoked_path"]),
            "source": source,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return cls(
            command_id=payload["command_id"],
            canonical_path=payload["canonical_path"],
            invoked_path=payload["invoked_path"],
            source=source,
            digest=digest,
            _seal=_TrustedEnvelopeSeal(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "canonical_path": list(self.canonical_path),
            "invoked_path": list(self.invoked_path),
            "source": self.source,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class CoreRegistry:
    schema_version: int
    commands: tuple[CliCommandSpec, ...]
    options: tuple[CliOptionSpec, ...]
    config_documents: tuple[ConfigDocument, ...]
    env: tuple[EnvFieldSpec, ...]
    reserved_namespaces: tuple[str, ...]

    @property
    def commands_by_id(self) -> dict[str, CliCommandSpec]:
        return {command.stable_id: command for command in self.commands}

    @property
    def options_by_id(self) -> dict[str, CliOptionSpec]:
        return {option.stable_id: option for option in self.options}

    @property
    def config_by_name(self) -> dict[str, ConfigDocument]:
        return {doc.name: doc for doc in self.config_documents}

    def resolve_command(self, argv: Sequence[str]) -> dict[str, Any]:
        tokens = tuple(str(token) for token in argv if str(token))
        best: tuple[int, CliCommandSpec, tuple[str, ...]] | None = None
        for command in self.commands:
            for path in command.all_paths():
                if tokens[: len(path)] == path:
                    score = len(path)
                    if best is None or score > best[0] or (score == best[0] and path == command.canonical_path):
                        best = (score, command, path)
        if best is None:
            raise KeyError(f"unknown command path: {' '.join(tokens)}")
        _, command, invoked = best
        return {
            "command": command,
            "invoked_path": invoked,
            "canonical_path": command.canonical_path,
            "remaining": tokens[len(invoked) :],
        }

    def canonicalize_argv(self, argv: Sequence[str]) -> tuple[str, ...]:
        resolution = self.resolve_command(argv)
        return (*resolution["canonical_path"], *resolution["remaining"])

    def validate_config_document(self, name: str, data: Mapping[str, Any]) -> tuple[ConfigDiagnostic, ...]:
        try:
            document = self.config_by_name[name]
        except KeyError as exc:
            raise KeyError(f"unknown config document: {name}") from exc
        return document.validate(data)

    def option_config_links(self) -> tuple[dict[str, str], ...]:
        links = []
        for option in self.options:
            if option.config_field_id:
                links.append({"option_id": option.stable_id, "config_field_id": option.config_field_id})
        return tuple(links)

    def parser_parity(self, parser: argparse.ArgumentParser) -> dict[str, Any]:
        parser_commands = _parser_command_paths(parser)
        registry_paths = {" ".join(command.canonical_path) for command in self.commands}
        registry_aliases = {" ".join(alias) for command in self.commands for alias in command.aliases}
        covered = sorted(path for path in parser_commands if path in registry_paths or path in registry_aliases)
        missing = sorted(path for path in parser_commands if path not in registry_paths and path not in registry_aliases)
        return {
            "parser_command_count": len(parser_commands),
            "registry_command_count": len(registry_paths),
            "covered": covered,
            "missing": missing,
            "missing_count": len(missing),
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "digest": getattr(self, "_digest_placeholder", None) or "",
            "commands": [command.to_dict() for command in self.commands],
            "options": [option.to_dict() for option in self.options],
            "config_documents": [doc.to_dict() for doc in self.config_documents],
            "env": [item.to_dict() for item in self.env],
            "reserved_namespaces": list(self.reserved_namespaces),
            "option_config_links": list(self.option_config_links()),
        }

    def envelope_for(self, argv: Sequence[str], *, source: str) -> TrustedInvocationEnvelope:
        return TrustedInvocationEnvelope.from_invocation(self, argv, source=source)


def _validate_field_value(field: ConfigFieldSpec, value: Any, diagnostics: list[ConfigDiagnostic]) -> None:
    ok = True
    if field.value_kind == "bool":
        ok = isinstance(value, bool)
    elif field.value_kind == "string":
        ok = isinstance(value, str)
    elif field.value_kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif field.value_kind == "enum":
        ok = isinstance(value, str) and value in field.choices
    elif field.value_kind == "table":
        ok = isinstance(value, Mapping)
    elif field.value_kind == "list:string":
        ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
    if not ok:
        diagnostics.append(ConfigDiagnostic(field.path, "invalid-type", f"expected {field.value_kind}"))
        return
    if field.choices and isinstance(value, str) and value not in field.choices:
        diagnostics.append(ConfigDiagnostic(field.path, "invalid-value", f"expected one of {', '.join(field.choices)}"))


def _parser_command_paths(parser: argparse.ArgumentParser) -> set[str]:
    paths: set[str] = set()

    def visit(p: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> None:
        for action in p._actions:
            if not isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
                continue
            for name, sub in action.choices.items():
                path = (*prefix, str(name))
                paths.add(" ".join(path))
                visit(sub, path)

    visit(parser)
    return paths


def build_core_registry() -> CoreRegistry:
    options = (
        CliOptionSpec("cli.option.repo", "--repo", ("-r",), scope="global", value_kind="path", config_field_id="cfg.project.repo_root"),
        CliOptionSpec("cli.option.config", "--config", ("-c",), scope="global", value_kind="path", config_field_id="cfg.project.config_file"),
        CliOptionSpec("cli.option.target", "--target", (), scope="workspace", value_kind="string", config_field_id="cfg.workspace.target"),
        CliOptionSpec("cli.option.copy", "--copy", ("-p",), scope="output", value_kind="bool", config_field_id="cfg.cli.output.copy"),
        CliOptionSpec("cli.option.stdout_payload", "--stdout-payload", (), scope="output", value_kind="enum", config_field_id="cfg.cli.output.stdout_payload"),
        CliOptionSpec("cli.option.machine_file", "--machine-file", (), scope="output", value_kind="path", sensitive=True),
        CliOptionSpec("cli.option.internal_invocation_source", "--internal-invocation-source", (), scope="internal", value_kind="string", sensitive=True),
        CliOptionSpec("cli.option.internal_invocation_command", "--internal-invocation-command", (), scope="internal", value_kind="string", sensitive=True),
        CliOptionSpec("cli.option.internal_wrapper_schema", "--internal-wrapper-schema", (), scope="internal", value_kind="string", sensitive=True),
        CliOptionSpec("cli.option.yes", "--yes", ("-y",), scope="execution", value_kind="bool"),
        CliOptionSpec("cli.option.verbose", "--verbose", ("-v",), scope="execution", value_kind="bool"),
        CliOptionSpec("cli.option.execution_environment", "--execution-environment", (), scope="environment", value_kind="enum", config_field_id="cfg.cli.environment.execution_environment"),
    )
    commands = (
        CliCommandSpec("cli.command.apply.overlay", ("apply", "overlay"), aliases=(("apply-overlay",),), options=("cli.option.repo", "cli.option.yes"), metadata={"domain": "artifact"}),
        CliCommandSpec("cli.command.apply.snapshot", ("apply", "snapshot"), aliases=(("apply-snapshot",),), metadata={"domain": "artifact"}),
        CliCommandSpec("cli.command.apply.bundle", ("apply", "bundle"), aliases=(("apply-overlays",),), metadata={"domain": "artifact"}),
        CliCommandSpec("cli.command.validate", ("validate",), aliases=(("v",),), metadata={"domain": "validation"}),
        CliCommandSpec("cli.command.config.doctor", ("config", "doctor"), metadata={"domain": "config"}),
        CliCommandSpec("cli.command.config.resolve", ("config", "resolve"), metadata={"domain": "config"}),
        CliCommandSpec("cli.command.wrapper.resolve", ("wrapper", "resolve"), aliases=(("wrapper", "plan"),), metadata={"domain": "wrapper"}),
        CliCommandSpec("cli.command.wrapper.schema", ("wrapper", "schema"), metadata={"domain": "wrapper"}),
        CliCommandSpec("cli.command.pytest.inventory", ("pytest", "inventory"), metadata={"domain": "test"}),
        CliCommandSpec("cli.command.pytest.select", ("pytest", "select"), metadata={"domain": "test"}),
        CliCommandSpec("cli.command.core.registry", ("core", "registry"), aliases=(("registry",),), metadata={"domain": "core"}),
        CliCommandSpec("cli.command.core.runtime", ("core", "runtime"), metadata={"domain": "core", "run_descriptor": True}),
    )
    repo_config = ConfigDocument(
        "repo",
        fields=(
            ConfigFieldSpec("cfg.project.kind", ("project", "kind"), "enum", choices=("auto", "dotnet", "android", "gradle", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "command", "unknown")),
            ConfigFieldSpec("cfg.project.runner", ("project", "runner"), "enum", choices=("auto", "dotnet", "android", "gradle", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "command", "unknown")),
            ConfigFieldSpec("cfg.workspace.targets", ("targets"), "table"),
            ConfigFieldSpec("cfg.wrapper.schema_version", ("wrapper", "schema_version"), "int"),
            ConfigFieldSpec("cfg.wrapper.commands", ("wrapper", "commands"), "table"),
            ConfigFieldSpec("cfg.federation.repositories", ("federation", "repositories"), "table"),
            ConfigFieldSpec("cfg.ui.profile", ("ui", "profile"), "string"),
        ),
        allow_unknown_top_level=("jobs", "tasks", "artifacts", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "test_profiles", "variants", "ssh"),
    )
    global_config = ConfigDocument(
        "global",
        fields=(
            ConfigFieldSpec("cfg.cli.output.copy", ("cli", "output", "copy"), "bool"),
            ConfigFieldSpec("cfg.cli.output.stdout_payload", ("cli", "output", "stdout_payload"), "enum", choices=("auto", "summary", "raw")),
            ConfigFieldSpec("cfg.cli.environment.execution_environment", ("cli", "environment", "execution_environment"), "enum", choices=("auto", "native-termux", "chroot", "proot-distro", "desktop-linux", "ssh", "android-device", "external-device")),
            ConfigFieldSpec("cfg.cli.secrets.token", ("cli", "secrets", "token"), "string", sensitive=True),
            ConfigFieldSpec("cfg.environment.default_chroot", ("environment", "default_chroot"), "string"),
            ConfigFieldSpec("cfg.ui.profile", ("ui", "profile"), "string"),
        ),
        allow_unknown_top_level=("termux_chroot", "termux", "proot", "android", "gradle", "dotnet", "terminal", "bootstrap", "chroots", "features", "ssh"),
    )
    env = (
        EnvFieldSpec("env.devtool.repo_root", "DEVTOOL_REPO_ROOT", protected=True),
        EnvFieldSpec("env.devtool.transaction_root", "DEVTOOL_TRANSACTION_ROOT", protected=True),
        EnvFieldSpec("env.devtool.validation_state_repo_root", "DEVTOOL_VALIDATION_STATE_REPO_ROOT", protected=True),
        EnvFieldSpec("env.devtool.internal_invocation_source", "DEVTOOL_INTERNAL_INVOCATION_SOURCE", sensitive=True, protected=True),
    )
    return CoreRegistry(
        schema_version=1,
        commands=commands,
        options=options,
        config_documents=(repo_config, global_config),
        env=env,
        reserved_namespaces=("devtool", "devtool.internal", "devtool.transaction", "devtool.validation"),
    )


def core_registry_payload(*, include_parser_parity: bool = False, parser: argparse.ArgumentParser | None = None) -> dict[str, Any]:
    registry = build_core_registry()
    payload = registry.to_dict()
    payload["digest"] = registry.digest
    if include_parser_parity and parser is not None:
        payload["parser_parity"] = registry.parser_parity(parser)
    return payload


def render_core_registry_text(payload: Mapping[str, Any]) -> str:
    lines = [f"Core registry schema v{payload.get('schema_version')} digest {payload.get('digest')}"]
    lines.append(f"commands: {len(payload.get('commands', []))}")
    lines.append(f"options: {len(payload.get('options', []))}")
    lines.append(f"config documents: {len(payload.get('config_documents', []))}")
    if "parser_parity" in payload:
        parity = payload["parser_parity"]
        lines.append(f"parser parity: covered={len(parity.get('covered', []))} missing={parity.get('missing_count', 0)}")
    return "\n".join(lines)
