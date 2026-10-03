
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from functools import lru_cache


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
        declared_commands = registry_paths | registry_aliases
        missing = sorted(declared_commands - parser_commands)
        extra = sorted(parser_commands - declared_commands)

        parser_options: set[tuple[tuple[str, ...], str]] = set()
        seen: set[int] = set()
        canonical_for_parser: dict[int, tuple[str, ...]] = {}
        for path, child in _parser_command_entries(parser):
            command = next((c for c in self.commands if path in c.all_paths()), None)
            if command is not None:
                canonical_for_parser[id(child)] = command.canonical_path
        canonical_for_parser[id(parser)] = ()
        def visit(current: argparse.ArgumentParser) -> None:
            if id(current) in seen:
                return
            seen.add(id(current))
            path = canonical_for_parser.get(id(current), ())
            for action in current._actions:
                if isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
                    for child in {id(item): item for item in action.choices.values()}.values():
                        visit(child)
                    continue
                for spelling in getattr(action, "option_strings", ()) or ():
                    parser_options.add((path, str(spelling)))
        visit(parser)
        registry_options = {
            (tuple(str(x) for x in (option.metadata.get("path") or ())), spelling)
            for option in self.options
            for spelling in option.all_spellings()
        }
        missing_options = sorted(" ".join((*path, spelling)) for path, spelling in registry_options - parser_options)
        extra_options = sorted(" ".join((*path, spelling)) for path, spelling in parser_options - registry_options)
        return {
            "parser_command_count": len(parser_commands),
            "registry_command_count": len(registry_paths),
            "covered": sorted(parser_commands & declared_commands),
            "missing": missing,
            "extra": extra,
            "missing_count": len(missing),
            "extra_count": len(extra),
            "missing_options": missing_options,
            "extra_options": extra_options,
            "option_missing_count": len(missing_options),
            "option_extra_count": len(extra_options),
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



def _parser_command_entries(parser: argparse.ArgumentParser) -> tuple[tuple[tuple[str, ...], argparse.ArgumentParser], ...]:
    """Return every argparse command spelling in deterministic traversal order.

    Aliases intentionally appear as separate paths here.  ``adopt_parser_registry``
    groups paths that point at the same parser object so one semantic command may
    retain many accepted spellings without multiplying command identities.
    """
    entries: list[tuple[tuple[str, ...], argparse.ArgumentParser]] = []

    def visit(current: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> None:
        for action in current._actions:
            if not isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
                continue
            for name, child in action.choices.items():
                path = (*prefix, str(name))
                entries.append((path, child))
                visit(child, path)

    visit(parser)
    return tuple(entries)


def _stable_command_id(path: Sequence[str]) -> str:
    parts = []
    for token in path:
        normalized = "".join(ch if ch.isalnum() else "_" for ch in str(token).lower()).strip("_") or "command"
        parts.append(normalized)
    return "cli.command." + ".".join(parts)


@lru_cache(maxsize=1)
def _canonical_cli_schema() -> Mapping[str, Any]:
    """Return the single checked-in CLI authority used by parser + registry.

    Importing through :mod:`core_parser` is intentional: the identity rows and
    parser lowering are two projections of one schema document, not separate
    authorities that can silently drift.
    """
    from .core_parser import canonical_cli_schema
    return canonical_cli_schema()


def _assert_schema_identity_binding() -> None:
    """Fail closed when registry identity rows diverge from parser lowering."""
    from .core_parser import parser_identity_projection

    schema = _canonical_cli_schema()
    projection = parser_identity_projection()
    parser_commands = {tuple(row) for row in projection["commands"]}
    declared_commands: set[tuple[str, ...]] = set()
    for row in schema.get("commands", ()):
        if not isinstance(row, Mapping):
            continue
        canonical = tuple(str(x) for x in row.get("canonical_path", ()) if str(x))
        if canonical:
            declared_commands.add(canonical)
        for alias in row.get("aliases", ()):
            if isinstance(alias, list):
                path = tuple(str(x) for x in alias if str(x))
                if path:
                    declared_commands.add(path)
    parser_options = {(tuple(path), spelling) for path, spelling in projection["options"]}
    declared_options: set[tuple[tuple[str, ...], str]] = set()
    for row in schema.get("options", ()):
        if not isinstance(row, Mapping):
            continue
        path = tuple(str(x) for x in (row.get("metadata") or {}).get("path", ()) if str(x))
        for spelling in (str(row.get("canonical") or ""), *(str(x) for x in row.get("aliases", ()) if str(x))):
            if spelling:
                declared_options.add((path, spelling))
    if declared_commands != parser_commands or declared_options != parser_options:
        missing_commands = sorted(" ".join(x) for x in declared_commands - parser_commands)
        extra_commands = sorted(" ".join(x) for x in parser_commands - declared_commands)
        missing_options = sorted(" ".join((*path, spelling)) for path, spelling in declared_options - parser_options)
        extra_options = sorted(" ".join((*path, spelling)) for path, spelling in parser_options - declared_options)
        raise RuntimeError(
            "canonical CLI schema identity rows diverged from parser lowering: "
            + json.dumps({
                "missing_commands": missing_commands, "extra_commands": extra_commands,
                "missing_options": missing_options, "extra_options": extra_options,
            }, sort_keys=True)
        )


def _schema_commands() -> tuple[CliCommandSpec, ...]:
    rows: list[CliCommandSpec] = []
    for raw in _canonical_cli_schema().get("commands", ()):
        if not isinstance(raw, Mapping):
            continue
        rows.append(CliCommandSpec(
            stable_id=str(raw.get("stable_id") or ""),
            canonical_path=tuple(str(x) for x in raw.get("canonical_path", ()) if str(x)),
            aliases=tuple(tuple(str(x) for x in alias if str(x)) for alias in raw.get("aliases", ()) if isinstance(alias, list)),
            options=tuple(str(x) for x in raw.get("options", ()) if str(x)),
            metadata=dict(raw.get("metadata") or {}),
        ))
    if not rows or any(not row.stable_id or not row.canonical_path for row in rows):
        raise RuntimeError("canonical CLI schema contains invalid command rows")
    return tuple(rows)


def _schema_options() -> tuple[CliOptionSpec, ...]:
    rows: list[CliOptionSpec] = []
    for raw in _canonical_cli_schema().get("options", ()):
        if not isinstance(raw, Mapping):
            continue
        rows.append(CliOptionSpec(
            stable_id=str(raw.get("stable_id") or ""),
            canonical=str(raw.get("canonical") or ""),
            aliases=tuple(str(x) for x in raw.get("aliases", ()) if str(x)),
            scope=str(raw.get("scope") or "global"),
            value_kind=str(raw.get("value_kind") or "flag"),
            config_field_id=str(raw.get("config_field_id") or "") or None,
            sensitive=bool(raw.get("sensitive")),
            metadata=dict(raw.get("metadata") or {}),
        ))
    if not rows or any(not row.stable_id or not row.canonical for row in rows):
        raise RuntimeError("canonical CLI schema contains invalid option rows")
    return tuple(rows)


def assert_parser_matches_canonical_registry(parser: argparse.ArgumentParser, registry: CoreRegistry | None = None) -> None:
    """Fail closed if argparse drifts from the checked-in canonical schema."""
    authority = registry or build_core_registry()
    parity = authority.parser_parity(parser)
    if parity.get("missing") or parity.get("extra") or parity.get("missing_options") or parity.get("extra_options"):
        raise RuntimeError(
            "argparse surface drifted from canonical CLI registry: "
            + json.dumps({k: parity.get(k) for k in ("missing", "extra", "missing_options", "extra_options")}, sort_keys=True)
        )


def adopt_parser_registry(base: CoreRegistry, parser: argparse.ArgumentParser) -> CoreRegistry:
    """Compatibility spelling for the retired reverse-adoption boundary.

    The parser may no longer contribute command/option identities.  Existing
    callers receive the canonical registry only after strict parity succeeds.
    """
    assert_parser_matches_canonical_registry(parser, base)
    return base

def build_adopted_core_registry(parser: argparse.ArgumentParser) -> CoreRegistry:
    """Compatibility alias: validate parser parity and return canonical authority."""
    registry = build_core_registry()
    assert_parser_matches_canonical_registry(parser, registry)
    return registry

def build_core_registry() -> CoreRegistry:
    _assert_schema_identity_binding()
    options = _schema_options()
    commands = _schema_commands()
    repo_config = ConfigDocument(
        "repo",
        fields=(
            ConfigFieldSpec("cfg.project.kind", ("project", "kind"), "enum", choices=("auto", "dotnet", "android", "gradle", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "command", "unknown")),
            ConfigFieldSpec("cfg.project.runner", ("project", "runner"), "enum", choices=("auto", "dotnet", "android", "gradle", "python", "node", "rust", "go", "maven", "cmake", "meson", "make", "command", "unknown")),
            ConfigFieldSpec("cfg.workspace.targets", ("targets"), "table"),
            ConfigFieldSpec("cfg.wrapper.schema_version", ("wrapper", "schema"), "int"),
            ConfigFieldSpec("cfg.wrapper.commands", ("wrapper", "commands"), "table"),
            ConfigFieldSpec("cfg.federation.repositories", ("federation", "repositories"), "table"),
            ConfigFieldSpec("cfg.ui.profile", ("ui", "profile"), "string"),
            ConfigFieldSpec("cfg.trust.artifacts", ("trust", "artifacts"), "table"),
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
            ConfigFieldSpec("cfg.trust.artifacts", ("trust", "artifacts"), "table"),
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
