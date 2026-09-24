from __future__ import annotations

"""Human and machine discovery helpers for repository wrappers."""

from dataclasses import asdict
from typing import Any

from . import __version__
from .models import ProjectConfig, WrapperCommandConfig, WrapperOptionConfig, WrapperPositionalConfig
from .wrapper_config import wrapper_as_dict
from .wrapper_launcher import launcher_status, launcher_status_as_dict

WRAPPER_HELP_SCHEMA_VERSION = 1


def _scope(command: WrapperCommandConfig) -> str:
    if command.target:
        return f"target:{command.target}"
    if command.group:
        return f"group:{command.group}"
    if command.all_targets:
        return "all-targets"
    return "default-target"


def _action(command: WrapperCommandConfig) -> str:
    return command.devtool or f"invoke {command.workflow}"


def _option_tokens(option: WrapperOptionConfig) -> list[str]:
    tokens = [f"--{option.name}"]
    for alias in option.aliases:
        tokens.append(f"-{alias}" if len(alias) == 1 else f"--{alias}")
    return tokens


def _option_summary(option: WrapperOptionConfig) -> dict[str, Any]:
    return {
        **asdict(option),
        "tokens": _option_tokens(option),
    }


def _positional_summary(positional: WrapperPositionalConfig) -> dict[str, Any]:
    return asdict(positional)


def wrapper_command_summary(command: WrapperCommandConfig) -> dict[str, Any]:
    return {
        "name": command.name,
        "description": command.description,
        "execution_kind": "native" if command.devtool else "workflow",
        "action": _action(command),
        "scope": _scope(command),
        "aliases": list(command.aliases),
        "allow_passthrough": command.allow_passthrough,
        "options": [_option_summary(item) for _name, item in sorted(command.options.items())],
        "positionals": [_positional_summary(item) for item in command.positionals],
    }


def wrapper_discovery_payload(config: ProjectConfig) -> dict[str, Any]:
    status = launcher_status(config.repo_root)
    return {
        "schema_version": WRAPPER_HELP_SCHEMA_VERSION,
        "repository": str(config.repo_root),
        "config_path": str(config.config_path) if config.config_path else "",
        "wrapper": wrapper_as_dict(config.wrapper),
        "default_command": config.wrapper.default_command,
        "commands": [wrapper_command_summary(item) for _name, item in sorted(config.wrapper.commands.items())],
        "launchers": launcher_status_as_dict(status),
        "docs": "docs/WRAPPER.md",
    }


def render_wrapper_overview(config: ProjectConfig, *, program: str = "devtoolw") -> str:
    if not config.wrapper.commands:
        return (
            "No repository wrapper commands are configured.\n\n"
            "Add a [wrapper] section to .devtool.toml, then run:\n"
            "  devtool wrapper init\n"
        )
    lines = [
        f"Usage: {program} <command> [options] [-- passthrough]",
        "",
        f"Wrapper schema: {config.wrapper.schema}",
    ]
    if config.wrapper.default_command:
        lines.append(f"Default command: {config.wrapper.default_command}")
    lines.extend(["", "Commands:"])
    width = max(len(name) for name in config.wrapper.commands) if config.wrapper.commands else 1
    for name, command in sorted(config.wrapper.commands.items()):
        alias = f" (aliases: {', '.join(command.aliases)})" if command.aliases else ""
        desc = f" - {command.description}" if command.description else ""
        lines.append(f"  {name.ljust(width)}  {_action(command)} [{_scope(command)}]{alias}{desc}")
    lines.extend([
        "",
        "Control:",
        f"  {program} --help              Show this overview.",
        f"  {program} <command> --help    Show command-specific help.",
        f"  {program} --version           Show Devtool wrapper version.",
        "",
        "Devtool management:",
        "  devtool wrapper describe      Machine/human contract discovery.",
        "  devtool wrapper resolve ...   Resolve without executing.",
        "  devtool wrapper init/update   Create or refresh devtoolw launchers.",
    ])
    return "\n".join(lines) + "\n"


def render_wrapper_command_help(config: ProjectConfig, command_name: str, *, program: str = "devtoolw") -> str:
    command = config.wrapper.commands.get(command_name)
    if command is None:
        return render_wrapper_overview(config, program=program)
    usage_parts = [program, command.name]
    if command.options:
        usage_parts.append("[options]")
    for positional in command.positionals:
        marker = f"<{positional.name}>"
        if positional.repeatable:
            marker += "..."
        if not positional.required:
            marker = f"[{marker}]"
        usage_parts.append(marker)
    if command.allow_passthrough:
        usage_parts.append("[-- passthrough]")
    lines = [
        "Usage: " + " ".join(usage_parts),
        "",
        f"Command: {command.name}",
        f"Action: {_action(command)}",
        f"Scope: {_scope(command)}",
    ]
    if command.description:
        lines.append(f"Description: {command.description}")
    if command.aliases:
        lines.append(f"Aliases: {', '.join(command.aliases)}")
    if command.positionals:
        lines.extend(["", "Positionals:"])
        for positional in command.positionals:
            flags = []
            if positional.required:
                flags.append("required")
            if positional.repeatable:
                flags.append("repeatable")
            if positional.choices:
                flags.append("choices: " + ", ".join(positional.choices))
            if positional.relative_to:
                flags.append(f"relative_to={positional.relative_to}")
            suffix = f" ({'; '.join(flags)})" if flags else ""
            desc = f" - {positional.description}" if positional.description else ""
            lines.append(f"  {positional.name}: {positional.type}{suffix}{desc}")
    if command.options:
        lines.extend(["", "Options:"])
        for option in sorted(command.options.values(), key=lambda item: item.name):
            flags = []
            if option.required:
                flags.append("required")
            if option.repeatable:
                flags.append("repeatable")
            if option.has_default:
                flags.append(f"default={option.default!r}")
            if option.choices:
                flags.append("choices: " + ", ".join(option.choices))
            if option.conflicts:
                flags.append("conflicts: " + ", ".join(f"--{name}" for name in option.conflicts))
            if option.requires:
                flags.append("requires: " + ", ".join(f"--{name}" for name in option.requires))
            if option.relative_to:
                flags.append(f"relative_to={option.relative_to}")
            suffix = f" ({'; '.join(flags)})" if flags else ""
            desc = f" - {option.description}" if option.description else ""
            lines.append(f"  {', '.join(_option_tokens(option))}: {option.type}{suffix}{desc}")
    if command.allow_passthrough:
        lines.extend(["", "Passthrough:", "  Values after -- are forwarded to the underlying native Devtool command."])
    return "\n".join(lines) + "\n"


def render_wrapper_version() -> str:
    return f"devtool {__version__}\n"
