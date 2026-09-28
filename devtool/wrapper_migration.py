from __future__ import annotations

"""Schema-1 -> schema-2 repository wrapper migration helpers for CORE-X04."""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import re
import tempfile
import tomllib
from typing import Any

from .wrapper_config import WRAPPER_CANONICAL_SCHEMA_VERSION, WRAPPER_SUPPORTED_SCHEMA_VERSIONS

_WRAPPER_HEADER = re.compile(r"^\s*\[wrapper\]\s*(?:#.*)?$")
_TABLE_HEADER = re.compile(r"^\s*\[[^\]]+\]\s*(?:#.*)?$")
_SCHEMA_ASSIGNMENT = re.compile(r"^(?P<indent>\s*)schema\s*=\s*(?P<value>\d+)(?P<tail>\s*(?:#.*)?)$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WrapperMigrationResult:
    source_schema: int
    target_schema: int
    changed: bool
    source_sha256: str
    migrated_sha256: str
    migrated_text: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "devtool.wrapper-schema-migration",
            "source_schema": self.source_schema,
            "target_schema": self.target_schema,
            "changed": self.changed,
            "source_sha256": self.source_sha256,
            "migrated_sha256": self.migrated_sha256,
            "idempotent": True,
        }


def migrate_wrapper_schema_text(text: str) -> WrapperMigrationResult:
    raw = tomllib.loads(text)
    wrapper = raw.get("wrapper")
    if wrapper is None:
        raise ValueError("repository has no [wrapper] contract to migrate")
    if not isinstance(wrapper, dict):
        raise ValueError("[wrapper] must be a TOML table")
    source_schema = wrapper.get("schema", 1)
    if isinstance(source_schema, bool) or not isinstance(source_schema, int) or source_schema not in WRAPPER_SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported wrapper schema: {source_schema!r}")
    if source_schema == WRAPPER_CANONICAL_SCHEMA_VERSION:
        return WrapperMigrationResult(source_schema, WRAPPER_CANONICAL_SCHEMA_VERSION, False, _sha(text), _sha(text), text)

    lines = text.splitlines(keepends=True)
    wrapper_start = None
    wrapper_end = len(lines)
    for index, line in enumerate(lines):
        if wrapper_start is None:
            if _WRAPPER_HEADER.match(line.rstrip("\r\n")):
                wrapper_start = index
            continue
        if index > wrapper_start and _TABLE_HEADER.match(line.rstrip("\r\n")):
            wrapper_end = index
            break
    if wrapper_start is None:
        raise ValueError("repository has no [wrapper] header to migrate")

    schema_index = None
    newline = "\r\n" if "\r\n" in text else "\n"
    for index in range(wrapper_start + 1, wrapper_end):
        candidate = lines[index].rstrip("\r\n")
        match = _SCHEMA_ASSIGNMENT.match(candidate)
        if not match:
            continue
        schema_index = index
        tail = match.group("tail") or ""
        lines[index] = f"{match.group('indent')}schema = {WRAPPER_CANONICAL_SCHEMA_VERSION}{tail}{newline}"
        break
    if schema_index is None:
        lines.insert(wrapper_start + 1, f"schema = {WRAPPER_CANONICAL_SCHEMA_VERSION}{newline}")

    migrated = "".join(lines)
    parsed = tomllib.loads(migrated)
    migrated_wrapper = parsed.get("wrapper")
    if not isinstance(migrated_wrapper, dict) or migrated_wrapper.get("schema") != WRAPPER_CANONICAL_SCHEMA_VERSION:
        raise ValueError("wrapper migration did not produce schema 2")
    return WrapperMigrationResult(source_schema, WRAPPER_CANONICAL_SCHEMA_VERSION, migrated != text, _sha(text), _sha(migrated), migrated)


def migrate_wrapper_schema_file(path: str | Path, *, write: bool = False) -> WrapperMigrationResult:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    result = migrate_wrapper_schema_text(text)
    if write and result.changed:
        mode = target.stat().st_mode
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), prefix=f".{target.name}.", delete=False, newline="") as handle:
            handle.write(result.migrated_text)
            temp = Path(handle.name)
        temp.chmod(mode)
        os.replace(temp, target)
    return result
