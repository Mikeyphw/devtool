from __future__ import annotations

"""GIT-FRX-04 commit-policy safety and evidence helpers.

This module is intentionally policy-focused.  Artifact transactions own recovery
refs/worktrees, while runners own staging/commit execution.  These helpers make
history-rewrite decisions explicit and machine-readable without ever pushing.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

from .git_state import GitStateError, collect_git_state

COMMIT_MODES = ("new", "amend", "fixup", "none")
DEVTOOL_NOTES_REF = "refs/notes/devtool"


class GitCommitPolicyError(RuntimeError):
    """Raised when a requested commit policy is unsafe or cannot be proven."""


@dataclass(frozen=True, slots=True)
class CommitPolicyEvidence:
    mode: str
    head: str
    fixup_target: str = ""
    fixup_target_subject: str = ""
    merge_commit: bool = False
    published_refs: tuple[str, ...] = ()
    signing_required: bool = False
    backup_ref: str = ""
    backup_sha: str = ""
    supersedes_commit: str = ""
    allow_merge_amend: bool = False
    allow_published_rewrite: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": self.mode,
            "head": self.head,
            "fixup_target": self.fixup_target,
            "fixup_target_subject": self.fixup_target_subject,
            "merge_commit": self.merge_commit,
            "published_refs": list(self.published_refs),
            "published": bool(self.published_refs),
            "signing_required": self.signing_required,
            "backup_ref": self.backup_ref,
            "backup_sha": self.backup_sha,
            "supersedes_commit": self.supersedes_commit,
            "allow_merge_amend": self.allow_merge_amend,
            "allow_published_rewrite": self.allow_published_rewrite,
            "automatic_push": False,
        }


def _git(repo_root: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitCommitPolicyError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _require(proc: subprocess.CompletedProcess[str], *, context: str) -> str:
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    detail = (proc.stderr or proc.stdout or "Git command failed").strip()
    raise GitCommitPolicyError(f"{context}: {detail}")


def resolve_commit(root: Path, revision: str) -> str:
    rev = str(revision or "").strip()
    if not rev:
        raise GitCommitPolicyError("A Git revision is required.")
    return _require(_git(root, "rev-parse", "--verify", f"{rev}^{{commit}}"), context=f"Unable to resolve commit {rev!r}")


def commit_subject(root: Path, revision: str) -> str:
    sha = resolve_commit(root, revision)
    return _require(_git(root, "show", "-s", "--format=%s", sha), context=f"Unable to read subject for {sha}")


def commit_parent_count(root: Path, revision: str = "HEAD") -> int:
    sha = resolve_commit(root, revision)
    raw = _require(_git(root, "rev-list", "--parents", "-n", "1", sha), context=f"Unable to inspect parents for {sha}")
    return max(0, len(raw.split()) - 1)


def signing_policy(root: Path) -> dict[str, Any]:
    proc = _git(root, "config", "--bool", "--get", "commit.gpgSign")
    if proc.returncode == 1:
        configured = False
        required = False
        raw = ""
    elif proc.returncode == 0:
        configured = True
        raw = (proc.stdout or "").strip().lower()
        required = raw in {"true", "yes", "on", "1"}
    else:
        detail = (proc.stderr or proc.stdout or "Unable to inspect commit.gpgSign").strip()
        raise GitCommitPolicyError(f"Unable to inspect Git signing policy: {detail}")
    key_proc = _git(root, "config", "--get", "user.signingKey")
    key = (key_proc.stdout or "").strip() if key_proc.returncode == 0 else ""
    return {
        "configured": configured,
        "required": required,
        "raw": raw,
        "signing_key_configured": bool(key),
        "signing_key": key,
    }


def protected_refs_containing(root: Path, revision: str = "HEAD") -> tuple[str, ...]:
    sha = resolve_commit(root, revision)
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        raise GitCommitPolicyError(f"Unable to collect authoritative Git state for published-history guard: {exc}") from exc

    candidates: set[str] = set()
    upstream = str(state.upstream.get("name") or "").strip()
    if upstream:
        candidates.add(upstream)
    for item in state.protected_remote_refs:
        name = str(item.name or "").strip()
        if name:
            candidates.add(name)

    published: list[str] = []
    for ref in sorted(candidates):
        check = _git(root, "merge-base", "--is-ancestor", sha, ref)
        if check.returncode == 0:
            published.append(ref)
        elif check.returncode == 1:
            continue
        else:
            detail = (check.stderr or check.stdout or "merge-base failed").strip()
            raise GitCommitPolicyError(f"Unable to verify published-history reachability for {ref}: {detail}")
    return tuple(published)


def _transaction_backup(transaction: Mapping[str, Any] | None, *, head: str) -> tuple[str, str]:
    if not isinstance(transaction, Mapping):
        return "", ""
    recovery = transaction.get("git_recovery")
    if not isinstance(recovery, Mapping):
        return "", ""
    rec = recovery.get("recovery")
    if not isinstance(rec, Mapping):
        return "", ""
    refs = rec.get("refs")
    if not isinstance(refs, Sequence):
        return "", ""
    for item in refs:
        if not isinstance(item, Mapping) or str(item.get("role") or "") != "backup-head":
            continue
        ref = str(item.get("ref") or "").strip()
        sha = str(item.get("sha") or "").strip()
        if ref and sha == head:
            return ref, sha
    return "", ""


def _verify_backup_ref(root: Path, ref: str, expected: str) -> None:
    if not ref or not expected:
        raise GitCommitPolicyError("Amend requires a verified Devtool backup-head recovery ref before history rewrite.")
    actual = _require(_git(root, "rev-parse", "--verify", "--quiet", ref), context=f"Unable to verify amend backup ref {ref}")
    if actual != expected:
        raise GitCommitPolicyError(f"Amend backup ref drift: {ref} -> {actual}, expected {expected}")


def preflight_commit_policy(
    repo_root: str | Path,
    *,
    mode: str,
    transaction: Mapping[str, Any] | None = None,
    fixup_target: str = "",
    allow_merge_amend: bool = False,
    allow_published_rewrite: bool = False,
) -> CommitPolicyEvidence:
    root = Path(repo_root).resolve()
    selected = str(mode or "none").strip().lower() or "none"
    if selected not in COMMIT_MODES:
        raise GitCommitPolicyError(f"Unsupported commit mode: {selected!r}")
    signing = signing_policy(root)
    if selected == "none":
        head_proc = _git(root, "rev-parse", "--verify", "HEAD")
        return CommitPolicyEvidence(selected, (head_proc.stdout or "").strip() if head_proc.returncode == 0 else "", signing_required=bool(signing["required"]))

    if selected == "new":
        head_proc = _git(root, "rev-parse", "--verify", "HEAD")
        head = (head_proc.stdout or "").strip() if head_proc.returncode == 0 else ""
        return CommitPolicyEvidence(selected, head, signing_required=bool(signing["required"]))

    head = resolve_commit(root, "HEAD")
    if selected == "fixup":
        target = resolve_commit(root, fixup_target or "HEAD")
        return CommitPolicyEvidence(
            selected,
            head,
            fixup_target=target,
            fixup_target_subject=commit_subject(root, target),
            signing_required=bool(signing["required"]),
        )

    # amend
    backup_ref, backup_sha = _transaction_backup(transaction, head=head)
    _verify_backup_ref(root, backup_ref, head)
    is_merge = commit_parent_count(root, head) > 1
    if is_merge and not allow_merge_amend:
        raise GitCommitPolicyError(
            "REFUSED: HEAD is a merge commit. Use --allow-merge-amend to amend it explicitly."
        )
    published = protected_refs_containing(root, head)
    if published and not allow_published_rewrite:
        refs = ", ".join(published)
        raise GitCommitPolicyError(
            "REFUSED: HEAD is reachable from a protected upstream/remote ref"
            + (f" ({refs})" if refs else "")
            + ". Use --commit-mode fixup, or explicitly use --allow-published-rewrite. "
              "No push will be performed automatically."
        )
    return CommitPolicyEvidence(
        selected,
        head,
        merge_commit=is_merge,
        published_refs=published,
        signing_required=bool(signing["required"]),
        backup_ref=backup_ref,
        backup_sha=backup_sha,
        supersedes_commit=head,
        allow_merge_amend=allow_merge_amend,
        allow_published_rewrite=allow_published_rewrite,
    )


def migrate_devtool_note(repo_root: str | Path, old_commit: str, new_commit: str) -> dict[str, Any]:
    """Copy existing Devtool note evidence to an amended commit, if present.

    The old note is deliberately retained; this preserves supersession evidence
    and avoids destructive note rewrites before GIT-FRX-11 makes notes a larger
    provenance subsystem.
    """

    root = Path(repo_root).resolve()
    old_sha = resolve_commit(root, old_commit)
    new_sha = resolve_commit(root, new_commit)
    show = _git(root, "notes", f"--ref={DEVTOOL_NOTES_REF}", "show", old_sha)
    if show.returncode == 1:
        return {"migrated": False, "source": old_sha, "destination": new_sha, "reason": "no-source-note"}
    if show.returncode != 0:
        detail = (show.stderr or show.stdout or "Unable to inspect Devtool note").strip()
        raise GitCommitPolicyError(f"Unable to inspect Devtool note for {old_sha}: {detail}")
    text = show.stdout or ""
    add = _git(root, "notes", f"--ref={DEVTOOL_NOTES_REF}", "add", "-f", "-F", "-", new_sha, input_text=text)
    if add.returncode != 0:
        detail = (add.stderr or add.stdout or "Unable to migrate Devtool note").strip()
        raise GitCommitPolicyError(f"Unable to migrate Devtool note to {new_sha}: {detail}")
    return {"migrated": True, "source": old_sha, "destination": new_sha, "notes_ref": DEVTOOL_NOTES_REF}


def probe_commit_policy(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    head_proc = _git(root, "rev-parse", "--verify", "HEAD")
    head = (head_proc.stdout or "").strip() if head_proc.returncode == 0 else ""
    payload: dict[str, Any] = {
        "schema_version": 1,
        "repository_root": str(root),
        "head": head,
        "signing": signing_policy(root),
        "automatic_push": False,
        "commit_modes": list(COMMIT_MODES),
    }
    if head:
        payload["merge_commit"] = commit_parent_count(root, head) > 1
        payload["protected_refs_containing_head"] = list(protected_refs_containing(root, head))
    else:
        payload["merge_commit"] = False
        payload["protected_refs_containing_head"] = []
    return payload


def _fixture() -> dict[str, Any]:
    """Create a disposable published-history fixture for native roadmap probing."""
    with tempfile.TemporaryDirectory(prefix="devtool-commit-policy-fixture-") as tmp:
        base = Path(tmp)
        remote = base / "remote.git"
        repo = base / "repo"
        _require(_git(base, "init", "--bare", "-q", str(remote)), context="Unable to create fixture remote")
        repo.mkdir()
        _require(_git(repo, "init", "-q"), context="Unable to initialize fixture repository")
        _require(_git(repo, "config", "user.name", "Devtool Fixture"), context="Unable to configure fixture user")
        _require(_git(repo, "config", "user.email", "fixture@example.invalid"), context="Unable to configure fixture email")
        (repo / "fixture.txt").write_text("fixture\n", encoding="utf-8")
        _require(_git(repo, "add", "fixture.txt"), context="Unable to stage fixture")
        _require(_git(repo, "commit", "-qm", "fixture baseline"), context="Unable to commit fixture")
        _require(_git(repo, "remote", "add", "origin", str(remote)), context="Unable to add fixture remote")
        branch = _require(_git(repo, "branch", "--show-current"), context="Unable to inspect fixture branch")
        _require(_git(repo, "push", "-q", "-u", "origin", branch), context="Unable to publish fixture HEAD")
        payload = probe_commit_policy(repo)
        payload["fixture"] = True
        payload["published"] = bool(payload.get("protected_refs_containing_head"))
        return payload


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m devtool.git_commit_policy")
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("--repo", default=".")
    sub.add_parser("fixture")
    args = parser.parse_args(argv)
    if args.command == "probe":
        payload = probe_commit_policy(Path(args.repo))
    else:
        payload = _fixture()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
