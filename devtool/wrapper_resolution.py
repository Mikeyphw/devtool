from __future__ import annotations

"""Pure repository-wrapper argv resolution.

WRAP-03 intentionally performs no Devtool execution.  It parses a repository's
public wrapper argv into a deterministic :class:`WrapperResolution` that later
wrapper-runtime overlays can dispatch through the normal Devtool CLI path.
"""

from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import ProjectConfig, WrapperCommandConfig, WrapperOptionConfig, WrapperPositionalConfig
from .wrapper_config import wrapper_binding_forbidden_reason


class WrapperResolutionError(ValueError):
    """Raised when repository-facing wrapper argv cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class WrapperResolvedOption:
    name: str
    value: Any
    provided: bool = False
    defaulted: bool = False


@dataclass(frozen=True, slots=True)
class WrapperResolution:
    schema: int
    mode: str = "execute"  # execute | help | version
    command: str = ""
    command_spelling: str = ""
    execution_kind: str = ""
    devtool_command: str = ""
    workflow_action: str = ""
    target: str = ""
    group: str = ""
    all_targets: bool = False
    options: tuple[WrapperResolvedOption, ...] = ()
    positionals: tuple[tuple[str, Any], ...] = ()
    raw_passthrough_argv: tuple[str, ...] = ()
    sensitive_options: tuple[str, ...] = ()
    global_argv: tuple[str, ...] = ()
    command_argv: tuple[str, ...] = ()
    passthrough_argv: tuple[str, ...] = ()
    canonical_argv: tuple[str, ...] = ()
    redacted_canonical_argv: tuple[str, ...] = ()
    repo_root: str = ""
    launch_cwd: str = ""
    help_requested: bool = False
    version_requested: bool = False

    @property
    def option_values(self) -> dict[str, Any]:
        return {item.name: item.value for item in self.options}


_HELP_TOKENS = frozenset({"-h", "--help"})
_VERSION_TOKENS = frozenset({"--version"})
_ENV_UNSAFE_CHARS_REPLACEMENT = "<redacted>"
_SENSITIVE_NAME_FRAGMENTS = ("secret", "token", "password", "passphrase", "credential", "api_key", "apikey", "auth")


def _fail(message: str) -> WrapperResolutionError:
    return WrapperResolutionError(message)


def _option_long(name: str) -> str:
    return f"--{name}"


def _alias_token(alias: str) -> str:
    return f"-{alias}" if len(alias) == 1 else f"--{alias}"


def _command_lookup(config: ProjectConfig) -> dict[str, WrapperCommandConfig]:
    lookup: dict[str, WrapperCommandConfig] = {}
    for command in config.wrapper.commands.values():
        for spelling in (command.name, *command.aliases):
            lookup[spelling] = command
    return lookup


def _option_lookup(command: WrapperCommandConfig) -> dict[str, WrapperOptionConfig]:
    lookup: dict[str, WrapperOptionConfig] = {}
    for option in command.options.values():
        lookup[_option_long(option.name)] = option
        for alias in option.aliases:
            lookup[_alias_token(alias)] = option
    return lookup


def _suggest_unknown(value: str, candidates: Iterable[str], *, kind: str) -> WrapperResolutionError:
    ordered = sorted(set(candidates))
    match = get_close_matches(value, ordered, n=1, cutoff=0.55)
    suffix = f" Did you mean {match[0]!r}?" if match else ""
    return _fail(f"Unknown wrapper {kind} {value!r}.{suffix}")


def _parse_bool_text(raw: str, *, path: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise _fail(f"{path} expects a boolean value (true/false)")


def _coerce_value(
    raw: object,
    *,
    value_type: str,
    choices: Sequence[str],
    relative_to: str,
    repo_root: Path,
    launch_cwd: Path,
    path: str,
) -> object:
    if value_type == "boolean":
        if isinstance(raw, bool):
            return raw
        if not isinstance(raw, str):
            raise _fail(f"{path} expects a boolean value")
        return _parse_bool_text(raw, path=path)

    if not isinstance(raw, str):
        raw = str(raw)

    if value_type == "string":
        return raw
    if value_type == "choice":
        if raw not in choices:
            raise _fail(f"{path}={raw!r} is not one of: {', '.join(choices)}")
        return raw
    if value_type == "integer":
        try:
            return int(raw, 10)
        except (TypeError, ValueError) as exc:
            raise _fail(f"{path} expects an integer value") from exc
    if value_type == "float":
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise _fail(f"{path} expects a number") from exc
    if value_type == "path":
        if relative_to not in {"cwd", "repo"}:
            # The config validator should already guarantee this.  Keep the
            # resolver fail-closed for directly constructed configs/tests.
            raise _fail(f"{path} has no explicit path base")
        parsed = Path(raw)
        if parsed.is_absolute():
            return str(parsed.resolve(strict=False))
        base = launch_cwd if relative_to == "cwd" else repo_root
        return str((base / parsed).resolve(strict=False))
    raise _fail(f"{path} uses unsupported type {value_type!r}")


def _value_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, "g")
    return str(value)


def _ensure_safe_value_text(value: object, *, path: str) -> None:
    text = _value_text(value)
    if "\x00" in text or "\n" in text or "\r" in text:
        raise _fail(f"{path} contains a NUL or newline and cannot be expanded into Devtool argv")


def _ensure_safe_raw_passthrough(tokens: Sequence[str]) -> None:
    for index, token in enumerate(tokens):
        if token == "--":
            raise _fail("Wrapper raw passthrough cannot contain an additional '--' separator")
        if "\x00" in token or "\n" in token or "\r" in token:
            raise _fail(f"Wrapper raw passthrough token at index {index} contains a NUL or newline")
        if token.startswith("--internal-"):
            raise _fail(f"Wrapper raw passthrough cannot contain internal Devtool control flag {token!r}")


def _looks_sensitive(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_NAME_FRAGMENTS)


def _collect_sensitive_texts(
    command: WrapperCommandConfig,
    values_by_name: Mapping[str, object],
    *,
    active_options: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    option_names: list[str] = []
    values: list[str] = []
    for option in command.options.values():
        if option.name not in active_options or not _looks_sensitive(option.name):
            continue
        option_names.append(option.name)
        for item in _iter_binding_values(values_by_name.get(option.name), repeatable=option.repeatable):
            if item is not None and not isinstance(item, bool):
                text = _value_text(item)
                if text:
                    values.append(text)
    return tuple(option_names), tuple(dict.fromkeys(values))


def _redact_argv(argv: Sequence[str], sensitive_values: Sequence[str]) -> tuple[str, ...]:
    redacted: list[str] = []
    ordered = sorted((value for value in sensitive_values if value), key=len, reverse=True)
    for token in argv:
        masked = token
        for value in ordered:
            if masked == value:
                masked = _ENV_UNSAFE_CHARS_REPLACEMENT
            elif value in masked:
                masked = masked.replace(value, _ENV_UNSAFE_CHARS_REPLACEMENT)
        redacted.append(masked)
    return tuple(redacted)


def _expand_tokens(tokens: Sequence[str], value: object, *, path: str, lane: str) -> tuple[str, ...]:
    _ensure_safe_value_text(value, path=path)
    replacement = _value_text(value)
    expanded = tuple(token.replace("{value}", replacement) for token in tokens)
    for token in expanded:
        reason = wrapper_binding_forbidden_reason(token, lane=lane)
        if not reason:
            continue
        if reason == "internal Devtool provenance/control flag":
            raise _fail(f"Wrapper binding {path} cannot expand to internal Devtool control token {token!r}")
        if reason in {"repository/global control", "target/repository control"}:
            raise _fail(f"Wrapper binding {path} cannot expand to routing control token {token!r}")
        if reason == "Devtool command token":
            raise _fail(f"Wrapper binding {path} cannot expand to Devtool command token {token!r}")
        raise _fail(f"Wrapper binding {path} cannot expand to raw '--' separator")
    return expanded


def _iter_binding_values(value: object, *, repeatable: bool) -> tuple[object, ...]:
    if repeatable:
        if value is None:
            return ()
        if isinstance(value, tuple):
            return tuple(value)
        if isinstance(value, list):
            return tuple(value)
        return (value,)
    return (value,)


def _is_active(option: WrapperOptionConfig, value: object, *, present: bool) -> bool:
    if not present:
        return False
    if option.type == "boolean":
        return bool(value)
    if option.repeatable:
        return bool(value)
    return True


def _parse_option_token(
    token: str,
    *,
    option_lookup: Mapping[str, WrapperOptionConfig],
) -> tuple[WrapperOptionConfig | None, str | None, str]:
    """Return (option, attached_value, spelling)."""

    if token.startswith("--"):
        spelling, sep, attached = token.partition("=")
        option = option_lookup.get(spelling)
        return option, attached if sep else None, spelling
    if token.startswith("-") and token != "-":
        spelling, sep, attached = token.partition("=")
        option = option_lookup.get(spelling)
        return option, attached if sep else None, spelling
    return None, None, ""


def _resolve_control_only(
    config: ProjectConfig,
    *,
    mode: str,
    repo_root: Path,
    launch_cwd: Path,
    command: WrapperCommandConfig | None = None,
    command_spelling: str = "",
) -> WrapperResolution:
    return WrapperResolution(
        schema=config.wrapper.schema,
        mode=mode,
        command=command.name if command is not None else "",
        command_spelling=command_spelling,
        execution_kind=("native" if command and command.devtool else "workflow" if command else ""),
        devtool_command=command.devtool if command is not None else "",
        workflow_action=command.workflow if command is not None else "",
        target=command.target if command is not None else "",
        group=command.group if command is not None else "",
        all_targets=bool(command.all_targets) if command is not None else False,
        repo_root=str(repo_root),
        launch_cwd=str(launch_cwd),
        help_requested=mode == "help",
        version_requested=mode == "version",
    )


def resolve_wrapper_argv(
    config: ProjectConfig,
    argv: Sequence[str],
    *,
    launch_cwd: str | Path | None = None,
) -> WrapperResolution:
    """Resolve repository wrapper argv into canonical Devtool argv without executing.

    ``canonical_argv`` intentionally omits the executable name itself.  It is
    suitable for passing back through ``devtool.cli.main()`` in WRAP-05.
    """

    repo_root = Path(config.repo_root).resolve(strict=False)
    cwd = Path(launch_cwd) if launch_cwd is not None else Path.cwd()
    cwd = cwd.resolve(strict=False)
    raw = [str(item) for item in argv]
    commands = _command_lookup(config)

    if raw and raw[0] in _HELP_TOKENS:
        return _resolve_control_only(config, mode="help", repo_root=repo_root, launch_cwd=cwd)
    if raw and raw[0] in _VERSION_TOKENS:
        return _resolve_control_only(config, mode="version", repo_root=repo_root, launch_cwd=cwd)

    command: WrapperCommandConfig | None = None
    command_spelling = ""
    remaining = list(raw)
    if remaining and remaining[0] in commands:
        command_spelling = remaining.pop(0)
        command = commands[command_spelling]
    elif remaining and not remaining[0].startswith("-"):
        raise _suggest_unknown(remaining[0], commands, kind="command")
    elif config.wrapper.default_command:
        command_spelling = config.wrapper.default_command
        command = config.wrapper.commands.get(command_spelling)
    elif not remaining:
        raise _fail("No wrapper command was provided and wrapper.default_command is not configured")
    else:
        raise _fail(
            f"Wrapper option {remaining[0]!r} requires an explicit command because wrapper.default_command is not configured"
        )

    if command is None:
        raise _fail(f"Wrapper command {command_spelling!r} is not configured")

    # Command-level controls short-circuit resolution deliberately.  WRAP-08
    # renders these requests; WRAP-03 only represents them structurally.
    before_separator = remaining[: remaining.index("--")] if "--" in remaining else remaining
    if any(token in _HELP_TOKENS for token in before_separator):
        return _resolve_control_only(
            config,
            mode="help",
            repo_root=repo_root,
            launch_cwd=cwd,
            command=command,
            command_spelling=command_spelling,
        )
    if any(token in _VERSION_TOKENS for token in before_separator):
        return _resolve_control_only(
            config,
            mode="version",
            repo_root=repo_root,
            launch_cwd=cwd,
            command=command,
            command_spelling=command_spelling,
        )

    raw_passthrough: tuple[str, ...] = ()
    if "--" in remaining:
        separator = remaining.index("--")
        raw_passthrough = tuple(remaining[separator + 1 :])
        remaining = remaining[:separator]
        if not command.allow_passthrough:
            raise _fail(f"Wrapper command {command.name!r} does not allow raw passthrough after '--'")
        if command.workflow:
            raise _fail(f"Workflow wrapper command {command.name!r} cannot accept raw passthrough")
        _ensure_safe_raw_passthrough(raw_passthrough)

    option_lookup = _option_lookup(command)
    parsed_values: dict[str, list[object]] = {}
    provided: set[str] = set()
    positional_tokens: list[str] = []

    index = 0
    while index < len(remaining):
        token = remaining[index]
        if token.startswith("-") and token != "-":
            option, attached, spelling = _parse_option_token(token, option_lookup=option_lookup)
            if option is None:
                raise _suggest_unknown(spelling or token, option_lookup, kind=f"option for {command.name}")
            provided.add(option.name)
            if option.type == "boolean":
                value = True if attached is None else _coerce_value(
                    attached,
                    value_type=option.type,
                    choices=option.choices,
                    relative_to=option.relative_to,
                    repo_root=repo_root,
                    launch_cwd=cwd,
                    path=f"--{option.name}",
                )
            else:
                if attached is None:
                    index += 1
                    if index >= len(remaining):
                        raise _fail(f"Wrapper option {spelling!r} requires a value")
                    attached = remaining[index]
                value = _coerce_value(
                    attached,
                    value_type=option.type,
                    choices=option.choices,
                    relative_to=option.relative_to,
                    repo_root=repo_root,
                    launch_cwd=cwd,
                    path=f"--{option.name}",
                )
            bucket = parsed_values.setdefault(option.name, [])
            if bucket and not option.repeatable:
                raise _fail(f"Wrapper option --{option.name} may only be provided once")
            bucket.append(value)
        else:
            positional_tokens.append(token)
        index += 1

    resolved_options: list[WrapperResolvedOption] = []
    active_options: set[str] = set()
    values_by_name: dict[str, object] = {}
    for option in command.options.values():
        raw_values = parsed_values.get(option.name)
        was_provided = option.name in provided
        defaulted = False
        present = was_provided
        if raw_values is not None:
            if option.repeatable:
                value: object = tuple(raw_values)
            else:
                value = raw_values[-1]
        elif option.has_default:
            if option.repeatable:
                value = tuple(
                    _coerce_value(
                        item,
                        value_type=option.type,
                        choices=option.choices,
                        relative_to=option.relative_to,
                        repo_root=repo_root,
                        launch_cwd=cwd,
                        path=f"--{option.name}",
                    )
                    for item in (option.default or ())
                )
            else:
                value = _coerce_value(
                    option.default,
                    value_type=option.type,
                    choices=option.choices,
                    relative_to=option.relative_to,
                    repo_root=repo_root,
                    launch_cwd=cwd,
                    path=f"--{option.name}",
                )
            defaulted = True
            present = True
        elif option.type == "boolean":
            value = False
        elif option.repeatable:
            value = ()
        else:
            value = None

        if option.required and not present:
            raise _fail(f"Wrapper command {command.name!r} requires option --{option.name}")
        values_by_name[option.name] = value
        if _is_active(option, value, present=present):
            active_options.add(option.name)
        resolved_options.append(
            WrapperResolvedOption(
                name=option.name,
                value=value,
                provided=was_provided,
                defaulted=defaulted,
            )
        )

    for option in command.options.values():
        if option.name not in active_options:
            continue
        for conflict in option.conflicts:
            if conflict in active_options:
                raise _fail(
                    f"Wrapper options --{option.name} and --{conflict} conflict for command {command.name!r}"
                )
        for requirement in option.requires:
            if requirement not in active_options:
                raise _fail(
                    f"Wrapper option --{option.name} requires --{requirement} for command {command.name!r}"
                )

    resolved_positionals: list[tuple[str, object]] = []
    cursor = 0
    for positional in command.positionals:
        if positional.repeatable:
            tokens = positional_tokens[cursor:]
            cursor = len(positional_tokens)
            if positional.required and not tokens:
                raise _fail(f"Wrapper command {command.name!r} requires positional {positional.name!r}")
            values = tuple(
                _coerce_value(
                    token,
                    value_type=positional.type,
                    choices=positional.choices,
                    relative_to=positional.relative_to,
                    repo_root=repo_root,
                    launch_cwd=cwd,
                    path=positional.name,
                )
                for token in tokens
            )
            resolved_positionals.append((positional.name, values))
            continue

        if cursor >= len(positional_tokens):
            if positional.required:
                raise _fail(f"Wrapper command {command.name!r} requires positional {positional.name!r}")
            resolved_positionals.append((positional.name, None))
            continue
        value = _coerce_value(
            positional_tokens[cursor],
            value_type=positional.type,
            choices=positional.choices,
            relative_to=positional.relative_to,
            repo_root=repo_root,
            launch_cwd=cwd,
            path=positional.name,
        )
        cursor += 1
        resolved_positionals.append((positional.name, value))

    if cursor < len(positional_tokens):
        extra = positional_tokens[cursor]
        raise _fail(f"Unexpected wrapper positional argument {extra!r} for command {command.name!r}")

    global_bindings: list[str] = []
    command_bindings: list[str] = []
    passthrough_bindings: list[str] = []

    for option in command.options.values():
        value = values_by_name[option.name]
        present = option.name in provided or option.has_default
        if not _is_active(option, value, present=present):
            continue
        for item in _iter_binding_values(value, repeatable=option.repeatable):
            global_bindings.extend(_expand_tokens(option.global_argv, item, path=f"--{option.name}", lane="global"))
            command_bindings.extend(_expand_tokens(option.command_argv, item, path=f"--{option.name}", lane="command"))
            passthrough_bindings.extend(_expand_tokens(option.passthrough_argv, item, path=f"--{option.name}", lane="passthrough"))

    positional_map = dict(resolved_positionals)
    for positional in command.positionals:
        value = positional_map.get(positional.name)
        if value is None:
            continue
        values = tuple(value) if positional.repeatable and isinstance(value, tuple) else (value,)
        for item in values:
            global_bindings.extend(_expand_tokens(positional.global_argv, item, path=positional.name, lane="global"))
            command_bindings.extend(_expand_tokens(positional.command_argv, item, path=positional.name, lane="command"))
            passthrough_bindings.extend(_expand_tokens(positional.passthrough_argv, item, path=positional.name, lane="passthrough"))

    global_argv = ["--repo", str(repo_root), *global_bindings]
    if command.devtool:
        command_token = command.devtool
        fixed_args: list[str] = []
        if command.devtool=='actions':
            spec=command.actions
            op=str(spec['operation'])
            fixed_args.append('run' if op=='dispatch' else op)
            if op in {'plan','dispatch'}:
                fixed_args.extend([str(spec['workflow_id']),'--ref',str(spec['ref'])])
                if op=='dispatch':fixed_args.append('--resume-if-existing')
                elif op=='plan':fixed_args.append('--resume-if-existing')
                # Typed repository wrappers are source-bound by default. The
                # runtime reads Git HEAD and checks worktree cleanliness; no
                # Git query is performed during pure resolution or sealing.
                fixed_args.append('--require-local-head')
                for key,value in sorted(spec.get('inputs',{}).items()):
                    fixed_args.extend(['--input',str(key)+'='+str(value)])
    else:
        command_token = "invoke"
        fixed_args = [command.workflow]

    scope_args: list[str] = []
    if command.target:
        scope_args.extend(["--target", command.target])
    elif command.group:
        scope_args.extend(["--group", command.group])
    elif command.all_targets:
        scope_args.append("--all-targets")

    if command.devtool=='actions' and command.actions['operation'] in {'watch','resume','artifacts'}:
        import re
        req=values_by_name.get('request_id')
        if not isinstance(req,str) or not re.fullmatch(r'[a-f0-9]{32}',req):
            raise _fail('Actions wrapper requires an exact 32-character dispatch request ID')
    command_argv = [*fixed_args, *scope_args, *command_bindings]
    passthrough_argv = [*passthrough_bindings, *raw_passthrough]
    canonical = [*global_argv, command_token, *command_argv]
    if passthrough_argv:
        canonical.extend(["--", *passthrough_argv])

    sensitive_options, sensitive_values = _collect_sensitive_texts(
        command,
        values_by_name,
        active_options=active_options,
    )
    redacted_canonical = _redact_argv(canonical, sensitive_values)

    return WrapperResolution(
        schema=config.wrapper.schema,
        mode="execute",
        command=command.name,
        command_spelling=command_spelling,
        execution_kind="native" if command.devtool else "workflow",
        devtool_command=command.devtool,
        workflow_action=command.workflow,
        target=command.target,
        group=command.group,
        all_targets=command.all_targets,
        options=tuple(resolved_options),
        positionals=tuple(resolved_positionals),
        raw_passthrough_argv=raw_passthrough,
        sensitive_options=sensitive_options,
        global_argv=tuple(global_argv),
        command_argv=tuple(command_argv),
        passthrough_argv=tuple(passthrough_argv),
        canonical_argv=tuple(canonical),
        redacted_canonical_argv=redacted_canonical,
        repo_root=str(repo_root),
        launch_cwd=str(cwd),
    )


def wrapper_resolution_as_dict(resolution: WrapperResolution) -> dict[str, Any]:
    """Return a deterministic JSON-safe, secret-safe representation.

    The :class:`WrapperResolution` object retains raw argv for in-process
    execution.  Machine/display serialization never exports sensitive option
    values or argv tokens containing them.
    """

    sensitive_names = set(resolution.sensitive_options)
    sensitive_values: list[str] = []
    for item in resolution.options:
        if item.name not in sensitive_names:
            continue
        values = item.value if isinstance(item.value, tuple) else (item.value,)
        for value in values:
            if value is None or isinstance(value, bool):
                continue
            text = _value_text(value)
            if text:
                sensitive_values.append(text)
    sensitive_values = list(dict.fromkeys(sensitive_values))

    def json_value(value: object, *, sensitive: bool = False) -> object:
        if isinstance(value, tuple):
            return [json_value(item, sensitive=sensitive) for item in value]
        if sensitive and value is not None and not isinstance(value, bool):
            return _ENV_UNSAFE_CHARS_REPLACEMENT
        if isinstance(value, str):
            return _redact_argv((value,), sensitive_values)[0]
        return value

    def argv_value(argv: Sequence[str]) -> list[str]:
        return list(_redact_argv(argv, sensitive_values))

    safe_canonical = argv_value(resolution.canonical_argv)
    return {
        "schema": resolution.schema,
        "mode": resolution.mode,
        "command": resolution.command,
        "command_spelling": resolution.command_spelling,
        "execution_kind": resolution.execution_kind,
        "devtool_command": resolution.devtool_command,
        "workflow_action": resolution.workflow_action,
        "scope": {
            "target": resolution.target,
            "group": resolution.group,
            "all_targets": resolution.all_targets,
        },
        "options": {
            item.name: {
                "value": json_value(item.value, sensitive=item.name in sensitive_names),
                "provided": item.provided,
                "defaulted": item.defaulted,
            }
            for item in resolution.options
        },
        "positionals": {name: json_value(value) for name, value in resolution.positionals},
        "raw_passthrough_argv": argv_value(resolution.raw_passthrough_argv),
        "sensitive_options": list(resolution.sensitive_options),
        "global_argv": argv_value(resolution.global_argv),
        "command_argv": argv_value(resolution.command_argv),
        "passthrough_argv": argv_value(resolution.passthrough_argv),
        "canonical_argv": safe_canonical,
        "redacted_canonical_argv": safe_canonical,
        "repo_root": resolution.repo_root,
        "launch_cwd": resolution.launch_cwd,
        "help_requested": resolution.help_requested,
        "version_requested": resolution.version_requested,
    }
