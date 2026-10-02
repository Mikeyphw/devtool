"""Canonical argparse compiler for Devtool's checked-in CLI schema.

The JSON schema is the runtime command/option authority.  This module lowers the
schema into argparse without discovering identities from argparse.  The old
hand-written parser construction helpers may remain temporarily as dead
compatibility code in ``cli.py`` while callers migrate, but ``build_parser``
never executes them.
"""
from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


_FORMATTERS: dict[str, type[argparse.HelpFormatter]] = {
    "HelpFormatter": argparse.HelpFormatter,
    "RawDescriptionHelpFormatter": argparse.RawDescriptionHelpFormatter,
    "RawTextHelpFormatter": argparse.RawTextHelpFormatter,
    "ArgumentDefaultsHelpFormatter": argparse.ArgumentDefaultsHelpFormatter,
    "MetavarTypeHelpFormatter": argparse.MetavarTypeHelpFormatter,
}
_TYPES: dict[str, Any] = {"int": int, "float": float, "str": str}
_ACTIONS = {
    "store",
    "store_true",
    "store_false",
    "store_const",
    "append",
    "append_const",
    "count",
    "extend",
}
_SPECIAL = {
    "SUPPRESS": argparse.SUPPRESS,
    "REMAINDER": argparse.REMAINDER,
    "PARSER": argparse.PARSER,
}


