from __future__ import annotations

"""Dependency-aware affected-target analysis for polyglot workspaces (MP07).

The selector deliberately prefers false positives over false negatives.  A
changed path that cannot be attributed to any configured target is treated as
workspace-wide for the candidate scope instead of being silently ignored.
"""

from dataclasses import dataclass, field
import fnmatch
import os
import posixpath
from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable, Mapping, Sequence

from .models import ProjectConfig, TargetConfig


class AffectedSelectionError(RuntimeError):
    """Raised when affected selection cannot be computed safely."""


@dataclass(frozen=True, slots=True)
class AffectedReason:
    kind: str
    path: str
    detail: str
    source: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "path": self.path,
            "detail": self.detail,
            "source": self.source,
        }


@dataclass(slots=True)
class AffectedSelection:
    targets: tuple[str, ...]
    changed_paths: tuple[str, ...]
    uncertain_paths: tuple[str, ...] = ()
    direct_targets: tuple[str, ...] = ()
    reasons: dict[str, tuple[AffectedReason, ...]] = field(default_factory=dict)
    candidates: tuple[str, ...] = ()
    base: str = ""
    head: str = ""
    source: str = "working-tree"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source": self.source,
            "base": self.base,
            "head": self.head,
            "changed_paths": list(self.changed_paths),
            "uncertain_paths": list(self.uncertain_paths),
            "candidates": list(self.candidates),
            "direct_targets": list(self.direct_targets),
            "targets": list(self.targets),
            "reasons": {
                name: [reason.as_dict() for reason in self.reasons.get(name, ())]
                for name in self.targets
            },
        }


