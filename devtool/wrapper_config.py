from __future__ import annotations

"""Repository-owned wrapper configuration contracts.

WRAP-01 intentionally stops at configuration parsing and validation.  It does
not parse wrapper argv or execute commands; later WRAP overlays consume these
models to build a deterministic WrapperResolution.
"""

from dataclasses import asdict
import re
import string
from typing import Any, Mapping

from .models import (
    TargetConfig,
    WorkspaceConfig,
    WrapperCommandConfig,
    WrapperConfig,
    WrapperOptionConfig,
    WrapperPositionalConfig,
)
from .workspace import expand_workspace_group


class WrapperConfigError(ValueError):
    """Raised when the repository wrapper contract is invalid."""


WRAPPER_SCHEMA_VERSION = 1
WRAPPER_OPTION_TYPES = frozenset({"boolean", "string", "integer", "float", "choice", "path"})
WRAPPER_PATH_BASES = frozenset({"cwd", "repo"})
WRAPPER_RESERVED_OPTION_SPELLINGS = frozenset({"help", "h", "version"})
WRAPPER_RESERVED_COMMAND_SPELLINGS = frozenset({"help", "version"})
# Canonical public action commands.  Aliases are deliberately excluded: the
# repository contract should not depend on CLI shorthand.
WRAPPER_NATIVE_COMMANDS = frozenset({
    "restore",
    "build",
    "test",
    "lint",
    "validate",
    "wine-validate",
    "run",
    "package",
    "clean",
})

# Static binding safety.  The wrapper contract may bind repository-facing
# options to Devtool argv, but it must not let the repository redefine the
# routing controls that identify the repository, select the target scope, or
# carry internal provenance.  Values carried by ``{value}`` are checked at
# resolution time for text safety; static structural tokens are rejected here.
_WRAPPER_BINDING_COMMAND_TOKENS = WRAPPER_NATIVE_COMMANDS | frozenset({"invoke", "plan", "wrapper"})
_WRAPPER_GLOBAL_BINDING_FORBIDDEN = frozenset({"--repo", "-r", "--config"})
_WRAPPER_COMMAND_BINDING_FORBIDDEN = _WRAPPER_GLOBAL_BINDING_FORBIDDEN | frozenset({"--target", "--group", "--all-targets"})
_WRAPPER_PASSTHROUGH_BINDING_FORBIDDEN = frozenset({"--"})

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_OPTION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_ENV_UNSAFE_CHARS_RE = re.compile(r"[\x00\r\n]")
_FORMATTER = string.Formatter()
_MISSING = object()

_WRAPPER_KEYS = frozenset({"schema", "default_command", "commands"})
_COMMAND_KEYS = frozenset({
    "description", "devtool", "workflow", "aliases", "target", "group", "all_targets",
    "allow_passthrough", "options", "positionals",
})
_OPTION_KEYS = frozenset({
    "type", "description", "aliases", "required", "repeatable", "choices", "default",
    "conflicts", "requires", "relative_to", "global_argv", "command_argv", "passthrough_argv",
})
_POSITIONAL_KEYS = frozenset({
    "name", "type", "description", "required", "repeatable", "choices", "relative_to",
    "global_argv", "command_argv", "passthrough_argv",
})


def _reject_unknown(raw: Mapping[str, Any], allowed: frozenset[str], *, path: str) -> None:
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        raise _fail(f"{path} contains unknown field(s): {', '.join(unknown)}")



def _fail(message: str) -> WrapperConfigError:
    return WrapperConfigError(message)