class CanonicalParserSchemaError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def canonical_cli_schema() -> Mapping[str, Any]:
    path = Path(__file__).with_name("core_cli_schema.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("schema_version") or 0) != 2 or raw.get("kind") != "devtool.canonical-cli-schema":
        raise CanonicalParserSchemaError(f"invalid canonical CLI schema: {path}")
    parser = raw.get("parser")
    if not isinstance(parser, Mapping):
        raise CanonicalParserSchemaError(f"canonical CLI schema has no parser authority: {path}")
    return raw


def _decode(value: Any) -> Any:
    if isinstance(value, Mapping) and "$argparse" in value:
        key = str(value["$argparse"])
        try:
            return _SPECIAL[key]
        except KeyError as exc:
            raise CanonicalParserSchemaError(f"unsupported argparse sentinel {key!r}") from exc
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _formatter(name: object) -> type[argparse.HelpFormatter]:
    key = str(name or "HelpFormatter")
    try:
        return _FORMATTERS[key]
    except KeyError as exc:
        raise CanonicalParserSchemaError(f"unsupported argparse formatter {key!r}") from exc


def _add_action(container: Any, row: Mapping[str, Any]) -> None:
    options = [str(item) for item in row.get("option_strings", ())]
    dest = str(row.get("dest") or "")
    if not dest:
        raise CanonicalParserSchemaError("canonical parser action is missing dest")
    positional = not options
    argv = [dest] if positional else options
    action = str(row.get("action") or "store")
    if action not in _ACTIONS:
        raise CanonicalParserSchemaError(f"unsupported argparse action {action!r}")

    kwargs: dict[str, Any] = {
        "default": _decode(row.get("default")),
        "help": _decode(row.get("help")),
    }
    if action != "store":
        kwargs["action"] = action
    if not positional:
        kwargs["dest"] = dest
        if bool(row.get("required")):
            kwargs["required"] = True
    metavar = row.get("metavar")
    if metavar is not None:
        kwargs["metavar"] = _decode(metavar)

    if action in {"store", "append", "extend"}:
        nargs = row.get("nargs")
        if nargs is not None:
            kwargs["nargs"] = _decode(nargs)
        const = row.get("const")
        if const is not None:
            kwargs["const"] = _decode(const)
        type_name = row.get("type")
        if type_name is not None:
            try:
                kwargs["type"] = _TYPES[str(type_name)]
            except KeyError as exc:
                raise CanonicalParserSchemaError(f"unsupported argparse type {type_name!r}") from exc
        choices = row.get("choices")
        if choices is not None:
            kwargs["choices"] = _decode(choices)
    elif action in {"store_const", "append_const"}:
        kwargs["const"] = _decode(row.get("const"))

    container.add_argument(*argv, **kwargs)


def _configure_parser(parser: argparse.ArgumentParser, spec: Mapping[str, Any]) -> argparse.ArgumentParser:
    parser.description = spec.get("description")
    parser.epilog = spec.get("epilog")
    parser.usage = spec.get("usage")

    groups: list[Any] = []
    for raw in spec.get("groups", ()):
        if not isinstance(raw, Mapping):
            raise CanonicalParserSchemaError("invalid parser argument-group row")
        groups.append(parser.add_argument_group(str(raw.get("title") or ""), raw.get("description")))

    mutexes: list[Any] = []
    for raw in spec.get("mutexes", ()):
        if not isinstance(raw, Mapping):
            raise CanonicalParserSchemaError("invalid parser mutex row")
        group_index = raw.get("group")
        container = parser if group_index is None else groups[int(group_index)]
        mutexes.append(container.add_mutually_exclusive_group(required=bool(raw.get("required"))))

    for raw in spec.get("actions", ()):
        if not isinstance(raw, Mapping):
            raise CanonicalParserSchemaError("invalid parser action row")
        mutex_index = raw.get("mutex")
        group_index = raw.get("group")
        if mutex_index is not None:
            container = mutexes[int(mutex_index)]
        elif group_index is not None:
            container = groups[int(group_index)]
        else:
            container = parser
        _add_action(container, raw)

    defaults = spec.get("defaults")
    if isinstance(defaults, Mapping) and defaults:
        parser.set_defaults(**{str(key): _decode(value) for key, value in defaults.items()})

    sub = spec.get("subparsers")
    if isinstance(sub, Mapping):
        kwargs: dict[str, Any] = {
            "dest": str(sub.get("dest") or ""),
            "required": bool(sub.get("required")),
        }
        if sub.get("help") is not None:
            kwargs["help"] = _decode(sub.get("help"))
        if sub.get("metavar") is not None:
            kwargs["metavar"] = _decode(sub.get("metavar"))
        group_index = sub.get("group")
        container = parser if group_index is None else groups[int(group_index)]
        subparsers = container.add_subparsers(**kwargs)
        for child in sub.get("children", ()):
            if not isinstance(child, Mapping) or not isinstance(child.get("parser"), Mapping):
                raise CanonicalParserSchemaError("invalid canonical child parser row")
            child_spec = child["parser"]
            child_kwargs: dict[str, Any] = {
                "aliases": [str(item) for item in child.get("aliases", ())],
                "description": child_spec.get("description"),
                "epilog": child_spec.get("epilog"),
                "formatter_class": _formatter(child_spec.get("formatter")),
                "add_help": bool(child_spec.get("add_help", True)),
                "allow_abbrev": bool(child_spec.get("allow_abbrev", True)),
            }
            if bool(child.get("help_present")):
                child_kwargs["help"] = _decode(child.get("help"))
            child_parser = subparsers.add_parser(str(child.get("name") or ""), **child_kwargs)
            _configure_parser(child_parser, child_spec)
    return parser


def build_canonical_parser() -> argparse.ArgumentParser:
    schema = canonical_cli_schema()
    spec = schema["parser"]
    parser = argparse.ArgumentParser(
        prog="devtool",
        description=spec.get("description"),
        epilog=spec.get("epilog"),
        formatter_class=_formatter(spec.get("formatter")),
        add_help=bool(spec.get("add_help", True)),
        allow_abbrev=bool(spec.get("allow_abbrev", True)),
    )
    return _configure_parser(parser, spec)


def parser_identity_projection() -> dict[str, set[Any]]:
    """Project parser identities directly from the canonical schema.

    Parent aliases are expanded through descendants (``fed status``), while
    options are attributed to the semantic canonical command path.  The
    projection reads only the checked-in schema and never introspects argparse.
    """
    schema = canonical_cli_schema()
    spec = schema["parser"]
    commands: set[tuple[str, ...]] = set()
    options: set[tuple[tuple[str, ...], str]] = set()
    canonical_for_path: dict[tuple[str, ...], tuple[str, ...]] = {}
    for raw in schema.get("commands", ()):
        if not isinstance(raw, Mapping):
            continue
        canonical = tuple(str(x) for x in raw.get("canonical_path", ()) if str(x))
        if not canonical:
            continue
        canonical_for_path[canonical] = canonical
        for alias in raw.get("aliases", ()):
            if isinstance(alias, list):
                path = tuple(str(x) for x in alias if str(x))
                if path:
                    canonical_for_path[path] = canonical

    def semantic_path(spellings: set[tuple[str, ...]], fallback: tuple[str, ...]) -> tuple[str, ...]:
        for spelling in sorted(spellings, key=lambda item: (0 if item == fallback else 1, len(item), item)):
            if spelling in canonical_for_path:
                return canonical_for_path[spelling]
        return canonical_for_path.get(fallback, fallback)

    def walk(node: Mapping[str, Any], canonical_hint: tuple[str, ...], spellings: set[tuple[str, ...]]) -> None:
        option_path = semantic_path(spellings, canonical_hint)
        if bool(node.get("add_help", True)):
            options.add((option_path, "-h"))
            options.add((option_path, "--help"))
        for action in node.get("actions", ()):
            if not isinstance(action, Mapping):
                continue
            for spelling in action.get("option_strings", ()):
                text = str(spelling)
                if text:
                    options.add((option_path, text))
        sub = node.get("subparsers")
        if not isinstance(sub, Mapping):
            return
        for child in sub.get("children", ()):
            if not isinstance(child, Mapping) or not isinstance(child.get("parser"), Mapping):
                continue
            name = str(child.get("name") or "")
            local_spellings = [name, *(str(x) for x in child.get("aliases", ()) if str(x))]
            child_spellings = {(*prefix, local) for prefix in spellings for local in local_spellings}
            commands.update(child_spellings)
            child_hint = (*canonical_hint, name)
            walk(child["parser"], semantic_path(child_spellings, child_hint), child_spellings)

    walk(spec, (), {()})
    return {"commands": commands, "options": options}


__all__ = [
    "CanonicalParserSchemaError",
    "build_canonical_parser",
    "canonical_cli_schema",
    "parser_identity_projection",
]
