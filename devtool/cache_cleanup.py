from __future__ import annotations

"""Cleanup helpers for devtool-managed caches and transient state."""

import hashlib
import re
from pathlib import Path
import shutil
from typing import Iterable

from .xdg import cache_dir, data_dir, state_dir


def default_cache_root() -> Path:
    return cache_dir()


def default_state_root() -> Path:
    return state_dir()


def default_share_root() -> Path:
    return data_dir()


def repository_cache_key(repo_root: str | Path) -> str:
    """Return the stable repository identity used by disposable caches.

    Cache cleanup is repository-scoped.  A basename alone is not sufficient
    because two checkouts with the same directory name must not share a clean
    boundary, so include a short digest of the canonical repository path.
    """

    root = Path(repo_root).expanduser().resolve(strict=False)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", root.name or "repo").strip(".-") or "repo"
    digest = hashlib.sha256(str(root).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{slug}-{digest}"


def _safe_component(value: str, default: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return normalized or default


def _remove_path(path: Path, *, dry_run: bool) -> bool:
    if not path.exists():
        return False
    if dry_run:
        return True
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def cleanup_devtool_caches(
    *,
    cache_root: Path | None = None,
    state_root: Path | None = None,
    share_root: Path | None = None,
    repo_root: str | Path | None = None,
    backend: str = "",
    extra_cache_paths: Iterable[str | Path] = (),
    dry_run: bool = False,
) -> list[str]:
    """Remove only disposable Devtool cache state.

    Durable logs, transaction journals/archives, and active-operation markers
    are recovery evidence and are intentionally never removed by cache cleanup.
    ``state_root``/``share_root`` remain accepted for API compatibility but do
    not widen the deletion boundary.  Cleanup always requires a repository
    identity and removes only disposable cache paths owned by that repository
    and execution backend; there is no broad global-cache deletion mode.
    """

    cache_root = (cache_root or default_cache_root()).expanduser().resolve(strict=False)
    _ = state_root, share_root
    removed: list[str] = []
    if repo_root is None:
        raise ValueError("Repository-scoped cache cleanup requires repo_root")

    key = repository_cache_key(repo_root)
    safe_backend = _safe_component(backend, "host")
    candidates: list[Path] = [
        cache_root / "build" / key / safe_backend,
        cache_root / "host-termux" / "build" / key / safe_backend,
        cache_root / "python" / key / safe_backend,
        cache_root / "repos" / key / safe_backend,
    ]
    # Older Python plans used the neutral name ``host``.  Removing that path is
    # still repository-scoped and avoids leaving pre-OV-03 disposable state
    # behind when a desktop/native cleanup is requested.
    if safe_backend in {"desktop-linux", "native-termux"}:
        candidates.append(cache_root / "python" / key / "host")

    for raw in extra_cache_paths:
        path = Path(raw).expanduser().resolve(strict=False)
        # Extra paths come from Devtool's resolved build policy.  Refuse broad
        # roots defensively: cleanup may delete a leaf/subtree but never the
        # repository itself, its parent, or the global Devtool cache root.
        repo = Path(repo_root).expanduser().resolve(strict=False)
        if path in {repo, repo.parent, cache_root, cache_root.parent}:
            raise ValueError(f"Refusing unsafe cache cleanup root: {path}")
        # Resolved build-policy cache roots include both the canonical repo key
        # and execution backend.  Refuse a custom/global path that would erase
        # another repository or backend's disposable state.
        if key not in path.parts or safe_backend not in path.parts:
            raise ValueError(f"Refusing unscoped cache cleanup path: {path}")
        candidates.append(path)

    from .cleanup_lifecycle import (
        CleanupScope,
        GeneratedOutputDescriptor,
        apply_cleanup_plan,
        build_cleanup_ownership_view,
        compile_cleanup_plan,
    )

    seen: set[Path] = set()
    descriptors: list[GeneratedOutputDescriptor] = []
    existed: dict[str, bool] = {}
    for path in candidates:
        path = path.resolve(strict=False)
        if path in seen:
            continue
        seen.add(path)
        descriptor = GeneratedOutputDescriptor.create(
            path, owner="devtool-cache", provider="devtool", category="repository-cache",
            producer_id=f"cache:{key}:{safe_backend}", lifecycle="disposable", rebuild_cost="low",
            scope=CleanupScope.REPOSITORY_CACHE, destination="local",
            provenance={"repository_cache_key": key, "backend": safe_backend},
        )
        descriptors.append(descriptor)
        existed[descriptor.resource_id] = path.exists() or path.is_symlink()

    ownership = build_cleanup_ownership_view(
        repo_root, descriptors, policy_generation=f"cache:{key}:{safe_backend}:v1"
    )
    plan = compile_cleanup_plan(
        ownership, scope=CleanupScope.REPOSITORY_CACHE, destination="local"
    )
    receipt = apply_cleanup_plan(plan, ownership=ownership, dry_run=dry_run, confirm=True)
    by_id = {item.resource_id: item for item in receipt.results}
    for candidate in plan.candidates:
        result = by_id.get(candidate.resource_id)
        if not existed.get(candidate.resource_id, False) or result is None:
            continue
        if result.status in {"planned", "applied"}:
            removed.append(candidate.path)
    return removed