def _table(value: object, *, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _fail(f"{path} must be a TOML table")
    return dict(value)


def _nonempty_name(value: object, *, path: str, option: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{path} must be a non-empty string")
    name = value.strip()
    pattern = _OPTION_NAME_RE if option else _NAME_RE
    if not pattern.fullmatch(name):
        allowed = "letters, numbers, '_' or '-'" if option else "letters, numbers, '.', '_' or '-'"
        raise _fail(f"Invalid {path} {name!r}. Use {allowed} and start with a letter/number")
    return name


def _string_list(value: object, *, path: str, option_names: bool = False) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _fail(f"{path} must be a TOML array of strings")
    out: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _nonempty_name(raw, path=f"{path}[{index}]", option=option_names)
        if item in seen:
            raise _fail(f"{path} contains duplicate value {item!r}")
        seen.add(item)
        out.append(item)
    return tuple(out)


def _validate_static_binding_token(token: str, *, path: str, lane: str) -> None:
    """Validate the static part of an argv binding for one binding lane."""

    if token == "--":
        raise _fail(f"{path} cannot bind a raw '--' separator")
    if token.startswith("--internal-"):
        raise _fail(f"{path} cannot bind internal Devtool provenance/control flag {token!r}")

    # Tokens containing {value} carry user input and are validated for text
    # safety by the resolver.  Do not classify a value token such as
    # ["--artifact", "{value}"] as an attempted structural control.
    if "{value}" in token:
        return

    if lane == "global" and token in _WRAPPER_GLOBAL_BINDING_FORBIDDEN:
        raise _fail(f"{path} cannot bind repository/global control token {token!r}")
    if lane == "command" and token in _WRAPPER_COMMAND_BINDING_FORBIDDEN:
        raise _fail(f"{path} cannot bind target/repository control token {token!r}; use wrapper command scope fields instead")
    if lane == "passthrough" and token in _WRAPPER_PASSTHROUGH_BINDING_FORBIDDEN:
        raise _fail(f"{path} cannot bind raw passthrough separator {token!r}")
    if lane in {"global", "command"} and token in _WRAPPER_BINDING_COMMAND_TOKENS:
        raise _fail(f"{path} cannot bind Devtool command token {token!r}")


def _argv_tokens(value: object, *, path: str, lane: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _fail(f"{path} must be a TOML array of argv tokens")
    out: list[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, str) or not raw:
            raise _fail(f"{path}[{index}] must be a non-empty string token")
        if _ENV_UNSAFE_CHARS_RE.search(raw):
            raise _fail(f"{path}[{index}] contains a NUL or newline and cannot be an argv token")
        try:
            parsed = list(_FORMATTER.parse(raw))
        except ValueError as exc:
            raise _fail(f"{path}[{index}] has an invalid template: {exc}") from exc
        for _literal, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if field_name != "value":
                raise _fail(f"{path}[{index}] uses unsupported template field {{{field_name}}}; only {{value}} is allowed")
            if format_spec or conversion:
                raise _fail(f"{path}[{index}] cannot use format specs or conversions in {{value}}")
        _validate_static_binding_token(raw, path=f"{path}[{index}]", lane=lane)
        out.append(raw)
    return tuple(out)


def _coerce_scalar(value: object, option_type: str, *, path: str, choices: tuple[str, ...]) -> object:
    if option_type == "boolean":
        if not isinstance(value, bool):
            raise _fail(f"{path} must be true or false")
        return value
    if option_type in {"string", "path", "choice"}:
        if not isinstance(value, str):
            raise _fail(f"{path} must be a string")
        if option_type == "choice" and value not in choices:
            raise _fail(f"{path}={value!r} is not one of: {', '.join(choices)}")
        return value
    if option_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _fail(f"{path} must be an integer")
        return value
    if option_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _fail(f"{path} must be a number")
        return float(value)
    raise _fail(f"{path} uses unsupported type {option_type!r}")


def _parse_value_contract(raw: Mapping[str, Any], *, path: str, positional: bool = False) -> dict[str, Any]:
    option_type = str(raw.get("type") or "string").strip().lower()
    if option_type not in WRAPPER_OPTION_TYPES:
        raise _fail(f"{path}.type must be one of: {', '.join(sorted(WRAPPER_OPTION_TYPES))}")

    choices_raw = raw.get("choices", [])
    if choices_raw is None:
        choices_raw = []
    if not isinstance(choices_raw, list) or any(not isinstance(item, str) for item in choices_raw):
        raise _fail(f"{path}.choices must be a TOML array of strings")
    choices = tuple(str(item) for item in choices_raw)
    if len(set(choices)) != len(choices):
        raise _fail(f"{path}.choices contains duplicate values")
    if option_type == "choice" and not choices:
        raise _fail(f"{path}.choices is required for type='choice'")
    if option_type != "choice" and choices:
        raise _fail(f"{path}.choices is only valid for type='choice'")

    repeatable = raw.get("repeatable", False)
    if not isinstance(repeatable, bool):
        raise _fail(f"{path}.repeatable must be true or false")
    required = raw.get("required", False)
    if not isinstance(required, bool):
        raise _fail(f"{path}.required must be true or false")

    relative_to = str(raw.get("relative_to") or "").strip().lower()
    if relative_to and option_type != "path":
        raise _fail(f"{path}.relative_to is only valid for type='path'")
    if option_type == "path" and not relative_to:
        raise _fail(f"{path}.relative_to is required for type='path' and must be one of: {', '.join(sorted(WRAPPER_PATH_BASES))}")
    if option_type == "path" and relative_to not in WRAPPER_PATH_BASES:
        raise _fail(f"{path}.relative_to must be one of: {', '.join(sorted(WRAPPER_PATH_BASES))}")

    default = raw.get("default", _MISSING)
    has_default = default is not _MISSING
    if has_default:
        if repeatable:
            if not isinstance(default, list):
                raise _fail(f"{path}.default must be an array when repeatable=true")
            default = tuple(
                _coerce_scalar(item, option_type, path=f"{path}.default[{index}]", choices=choices)
                for index, item in enumerate(default)
            )
        else:
            default = _coerce_scalar(default, option_type, path=f"{path}.default", choices=choices)
    else:
        default = None

    return {
        "type": option_type,
        "description": str(raw.get("description") or "").strip(),
        "required": required,
        "repeatable": repeatable,
        "choices": choices,
        "relative_to": relative_to,
        "default": default,
        "has_default": has_default,
        "global_argv": _argv_tokens(raw.get("global_argv"), path=f"{path}.global_argv", lane="global"),
        "command_argv": _argv_tokens(raw.get("command_argv"), path=f"{path}.command_argv", lane="command"),
        "passthrough_argv": _argv_tokens(raw.get("passthrough_argv"), path=f"{path}.passthrough_argv", lane="passthrough"),
    }


def _parse_option(name: str, raw_value: object, *, command: str) -> WrapperOptionConfig:
    path = f"wrapper.commands.{command}.options.{name}"
    raw = _table(raw_value, path=path)
    _reject_unknown(raw, _OPTION_KEYS, path=path)
    normalized_name = _nonempty_name(name, path=path, option=True)
    values = _parse_value_contract(raw, path=path)
    aliases = _string_list(raw.get("aliases"), path=f"{path}.aliases", option_names=True)
    reserved = sorted({normalized_name, *aliases} & WRAPPER_RESERVED_OPTION_SPELLINGS)
    if reserved:
        raise _fail(
            f"{path} uses reserved wrapper control spelling(s): {', '.join(reserved)}"
        )
    conflicts = _string_list(raw.get("conflicts"), path=f"{path}.conflicts", option_names=True)
    requires = _string_list(raw.get("requires"), path=f"{path}.requires", option_names=True)
    return WrapperOptionConfig(
        name=normalized_name,
        aliases=aliases,
        conflicts=conflicts,
        requires=requires,
        **values,
    )


def _parse_positionals(raw_value: object, *, command: str) -> tuple[WrapperPositionalConfig, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, list):
        raise _fail(f"wrapper.commands.{command}.positionals must be an array of tables")
    out: list[WrapperPositionalConfig] = []
    seen: set[str] = set()
    optional_seen = False
    repeatable_seen = False
    for index, item in enumerate(raw_value):
        path = f"wrapper.commands.{command}.positionals[{index}]"
        raw = _table(item, path=path)
        _reject_unknown(raw, _POSITIONAL_KEYS, path=path)
        name = _nonempty_name(raw.get("name"), path=f"{path}.name", option=True)
        if name in seen:
            raise _fail(f"wrapper command {command!r} declares duplicate positional {name!r}")
        seen.add(name)
        values = _parse_value_contract(raw, path=path, positional=True)
        if values["has_default"]:
            raise _fail(f"{path}.default is not supported for positional arguments in schema 1")
        required = bool(values["required"])
        repeatable = bool(values["repeatable"])
        if optional_seen and required:
            raise _fail(f"{path} cannot be required after an optional positional")
        optional_seen = optional_seen or not required
        if repeatable_seen:
            raise _fail(f"{path} cannot follow a repeatable positional")
        if repeatable and index != len(raw_value) - 1:
            raise _fail(f"{path} must be the final positional when repeatable=true")
        repeatable_seen = repeatable_seen or repeatable
        values.pop("default")
        values.pop("has_default")
        out.append(WrapperPositionalConfig(name=name, **values))
    return tuple(out)


def _validate_option_links(command: WrapperCommandConfig) -> None:
    names = set(command.options)
    for option in command.options.values():
        for relation, values in (("conflicts", option.conflicts), ("requires", option.requires)):
            for target in values:
                if target == option.name:
                    raise _fail(f"wrapper option {command.name}.{option.name} cannot {relation} itself")
                if target not in names:
                    raise _fail(
                        f"wrapper option {command.name}.{option.name} references unknown {relation} option {target!r}"
                    )


def _parse_command(
    name: str,
    raw_value: object,
    *,
    targets: Mapping[str, TargetConfig],
    workspace: WorkspaceConfig,
) -> WrapperCommandConfig:
    path = f"wrapper.commands.{name}"
    raw = _table(raw_value, path=path)
    _reject_unknown(raw, _COMMAND_KEYS, path=path)
    command_name = _nonempty_name(name, path=path)
    if command_name in WRAPPER_RESERVED_COMMAND_SPELLINGS:
        raise _fail(f"{path} uses reserved wrapper command spelling {command_name!r}")
    devtool = str(raw.get("devtool") or "").strip()
    workflow = str(raw.get("workflow") or "").strip()
    if bool(devtool) == bool(workflow):
        raise _fail(f"{path} must declare exactly one of 'devtool' or 'workflow'")
    if devtool and devtool not in WRAPPER_NATIVE_COMMANDS:
        raise _fail(
            f"{path}.devtool={devtool!r} is not a supported canonical wrapper command; "
            f"choose one of: {', '.join(sorted(WRAPPER_NATIVE_COMMANDS))}"
        )
    if workflow:
        _nonempty_name(workflow, path=f"{path}.workflow")

    target = str(raw.get("target") or "").strip()
    group = str(raw.get("group") or "").strip()
    all_targets = raw.get("all_targets", False)
    if not isinstance(all_targets, bool):
        raise _fail(f"{path}.all_targets must be true or false")
    selectors = int(bool(target)) + int(bool(group)) + int(all_targets)
    if selectors > 1:
        raise _fail(f"{path} may declare only one of target, group, or all_targets=true")
    if target and target not in targets:
        available = ", ".join(sorted(targets)) or "<none>"
        raise _fail(f"{path}.target references unknown target {target!r}. Available targets: {available}")
    if group and group not in workspace.groups:
        available = ", ".join(sorted(workspace.groups)) or "<none>"
        raise _fail(f"{path}.group references unknown workspace group {group!r}. Available groups: {available}")

    if workflow:
        if target:
            workflow_targets = (target,)
        elif group:
            workflow_targets = expand_workspace_group(workspace, targets, group)
        elif all_targets:
            workflow_targets = tuple(targets)
        elif workspace.default_target:
            workflow_targets = (workspace.default_target,)
        elif len(targets) == 1:
            workflow_targets = tuple(targets)
        else:
            available = ", ".join(sorted(targets)) or "<none>"
            raise _fail(
                f"{path}.workflow requires a deterministic target scope; set target/group/all_targets or "
                f"[workspace].default_target. Available targets: {available}"
            )
        if not workflow_targets:
            raise _fail(f"{path}.workflow cannot resolve because the repository has no configured targets")
        missing = [target_name for target_name in workflow_targets if workflow not in targets[target_name].workflows]
        if missing:
            raise _fail(
                f"{path}.workflow={workflow!r} is not declared by target(s): {', '.join(missing)}"
            )

    aliases = _string_list(raw.get("aliases"), path=f"{path}.aliases")
    reserved_aliases = sorted(set(aliases) & WRAPPER_RESERVED_COMMAND_SPELLINGS)
    if reserved_aliases:
        raise _fail(f"{path}.aliases uses reserved wrapper command spelling(s): {', '.join(reserved_aliases)}")
    options_raw = _table(raw.get("options"), path=f"{path}.options")
    options: dict[str, WrapperOptionConfig] = {}
    option_spellings: dict[str, str] = {}
    for raw_option_name, raw_option in options_raw.items():
        if not isinstance(raw_option_name, str):
            raise _fail(f"{path}.options keys must be strings")
        option = _parse_option(raw_option_name, raw_option, command=command_name)
        for spelling in (option.name, *option.aliases):
            owner = option_spellings.get(spelling)
            if owner is not None:
                raise _fail(
                    f"wrapper command {command_name!r} option spelling {spelling!r} collides between {owner!r} and {option.name!r}"
                )
            option_spellings[spelling] = option.name
        options[option.name] = option

    command = WrapperCommandConfig(
        name=command_name,
        description=str(raw.get("description") or "").strip(),
        devtool=devtool,
        workflow=workflow,
        aliases=aliases,
        target=target,
        group=group,
        all_targets=all_targets,
        allow_passthrough=raw.get("allow_passthrough", False),
        options=options,
        positionals=_parse_positionals(raw.get("positionals"), command=command_name),
    )
    if not isinstance(command.allow_passthrough, bool):
        raise _fail(f"{path}.allow_passthrough must be true or false")
    if workflow and command.allow_passthrough:
        raise _fail(f"{path}.allow_passthrough is not supported for workflow wrapper commands in schema 1")
    declared_passthrough = [
        option.name for option in command.options.values() if option.passthrough_argv
    ] + [
        positional.name for positional in command.positionals if positional.passthrough_argv
    ]
    if declared_passthrough and not command.allow_passthrough:
        raise _fail(
            f"{path} declares passthrough bindings for {', '.join(declared_passthrough)} but allow_passthrough is false"
        )
    _validate_option_links(command)
    return command


def load_wrapper_config(
    section: object,
    *,
    targets: Mapping[str, TargetConfig],
    workspace: WorkspaceConfig,
) -> WrapperConfig:
    """Parse and validate the repository-local ``[wrapper]`` contract."""

    if section is None:
        return WrapperConfig()
    raw = _table(section, path="[wrapper]")
    _reject_unknown(raw, _WRAPPER_KEYS, path="[wrapper]")
    if not raw:
        return WrapperConfig()

    schema = raw.get("schema", WRAPPER_SCHEMA_VERSION)
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != WRAPPER_SCHEMA_VERSION:
        raise _fail(f"wrapper.schema must be {WRAPPER_SCHEMA_VERSION}")

    commands_raw = _table(raw.get("commands"), path="[wrapper.commands]")
    commands: dict[str, WrapperCommandConfig] = {}
    command_spellings: dict[str, str] = {}
    for raw_name, raw_command in commands_raw.items():
        if not isinstance(raw_name, str):
            raise _fail("wrapper.commands keys must be strings")
        command = _parse_command(raw_name, raw_command, targets=targets, workspace=workspace)
        for spelling in (command.name, *command.aliases):
            owner = command_spellings.get(spelling)
            if owner is not None:
                raise _fail(
                    f"wrapper command spelling {spelling!r} collides between {owner!r} and {command.name!r}"
                )
            command_spellings[spelling] = command.name
        commands[command.name] = command

    default_command = str(raw.get("default_command") or "").strip()
    if default_command:
        if default_command not in commands:
            raise _fail(
                f"wrapper.default_command references unknown canonical command {default_command!r}; "
                "aliases are not allowed here"
            )

    return WrapperConfig(schema=schema, default_command=default_command, commands=commands)


def wrapper_as_dict(wrapper: WrapperConfig) -> dict[str, Any]:
    """Return deterministic machine-readable wrapper configuration."""

    return {
        "schema": wrapper.schema,
        "default_command": wrapper.default_command,
        "commands": {
            name: {
                "name": command.name,
                "description": command.description,
                "execution_kind": "native" if command.devtool else "workflow",
                "devtool": command.devtool,
                "workflow": command.workflow,
                "aliases": list(command.aliases),
                "target": command.target,
                "group": command.group,
                "all_targets": command.all_targets,
                "allow_passthrough": command.allow_passthrough,
                "options": {
                    option_name: {
                        key: (list(value) if isinstance(value, tuple) else value)
                        for key, value in asdict(option).items()
                    }
                    for option_name, option in sorted(command.options.items())
                },
                "positionals": [
                    {key: (list(value) if isinstance(value, tuple) else value) for key, value in asdict(item).items()}
                    for item in command.positionals
                ],
            }
            for name, command in sorted(wrapper.commands.items())
        },
    }
