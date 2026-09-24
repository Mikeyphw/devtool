from __future__ import annotations

"""Canonical repository identity for federated/nested Devtool execution.

FRX-01 deliberately does not discover or orchestrate child repositories.  It
establishes the repository identity primitive that later federation overlays
can carry across plans, EXO lineage, transports, artifacts, and UI surfaces.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import Any, Mapping

REPOSITORY_IDENTITY_SCHEMA_VERSION = 1


class RepositoryIdentityError(ValueError):
    """Raised when a repository/config ownership contract is impossible."""


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    schema_version: int
    root: str
    fingerprint: str
    display_name: str
    requested_root: str = ""
    config_path: str = ""
    git_root: str = ""
    git_common_dir: str = ""
    git_common_fingerprint: str = ""
    git_remote_fingerprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "fingerprint": self.fingerprint,
            "display_name": self.display_name,
            "requested_root": self.requested_root,
            "config_path": self.config_path,
            "git_root": self.git_root,
            "git_common_dir": self.git_common_dir,
            "git_common_fingerprint": self.git_common_fingerprint,
            "git_remote_fingerprint": self.git_remote_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepositoryIdentity":
        return cls(
            schema_version=int(value.get("schema_version") or REPOSITORY_IDENTITY_SCHEMA_VERSION),
            root=str(value.get("root") or ""),
            fingerprint=str(value.get("fingerprint") or ""),
            display_name=str(value.get("display_name") or ""),
            requested_root=str(value.get("requested_root") or value.get("root") or ""),
            config_path=str(value.get("config_path") or ""),
            git_root=str(value.get("git_root") or ""),
            git_common_dir=str(value.get("git_common_dir") or ""),
            git_common_fingerprint=str(value.get("git_common_fingerprint") or ""),
            git_remote_fingerprint=str(value.get("git_remote_fingerprint") or ""),
        )


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def validate_repository_config_ownership(
    repo_root: str | Path,
    config_path: str | Path | None,
) -> tuple[Path, Path | None, Path]:
    """Resolve requested root, config, and canonical config-owning root.

    Devtool historically allows a repository/workspace config to govern deeper
    project roots.  That is a valid ownership relationship and must remain
    backward compatible.  What is unsafe is borrowing configuration from a
    descendant or unrelated repository.
    """

    requested_root = _canonical(repo_root)
    config = _canonical(config_path) if config_path else None
    owner_root = requested_root
    if config is not None:
        owner_root = config.parent
        try:
            requested_root.relative_to(owner_root)
        except ValueError as exc:
            raise RepositoryIdentityError(
                "Repository config ownership mismatch: "
                f"requested project root is {requested_root}, but config is owned by "
                f"{owner_root} ({config}). The config owner must be the requested root "
                "or one of its ancestors."
            ) from exc
    return requested_root, config, owner_root


def _git_text(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_metadata(root: Path) -> tuple[str, str, str, str]:
    git_root_text = _git_text(root, "rev-parse", "--show-toplevel")
    if not git_root_text:
        return "", "", "", ""
    git_root = _canonical(git_root_text)

    common_text = _git_text(root, "rev-parse", "--git-common-dir")
    git_common = ""
    if common_text:
        common_path = Path(common_text).expanduser()
        if not common_path.is_absolute():
            common_path = git_root / common_path
        git_common = str(common_path.resolve(strict=False))

    # Remote URL is never exposed.  Its digest is only an additional identity
    # signal so the fingerprint is not derived from filesystem paths alone.
    common_fingerprint = ""
    if git_common:
        try:
            stat = Path(git_common).stat()
        except OSError:
            pass
        else:
            common_fingerprint = hashlib.sha256(
                f"{stat.st_dev}:{stat.st_ino}".encode("utf-8")
            ).hexdigest()

    remote = _git_text(root, "config", "--get", "remote.origin.url")
    remote_fingerprint = hashlib.sha256(remote.encode("utf-8")).hexdigest() if remote else ""
    return str(git_root), git_common, common_fingerprint, remote_fingerprint


def build_repository_identity(repo_root: str | Path, config_path: str | Path | None = None) -> RepositoryIdentity:
    requested_root, config, root = validate_repository_config_ownership(repo_root, config_path)
    git_root, git_common_dir, git_common_fingerprint, remote_fingerprint = _git_metadata(root)
    config_text = str(config) if config is not None else ""
    basis = "\n".join(
        [
            f"schema={REPOSITORY_IDENTITY_SCHEMA_VERSION}",
            f"root={root}",
            f"git_root={git_root}",
            f"git_common_dir={git_common_dir}",
            f"git_common_fingerprint={git_common_fingerprint}",
        ]
    )
    fingerprint = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return RepositoryIdentity(
        schema_version=REPOSITORY_IDENTITY_SCHEMA_VERSION,
        root=str(root),
        fingerprint=fingerprint,
        display_name=root.name or str(root),
        requested_root=str(requested_root),
        config_path=config_text,
        git_root=git_root,
        git_common_dir=git_common_dir,
        git_common_fingerprint=git_common_fingerprint,
        git_remote_fingerprint=remote_fingerprint,
    )


def same_repository_identity(left: RepositoryIdentity, right: RepositoryIdentity) -> bool:
    return bool(left.fingerprint and right.fingerprint and left.fingerprint == right.fingerprint)


def assert_repository_identity_matches(
    expected: RepositoryIdentity,
    actual: RepositoryIdentity,
    *,
    context: str = "repository",
) -> None:
    if same_repository_identity(expected, actual):
        return
    raise RepositoryIdentityError(
        f"{context} identity mismatch: expected {expected.fingerprint or '<missing>'} "
        f"at {expected.root}, got {actual.fingerprint or '<missing>'} at {actual.root}."
    )


__all__ = [
    "REPOSITORY_IDENTITY_SCHEMA_VERSION",
    "RepositoryIdentity",
    "RepositoryIdentityError",
    "assert_repository_identity_matches",
    "build_repository_identity",
    "same_repository_identity",
    "validate_repository_config_ownership",
]
