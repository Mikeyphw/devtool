"""NEXT-06-R1 read-only dependency inventory for a *draft* extraction manifest.

Discovery is an untrusted suggestion producer, never a prepare/execute authority.
Every move and byte rewrite must still be reviewed in the original manifest and
pass repository_extraction._candidate and its independently pinned Git contracts.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import hashlib
import re
from typing import Any

from .repository_extraction import (
    SCHEMA, ExtractionBlocked, _read_manifest, _identity, _git, _tracked,
    _ignored, _rel, _safe, _transaction_free,
)

DISCOVERY_SCHEMA = "devtool.repo-extraction-discovery.v1"
MAX_FILES = 5000
MAX_FILE_BYTES = 256 * 1024
MAX_SCAN_BYTES = 32 * 1024 * 1024
MAX_REFERENCES = 500
MAX_IGNORED = 1000
PROJECT_MARKERS = {
    "go.mod", "go.work", "package.json", "Cargo.toml", "pyproject.toml",
    "settings.gradle", "settings.gradle.kts", "pom.xml", "CMakeLists.txt",
}
CONFIG_SUFFIXES = (".yml", ".yaml", ".toml", ".json", ".xml", ".gradle", ".kts", ".sh", ".bash", ".zsh", ".mk", ".cmake", ".properties")
SOURCE_SUFFIXES = (".py", ".go", ".rs", ".js", ".ts", ".java", ".kt", ".cpp", ".cc", ".c", ".h")


def _mode_inventory(root: Path) -> dict[str, str]:
    raw = _git(root, "ls-files", "--stage", "-z", binary=True)
    modes: dict[str, str] = {}
    for part in raw.split(b"\0"):
        if not part:
            continue
        try:
            meta, name = part.split(b"\t", 1)
            mode = meta.split(b" ", 1)[0].decode("ascii")
            path = name.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise ExtractionBlocked("unreadable Git mode inventory") from exc
        modes[path] = mode
    return modes


def _within(path: str, prefix: str) -> bool:
    return path.startswith(prefix + "/")


def _tokens(moves: list[dict[str, str]]) -> list[tuple[str, str]]:
    out = []
    for row in moves:
        prefix = row["from"]
        out.append((prefix, prefix))
        # Python imports use dotted package names, which need separate review.
        dotted = prefix.replace("/", ".")
        if dotted != prefix:
            out.append((dotted, prefix))
        # A basename alone is ambiguous: surface as review, never must_rewrite.
        leaf = PurePosixPath(prefix).name
        if len(leaf) >= 4 and leaf != prefix:
            out.append((leaf, prefix))
    return sorted(set(out), key=lambda pair: (-len(pair[0]), pair[0]))


def _file_kind(path: str) -> str:
    name = PurePosixPath(path).name
    if path.startswith(".github/workflows/") or name in {"Dockerfile", "Makefile", "Justfile"} or path.endswith(CONFIG_SUFFIXES):
        return "configuration_or_workflow"
    if name in PROJECT_MARKERS:
        return "project_manifest"
    if path.endswith(SOURCE_SUFFIXES):
        return "source"
    return "other_text"


def _language_clue(line: str, path: str) -> str:
    stripped = line.strip()
    if ("../" in stripped or "..\\" in stripped) and ("import" in stripped or "require" in stripped or "replace" in stripped):
        return "relative_import_or_module"
    if re.search(r"\b(from|import)\s+[.\w]+", stripped) and path.endswith(".py"):
        return "python_import"
    if re.search(r"\b(replace|require|module)\b", stripped) and path.endswith(("go.mod", "go.work")):
        return "go_module"
    if path.startswith(".github/workflows/"):
        return "ci_workflow"
    if path.endswith((".sh", ".bash", ".zsh")):
        return "shell_wrapper"
    return "path_or_module_string"


def _read_git_text(root: Path, head: str, path: str, mode: str, notes: list[dict[str, Any]]) -> str | None:
    _rel(path)
    if mode not in {"100644", "100755"}:
        notes.append({"path": path, "reason": "symlink_or_nonregular_tracked_file", "classification": "likely_reference_review"})
        return None
    file = _safe(root, path)
    if not file.is_file():
        notes.append({"path": path, "reason": "symlink_or_nonregular_tracked_file", "classification": "likely_reference_review"})
        return None
    size = file.stat().st_size
    if size > MAX_FILE_BYTES:
        notes.append({"path": path, "reason": "oversized_unscanned", "classification": "likely_reference_review", "size": size})
        return None
    raw = _git(root, "show", head + ":" + path, binary=True)
    if len(raw) > MAX_FILE_BYTES or b"\0" in raw:
        notes.append({"path": path, "reason": "binary_or_oversized_unscanned", "classification": "likely_reference_review"})
        return None
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeError:
        notes.append({"path": path, "reason": "non_utf8_unscanned", "classification": "likely_reference_review"})
        return None


def discover(manifest: Path) -> dict[str, Any]:
    """Produce bounded, evidence-bearing *draft* proposals with no mutation.

    Requires the existing strict draft manifest to pin both checkout identities.
    Discovery does not invoke _candidate: unresolved references are precisely
    what discovery is meant to identify before reviewing rewrites.
    """
    config = _read_manifest(manifest)
    source = Path(config["source"]["path"]).resolve()
    dest = Path(config["destination"]["path"]).resolve()
    for side, root in (("source", source), ("destination", dest)):
        _identity(root, config[side])
        _transaction_free(root)
        if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ExtractionBlocked(side + " worktree dirty; re-pin and review before discovery")
    modes_src, modes_dst = _mode_inventory(source), _mode_inventory(dest)
    if "160000" in modes_src.values() or "160000" in modes_dst.values():
        raise ExtractionBlocked("nested submodule/gitlink cannot be discovered safely")
    if len(modes_src) + len(modes_dst) > MAX_FILES:
        raise ExtractionBlocked("tracked discovery scope exceeds 5000 files")
    moves = config["moves"]
    roots = [m["from"] for m in moves]
    for prefix in roots:
        _safe(source, prefix)
        if (source / prefix / ".git").exists() or (source / prefix / ".git").is_symlink():
            raise ExtractionBlocked("nested repository inside extraction prefix: " + prefix)
        if not any(_within(path, prefix) for path in modes_src):
            raise ExtractionBlocked("discovery prefix has no tracked paths: " + prefix)
    ignored = sorted(set(_ignored(source, roots)))
    if len(ignored) > MAX_IGNORED:
        raise ExtractionBlocked("ignored/generated discovery exceeds bound")
    notes: list[dict[str, Any]] = []
    backups = []
    for path in ignored:
        obj = _safe(source, path)
        if obj.is_symlink() or not obj.is_file():
            raise ExtractionBlocked("unsafe ignored/generated path in discovery: " + path)
        backups.append({"path": path, "classification": "generated_backup", "size": obj.stat().st_size, "review_required": True})

    # Values from source tree only; never include arbitrary line text in JSON, as
    # credentials may appear elsewhere on a matching source/configuration line.
    tokens = _tokens(moves)
    references: list[dict[str, Any]] = []
    irrelevant: list[dict[str, Any]] = []
    budget = 0
    for side, root, modes in (("source", source, modes_src), ("destination", dest, modes_dst)):
        for path in sorted(modes):
            if side == "source" and any(_within(path, p) for p in roots):
                transferred = True
            else:
                transferred = False
            budget += min((root / path).lstat().st_size, MAX_FILE_BYTES) if (root / path).exists() or (root / path).is_symlink() else MAX_FILE_BYTES
            if budget > MAX_SCAN_BYTES:
                raise ExtractionBlocked("discovery read budget exceeded")
            data = _read_git_text(root, config[side]["head"], path, modes[path], notes)
            if data is None:
                continue
            for number, line in enumerate(data.splitlines(), start=1):
                if len(line) > 16_384:
                    notes.append({"path": path, "reason": "oversized_line_unscanned", "classification": "likely_reference_review"})
                    continue
                seen = set()
                for token, origin in tokens:
                    if token in seen:
                        continue
                    # Do not report partial words / incidental substrings.
                    expression = r"(?<![\w.-])" + re.escape(token) + r"(?![\w.-])"
                    if not re.search(expression, line):
                        # A matching substring inside a different identifier is
                        # not a dependency; retain a small, explainable sample.
                        if token in line and len(irrelevant) < 64:
                            irrelevant.append({
                                "repository": side, "path": path, "line": number,
                                "line_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                                "matched_token": token, "classification": "irrelevant",
                                "reason": "identifier_boundary_rejected", "review_required": False,
                            })
                        continue
                    seen.add(token)
                    clue = _language_clue(line, path)
                    kind = _file_kind(path)
                    exact = token == origin
                    if side == "destination":
                        cls = "external_consumer_unknown"
                    elif transferred:
                        cls = "likely_reference_review" if clue != "relative_import_or_module" else "must_rewrite"
                    elif exact and kind in {"configuration_or_workflow", "project_manifest"}:
                        cls = "must_rewrite"
                    else:
                        cls = "likely_reference_review"
                    references.append({
                        "repository": side, "path": path, "line": number,
                        "line_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                        "matched_token": token, "moved_prefix": origin,
                        "signal": clue, "file_kind": kind, "classification": cls,
                        "confidence": "high" if exact and kind != "other_text" else "review",
                        "review_required": True,
                    })
                    if len(references) > MAX_REFERENCES:
                        raise ExtractionBlocked("discovery references exceed bounded evidence cap")
                # Relative module imports may cross a moved directory's root
                # without spelling that directory; never infer a rewrite value.
                if transferred and not seen and ("../" in line or "..\\" in line) and re.search(
                    r"\b(import|from|require|replace|include|source|path|working-directory)\b", line
                ):
                    origin = next(prefix for prefix in roots if _within(path, prefix))
                    references.append({
                        "repository": side, "path": path, "line": number,
                        "line_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                        "matched_token": "../", "moved_prefix": origin,
                        "signal": "relative_import_or_module", "file_kind": _file_kind(path),
                        "classification": "likely_reference_review", "confidence": "review",
                        "review_required": True,
                    })
                    if len(references) > MAX_REFERENCES:
                        raise ExtractionBlocked("discovery references exceed bounded evidence cap")
    # Deterministic user-authored move candidates, not an executable manifest.
    candidates = []
    for move in moves:
        root = move["from"]
        markers = sorted(path for path in modes_src if _within(path, root) and PurePosixPath(path).name in PROJECT_MARKERS)
        candidates.append({"from": root, "to": move["to"], "project_markers": markers[:20],
                           "classification": "likely_reference_review", "review_required": True,
                           "provenance": "user_supplied_draft_not_approved"})
    # Unselected project roots are hints only: never assume that a marker
    # represents an independently extractable dependency or a safe target path.
    suggested_roots: dict[str, list[str]] = {}
    for path in modes_src:
        if PurePosixPath(path).name not in PROJECT_MARKERS:
            continue
        parent = str(PurePosixPath(path).parent)
        if parent in (".", "") or any(parent == p or _within(parent, p) for p in roots):
            continue
        if path.count("/") > 6:
            continue
        suggested_roots.setdefault(parent, []).append(path)
    additional_moves = [{"from": parent, "to": None, "project_markers": sorted(markers)[:20],
                         "classification": "likely_reference_review", "review_required": True,
                         "provenance": "project_marker_heuristic_not_approved"}
                        for parent, markers in sorted(suggested_roots.items())[:32]]
    # Candidate rewrite entries are deliberately INCOMPLETE and cannot be used
    # as source_rewrites/destination_rewrites in the executable v1 manifest.
    rewrite_suggestions = [{"repository": r["repository"], "path": r["path"], "line": r["line"],
                            "matched_token": r["matched_token"], "replacement": None,
                            "classification": r["classification"], "review_required": True}
                           for r in references if r["classification"] == "must_rewrite"]
    # Refuse time-of-check/time-of-use drift in either checkout during scanning.
    for side, root in (("source", source), ("destination", dest)):
        _identity(root, config[side])
        if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ExtractionBlocked(side + " changed during dependency discovery")
    # Explicitly show that there is no approved change set in discovery output.
    return {
        "schema": DISCOVERY_SCHEMA, "ok": True, "state": "REVIEW_REQUIRED", "read_only": True,
        "manifest_schema": SCHEMA,
        "source_head": config["source"]["head"], "source_tree": config["source"]["tree"],
        "destination_head": config["destination"]["head"], "destination_tree": config["destination"]["tree"],
        "candidate_moves": candidates, "additional_move_suggestions": additional_moves,
        "rewrite_suggestions": rewrite_suggestions,
        "references": references, "generated_backups": backups,
        "irrelevant_matches": irrelevant, "unscanned": notes, "scan_complete": not bool(notes),
        "requires_manual_approval": True, "executable_manifest": None,
        "prepare_or_execute_authorized": False, "cross_repo_atomic": False,
        "release_qualified": False,
        "summary": {"tracked_examined": len(modes_src) + len(modes_dst),
                    "references": len(references), "generated_backups": len(backups),
                    "irrelevant_sample": len(irrelevant), "unscanned": len(notes)},
    }