def _git(repo_root: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AffectedSelectionError(f"Unable to inspect Git changes: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip() or f"git exited {proc.returncode}"
        raise AffectedSelectionError(f"Unable to inspect Git changes: {detail}")
    return proc.stdout


def _zpaths(payload: bytes) -> tuple[str, ...]:
    values: list[str] = []
    for raw in payload.split(b"\0"):
        if not raw:
            continue
        text = raw.decode("utf-8", errors="surrogateescape").strip()
        if text:
            values.append(PurePosixPath(text.replace("\\", "/")).as_posix())
    return tuple(values)


def git_changed_paths(
    repo_root: Path,
    *,
    base: str = "",
    head: str = "",
) -> tuple[str, ...]:
    """Return stable repository-relative paths changed for affected selection.

    With no refs, staged, unstaged, and untracked working-tree changes are
    combined. With ``base`` (and optional ``head``), Git's merge-base diff
    syntax is used so CI and local branch comparisons share the same semantics.
    """

    repo = Path(repo_root).expanduser().resolve()
    if not (repo / ".git").exists():
        raise AffectedSelectionError("--affected requires a Git repository")

    base = str(base or os.environ.get("DEVTOOL_AFFECTED_BASE", "") or "").strip()
    head = str(head or os.environ.get("DEVTOOL_AFFECTED_HEAD", "") or "").strip()
    ordered: list[str] = []
    seen: set[str] = set()

    def add(payload: bytes) -> None:
        for value in _zpaths(payload):
            if value not in seen:
                seen.add(value)
                ordered.append(value)

    if base:
        endpoint = head or "HEAD"
        add(_git(repo, "diff", "--name-only", "-z", f"{base}...{endpoint}"))
        return tuple(ordered)
    if head:
        # A head without a base compares the selected revision to its first
        # parent. This is explicit rather than guessing a branch name.
        add(_git(repo, "diff", "--name-only", "-z", f"{head}^", head))
        return tuple(ordered)

    add(_git(repo, "diff", "--name-only", "-z"))
    add(_git(repo, "diff", "--cached", "--name-only", "-z"))
    add(_git(repo, "ls-files", "--others", "--exclude-standard", "-z"))
    return tuple(ordered)


def _repo_relative_root(repo_root: Path, value: str) -> str | None:
    if not str(value or "").strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve(strict=False)
    if path.is_file():
        path = path.parent
    try:
        relative = path.relative_to(repo_root).as_posix()
    except ValueError:
        return None
    return "" if relative == "." else relative.rstrip("/")


def _target_root(repo_root: Path, target: TargetConfig) -> str | None:
    for value in (target.root, target.cwd, target.build_target, target.restore_target, target.test_target):
        root = _repo_relative_root(repo_root, value)
        if root is not None:
            return root
    return None


def _pattern_from_base(repo_root: Path, base: str | None, pattern: str) -> str | None:
    raw = str(pattern or "").strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("/"):
        normalized = posixpath.normpath(raw.lstrip("/"))
    else:
        prefix = str(base or "").strip("/")
        normalized = posixpath.normpath(posixpath.join(prefix, raw)) if prefix else posixpath.normpath(raw)
    if normalized == ".." or normalized.startswith("../"):
        # Lexical normalization escaped the repository boundary.
        return None
    return normalized.lstrip("./")


def _matches_pattern(path: str, pattern: str) -> bool:
    normalized = path.strip("/")
    pat = pattern.strip("/")
    if not pat:
        return normalized == ""
    # fnmatch handles ** as the broad recursive form users expect here. Also
    # treat a literal directory pattern as owning its descendants.
    return fnmatch.fnmatchcase(normalized, pat) or normalized == pat or normalized.startswith(pat.rstrip("/") + "/")


def _direct_job_names(target: TargetConfig) -> tuple[str, ...]:
    names: list[str] = []
    for workflow in target.workflows.values():
        for step in workflow.steps:
            if step.kind == "job" and step.name not in names:
                names.append(step.name)
    return tuple(names)


def target_dependencies(config: ProjectConfig) -> dict[str, tuple[str, ...]]:
    """Return direct target -> target dependencies declared by workflows."""
    dependencies: dict[str, list[str]] = {name: [] for name in config.targets}
    for name, target in config.targets.items():
        for workflow in target.workflows.values():
            for step in workflow.steps:
                if step.kind == "target" and step.name in config.targets and step.name not in dependencies[name]:
                    dependencies[name].append(step.name)
    return {name: tuple(values) for name, values in dependencies.items()}


def reverse_target_dependencies(config: ProjectConfig) -> dict[str, tuple[str, ...]]:
    reverse: dict[str, list[str]] = {name: [] for name in config.targets}
    for consumer, dependencies in target_dependencies(config).items():
        for dependency in dependencies:
            if consumer not in reverse[dependency]:
                reverse[dependency].append(consumer)
    return {name: tuple(values) for name, values in reverse.items()}


def _target_match_reasons(config: ProjectConfig, target_name: str, path: str) -> list[AffectedReason]:
    target = config.targets[target_name]
    repo = config.repo_root.resolve()
    reasons: list[AffectedReason] = []
    root = _target_root(repo, target)
    if root is not None and (not root or path == root or path.startswith(root + "/")):
        reasons.append(AffectedReason("target-root", path, f"path is inside target root {root or '.'}", source=root or "."))

    for raw in target.inputs:
        pattern = _pattern_from_base(repo, root, raw)
        if pattern and _matches_pattern(path, pattern):
            reasons.append(AffectedReason("target-input", path, f"matches target input {raw}", source=raw))

    for job_name in _direct_job_names(target):
        job = config.jobs.get(job_name)
        if job is None:
            continue
        job_root = _repo_relative_root(repo, job.root) if job.root else ""
        for raw in job.inputs:
            pattern = _pattern_from_base(repo, job_root, raw)
            if pattern and _matches_pattern(path, pattern):
                reasons.append(AffectedReason("job-input", path, f"matches input {raw} for job {job_name}", source=job_name))
    return reasons


def analyze_affected_targets(
    config: ProjectConfig,
    *,
    changed_paths: Sequence[str] | None = None,
    candidate_targets: Iterable[str] | None = None,
    base: str = "",
    head: str = "",
) -> AffectedSelection:
    """Compute direct and transitive affected workspace targets."""

    all_targets = tuple(config.targets)
    if not all_targets:
        raise AffectedSelectionError("--affected requires configured [targets.*] entries")

    candidates = tuple(dict.fromkeys(str(item).strip() for item in (candidate_targets or all_targets) if str(item).strip()))
    unknown = [name for name in candidates if name not in config.targets]
    if unknown:
        raise AffectedSelectionError(f"Unknown affected candidate target(s): {', '.join(unknown)}")

    if changed_paths is None:
        changes = git_changed_paths(config.repo_root, base=base, head=head)
        source = "git-range" if (base or head or os.environ.get("DEVTOOL_AFFECTED_BASE") or os.environ.get("DEVTOOL_AFFECTED_HEAD")) else "working-tree"
    else:
        source = "provided"
        changes = tuple(
            dict.fromkeys(
                PurePosixPath(str(item).replace("\\", "/").lstrip("./")).as_posix()
                for item in changed_paths
                if str(item).strip()
            )
        )

    direct: set[str] = set()
    reasons: dict[str, list[AffectedReason]] = {name: [] for name in all_targets}
    uncertain: list[str] = []

    for path in changes:
        matched: list[str] = []
        for target_name in all_targets:
            target_reasons = _target_match_reasons(config, target_name, path)
            if target_reasons:
                matched.append(target_name)
                direct.add(target_name)
                reasons[target_name].extend(target_reasons)
        if not matched:
            uncertain.append(path)

    # Unknown/shared ownership is deliberately conservative, but only across
    # the caller's candidate scope (e.g. --group frontend --affected).
    if uncertain:
        for target_name in candidates:
            direct.add(target_name)
            for path in uncertain:
                reasons[target_name].append(
                    AffectedReason(
                        "uncertain-workspace",
                        path,
                        "path has no configured owner; conservatively selecting candidate target",
                        source="workspace",
                    )
                )

    reverse = reverse_target_dependencies(config)
    affected = set(direct)
    queue = list(direct)
    while queue:
        dependency = queue.pop(0)
        for consumer in reverse.get(dependency, ()):
            if consumer in affected:
                continue
            affected.add(consumer)
            reasons[consumer].append(
                AffectedReason(
                    "dependency",
                    "",
                    f"depends on affected target {dependency}",
                    source=dependency,
                )
            )
            queue.append(consumer)

    # Preserve repository target declaration order and apply candidate filtering
    # only after dependency propagation so a dependency outside a group can
    # still explain why an in-group consumer is affected.
    selected = tuple(name for name in all_targets if name in affected and name in candidates)
    direct_selected = tuple(name for name in all_targets if name in direct and name in candidates)
    frozen_reasons = {
        name: tuple(reasons[name])
        for name in selected
    }
    return AffectedSelection(
        targets=selected,
        changed_paths=tuple(changes),
        uncertain_paths=tuple(uncertain),
        direct_targets=direct_selected,
        reasons=frozen_reasons,
        candidates=candidates,
        base=str(base or os.environ.get("DEVTOOL_AFFECTED_BASE", "") or "").strip(),
        head=str(head or os.environ.get("DEVTOOL_AFFECTED_HEAD", "") or "").strip(),
        source=source,
    )
