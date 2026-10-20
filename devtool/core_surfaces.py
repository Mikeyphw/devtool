from __future__ import annotations

"""CORE-X04 generated discovery, schema, completion and adoption surfaces.

This module is deliberately projection-only.  It consumes canonical/adopted
CORE identities plus the live argparse frontend while the monolithic parser is
being migrated, and emits deterministic machine/help/completion/schema
artifacts.  It does not own execution, scheduling, transport, presentation, or
transaction semantics.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import argparse
import hashlib
import json
import os

from .core_registry import CoreRegistry, build_core_registry, assert_parser_matches_canonical_registry

SURFACE_SCHEMA_VERSION = 1
PERFORMANCE_BUDGETS_MS = {
    "startup_parser": 5000,
    "generated_surfaces": 5000,
    "machine_help_filter": 1000,
    "completion_generation": 1000,
    "run_query_10000": 2000,
}
GENERATED_SURFACE_FILES = (
    "machine-help.json",
    "config-schema.json",
    "completion.zsh",
    "completion.bash",
    "completion.fish",
    "reference.md",
    "devtool-core.1",
    "ownership.json",
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    raw = value if isinstance(value, str) else _stable_json(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _indexed_option_rows(registry: CoreRegistry) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    """Index canonical options once, preserving source order and payload bytes.

    The previous projection scanned the complete options registry once for
    *each* command (quadratic work on a large CLI).  Grouping by canonical
    path is a pure, invocation-local projection: no cache, no relaxed parity,
    and no change to hidden/alias/option handling.
    """
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for option in registry.options:
        metadata = dict(option.metadata)
        path = tuple(str(x) for x in (metadata.get("path") or ()))
        grouped.setdefault(path, []).append({
            "id": option.stable_id,
            "dest": str(metadata.get("dest") or ""),
            "spellings": list(option.all_spellings()),
            "required": bool(metadata.get("required")),
            "nargs": metadata.get("nargs"),
            "choices": list(metadata.get("choices") or ()),
            "help": "" if metadata.get("hidden") else str(metadata.get("help") or ""),
            "hidden": bool(metadata.get("hidden")),
        })
    return grouped


def _parser_by_paths(parser: argparse.ArgumentParser) -> dict[tuple[str, ...], argparse.ArgumentParser]:
    result: dict[tuple[str, ...], argparse.ArgumentParser] = {}

    def visit(current: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> None:
        for action in current._actions:
            if not isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
                continue
            for name, child in action.choices.items():
                path = (*prefix, str(name))
                result[path] = child
                visit(child, path)

    visit(parser)
    return result


def machine_help_payload(parser: argparse.ArgumentParser, registry: CoreRegistry | None = None) -> dict[str, Any]:
    registry = registry or build_core_registry()
    assert_parser_matches_canonical_registry(parser, registry)
    parser_map = _parser_by_paths(parser)
    options_by_path = _indexed_option_rows(registry)
    commands: list[dict[str, Any]] = []
    for command in registry.commands:
        command_parser = parser_map.get(command.canonical_path)
        if command_parser is None:
            command_parser = next((parser_map.get(alias) for alias in command.aliases if alias in parser_map), None)
        metadata = dict(command.metadata)
        summary = str(metadata.get("summary") or "")
        if command_parser is not None and not summary:
            summary = str(getattr(command_parser, "description", "") or "").strip()
        commands.append(
            {
                "id": command.stable_id,
                "path": list(command.canonical_path),
                "aliases": [list(item) for item in command.aliases],
                "domain": str(metadata.get("domain") or (command.canonical_path[0] if command.canonical_path else "root")),
                "summary": summary,
                "stability": str(metadata.get("stability") or "stable"),
                "adoption_source": str(metadata.get("adoption_source") or "core-registry"),
                "options": [dict(row) for row in options_by_path.get(command.canonical_path, ())],
            }
        )
    parity = registry.parser_parity(parser)
    payload = {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "kind": "devtool.machine-help",
        "registry_schema_version": registry.schema_version,
        "registry_digest": registry.digest,
        "commands": commands,
        "parser_parity": parity,
    }
    payload["performance_budget_ms"] = PERFORMANCE_BUDGETS_MS["startup_parser"]
    payload["digest"] = _sha(payload)
    return payload


def filter_machine_help(
    payload: Mapping[str, Any],
    *,
    search: str = "",
    include_aliases: bool = False,
    include_advanced: bool = False,
) -> dict[str, Any]:
    needle = search.strip().lower()
    selected: list[dict[str, Any]] = []
    for raw in payload.get("commands", []) or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        aliases = [" ".join(item) for item in row.get("aliases", []) if isinstance(item, list)]
        haystack = " ".join(
            [
                str(row.get("id") or ""),
                " ".join(str(x) for x in row.get("path", []) or []),
                str(row.get("summary") or ""),
                str(row.get("domain") or ""),
                *aliases,
            ]
        ).lower()
        if needle and needle not in haystack:
            continue
        options = []
        for option in row.get("options", []) or []:
            if not isinstance(option, Mapping):
                continue
            if option.get("hidden") and not include_advanced:
                continue
            options.append(dict(option))
        row["options"] = options
        if not include_aliases:
            row["aliases"] = []
        selected.append(row)
    result = {
        "schema_version": int(payload.get("schema_version") or SURFACE_SCHEMA_VERSION),
        "kind": "devtool.machine-help.view",
        "registry_digest": str(payload.get("registry_digest") or ""),
        "search": search,
        "include_aliases": include_aliases,
        "include_advanced": include_advanced,
        "commands": selected,
        "count": len(selected),
    }
    result["digest"] = _sha(result)
    return result


def render_help_text(payload: Mapping[str, Any]) -> str:
    lines = [f"Devtool command catalog ({int(payload.get('count') or 0)} commands)"]
    search = str(payload.get("search") or "")
    if search:
        lines.append(f"search: {search}")
    lines.append("")
    for command in payload.get("commands", []) or []:
        if not isinstance(command, Mapping):
            continue
        path = " ".join(str(item) for item in command.get("path", []) or [])
        summary = str(command.get("summary") or "").strip()
        line = f"  {path}"
        if summary:
            line += f" — {summary}"
        lines.append(line)
        aliases = [" ".join(item) for item in command.get("aliases", []) if isinstance(item, list)]
        if aliases:
            lines.append(f"    aliases: {', '.join(aliases)}")
    return "\n".join(lines).rstrip() + "\n"


def config_schema_payload(registry: CoreRegistry) -> dict[str, Any]:
    documents: dict[str, Any] = {}
    for document in registry.config_documents:
        fields: list[dict[str, Any]] = []
        for field in document.fields:
            row = field.to_dict()
            # Sensitive fields are described but never carry runtime values.
            row["serialization"] = "redacted" if field.sensitive else "normal"
            fields.append(row)
        documents[document.name] = {
            "name": document.name,
            "fields": fields,
            "allow_unknown_top_level": list(document.allow_unknown_top_level),
        }
    payload = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema_version": SURFACE_SCHEMA_VERSION,
        "kind": "devtool.config-schema",
        "registry_schema_version": registry.schema_version,
        "registry_digest": registry.digest,
        "documents": documents,
        "reserved_namespaces": list(registry.reserved_namespaces),
        "environment": [item.to_dict() for item in registry.env],
        "option_config_links": list(registry.option_config_links()),
    }
    payload["digest"] = _sha(payload)
    return payload


def _root_words(registry: CoreRegistry) -> list[str]:
    words: list[str] = []
    for command in registry.commands:
        path = command.canonical_path
        if path:
            words.append(path[0])
    return sorted(dict.fromkeys(words))


def _children(registry: CoreRegistry, prefix: Sequence[str]) -> list[str]:
    p = tuple(prefix)
    children: list[str] = []
    for command in registry.commands:
        path = command.canonical_path
        if path[: len(p)] != p or len(path) <= len(p):
            continue
        children.append(path[len(p)])
    return sorted(dict.fromkeys(children))


def generated_completion(registry: CoreRegistry, shell: str) -> str:
    shell = shell.strip().lower()
    roots = _root_words(registry)
    if shell == "zsh":
        words = " ".join(roots)
        return (
            "#compdef devtool\n"
            "# Generated from the CORE-X04 command registry.\n"
            "_devtool_core_generated() {\n"
            f"  local -a root_commands; root_commands=({words})\n"
            "  if (( CURRENT == 2 )); then\n"
            "    _describe 'devtool command' root_commands\n"
            "    return\n"
            "  fi\n"
            "  _files\n"
            "}\n"
            "compdef _devtool_core_generated devtool\n"
        )
    if shell == "bash":
        words = " ".join(roots)
        return (
            "# Generated from the CORE-X04 command registry.\n"
            "_devtool_core_generated() {\n"
            "  local cur=\"${COMP_WORDS[COMP_CWORD]}\"\n"
            f"  if [[ $COMP_CWORD -eq 1 ]]; then COMPREPLY=( $(compgen -W \"{words}\" -- \"$cur\") ); else COMPREPLY=( $(compgen -f -- \"$cur\") ); fi\n"
            "}\n"
            "complete -F _devtool_core_generated devtool\n"
        )
    if shell == "fish":
        lines = ["# Generated from the CORE-X04 command registry.", "complete -c devtool -f"]
        lines.extend(f"complete -c devtool -n '__fish_use_subcommand' -a '{word}'" for word in roots)
        return "\n".join(lines) + "\n"
    raise ValueError(f"unsupported generated completion shell: {shell}")


def reference_markdown(machine_help: Mapping[str, Any], config_schema: Mapping[str, Any]) -> str:
    lines = [
        "# Devtool generated CORE reference",
        "",
        f"Registry digest: `{machine_help.get('registry_digest', '')}`",
        "",
        "This file is generated from the CORE command/config registry and the canonical CLI schema.",
        "",
        "## Commands",
        "",
    ]
    for command in machine_help.get("commands", []) or []:
        if not isinstance(command, Mapping):
            continue
        path = " ".join(str(item) for item in command.get("path", []) or [])
        summary = str(command.get("summary") or "")
        lines.append(f"- `{path}` — {summary}" if summary else f"- `{path}`")
    lines.extend(["", "## Configuration documents", ""])
    documents = config_schema.get("documents", {}) if isinstance(config_schema.get("documents"), Mapping) else {}
    for name, raw in sorted(documents.items()):
        fields = raw.get("fields", []) if isinstance(raw, Mapping) else []
        lines.append(f"### {name}")
        lines.append("")
        for field in fields:
            if not isinstance(field, Mapping):
                continue
            lines.append(f"- `{'.'.join(str(x) for x in field.get('path', []) or [])}` (`{field.get('field_id')}`): {field.get('value_kind')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _roff_escape(text: str) -> str:
    return str(text).replace("\\", r"\\e").replace("-", r"\\-")


def generated_manpage(machine_help: Mapping[str, Any]) -> str:
    """Render a deterministic registry-backed manual projection.

    The historical hand-written ``share/man/man1/devtool.1`` remains a broader
    human guide during migration.  This generated companion is the parity
    authority for command discovery and is intentionally simple enough to diff.
    """
    lines = [
        '.TH DEVTOOL-CORE 1 "" "Devtool" "Generated CORE command reference"',
        ".SH NAME",
        "devtool \\- generated canonical command reference",
        ".SH SYNOPSIS",
        ".B devtool",
        "[global-options] command [command-options]",
        ".SH DESCRIPTION",
        "This manual is generated from the canonical CORE registry and canonical CLI schema.",
        ".SH COMMANDS",
    ]
    for command in machine_help.get("commands", []) or []:
        if not isinstance(command, Mapping):
            continue
        path = " ".join(str(item) for item in command.get("path", []) or [])
        summary = str(command.get("summary") or "").strip()
        lines.extend([".TP", f".B {_roff_escape(path)}"])
        lines.append(_roff_escape(summary) if summary else "Canonical Devtool command.")
        aliases = [" ".join(item) for item in command.get("aliases", []) if isinstance(item, list)]
        if aliases:
            lines.append("Aliases: " + _roff_escape(", ".join(aliases)))
    lines.extend([
        ".SH GENERATED CONTRACT",
        f"Registry digest: {_roff_escape(str(machine_help.get('registry_digest') or ''))}",
        ".SH SEE ALSO",
        "devtool(1)",
    ])
    return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True, slots=True)
class GeneratedSurfaceBundle:
    registry: CoreRegistry
    machine_help: Mapping[str, Any]
    config_schema: Mapping[str, Any]
    completions: Mapping[str, str]
    reference: str
    manpage: str
    ownership: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return _sha(
            {
                "registry_digest": self.registry.digest,
                "machine_help": self.machine_help.get("digest"),
                "config_schema": self.config_schema.get("digest"),
                "completions": {key: _sha(value) for key, value in sorted(self.completions.items())},
                "reference": _sha(self.reference),
                "manpage": _sha(self.manpage),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SURFACE_SCHEMA_VERSION,
            "kind": "devtool.core-generated-surfaces",
            "registry_digest": self.registry.digest,
            "digest": self.digest,
            "parser_parity": dict(self.machine_help.get("parser_parity") or {}),
            "files": list(GENERATED_SURFACE_FILES),
            "ownership": dict(self.ownership),
            "performance_budgets_ms": dict(PERFORMANCE_BUDGETS_MS),
        }


def build_generated_surfaces(parser: argparse.ArgumentParser) -> GeneratedSurfaceBundle:
    registry = build_core_registry()
    assert_parser_matches_canonical_registry(parser, registry)
    machine_help = machine_help_payload(parser, registry)
    config_schema = config_schema_payload(registry)
    completions = {shell: generated_completion(registry, shell) for shell in ("zsh", "bash", "fish")}
    reference = reference_markdown(machine_help, config_schema)
    manpage = generated_manpage(machine_help)
    ownership = {
        "schema_version": 1,
        "owner": "CORE",
        "authority": "generated-projection",
        "canonical_sources": ["CoreRegistry", "core_cli_schema.json"],
        "generated_files": list(GENERATED_SURFACE_FILES[:-1]),
        "manifest_file": "manifest.json",
        "drift_policy": "exact-generated-set",
        "forbidden_authorities": ["execution", "scheduler", "transaction", "artifact-registry", "device-fabric"],
    }
    return GeneratedSurfaceBundle(registry, machine_help, config_schema, completions, reference, manpage, ownership)


def write_generated_surfaces(bundle: GeneratedSurfaceBundle, directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, bytes] = {
        "machine-help.json": (json.dumps(bundle.machine_help, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(),
        "config-schema.json": (json.dumps(bundle.config_schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(),
        "completion.zsh": bundle.completions["zsh"].encode(),
        "completion.bash": bundle.completions["bash"].encode(),
        "completion.fish": bundle.completions["fish"].encode(),
        "reference.md": bundle.reference.encode(),
        "devtool-core.1": bundle.manpage.encode(),
        "ownership.json": (json.dumps(bundle.ownership, indent=2, sort_keys=True) + "\n").encode(),
    }
    files: dict[str, dict[str, Any]] = {}
    for name, data in payloads.items():
        path = root / name
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    manifest = {
        "schema_version": 1,
        "kind": "devtool.core-generated-surface-manifest",
        "bundle_digest": bundle.digest,
        "registry_digest": bundle.registry.digest,
        "files": files,
    }
    manifest["digest"] = _sha(manifest)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def check_generated_surfaces(bundle: GeneratedSurfaceBundle, directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    expected = {
        "machine-help.json": (json.dumps(bundle.machine_help, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(),
        "config-schema.json": (json.dumps(bundle.config_schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(),
        "completion.zsh": bundle.completions["zsh"].encode(),
        "completion.bash": bundle.completions["bash"].encode(),
        "completion.fish": bundle.completions["fish"].encode(),
        "reference.md": bundle.reference.encode(),
        "devtool-core.1": bundle.manpage.encode(),
        "ownership.json": (json.dumps(bundle.ownership, indent=2, sort_keys=True) + "\n").encode(),
    }
    drift: list[dict[str, Any]] = []
    for name, data in expected.items():
        path = root / name
        if not path.exists():
            drift.append({"path": name, "reason": "missing"})
            continue
        actual = path.read_bytes()
        if actual != data:
            drift.append({"path": name, "reason": "content-drift", "expected_sha256": hashlib.sha256(data).hexdigest(), "actual_sha256": hashlib.sha256(actual).hexdigest()})
    if root.exists():
        expected_names = set(expected) | {"manifest.json"}
        for path in sorted(item for item in root.iterdir() if item.is_file()):
            if path.name not in expected_names:
                drift.append({"path": path.name, "reason": "unclassified-generated-output"})
    parity = dict(bundle.machine_help.get("parser_parity") or {})
    return {
        "schema_version": 1,
        "status": "pass" if not drift and int(parity.get("missing_count") or 0) == 0 else "fail",
        "bundle_digest": bundle.digest,
        "parser_parity": parity,
        "drift": drift,
    }
