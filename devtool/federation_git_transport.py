from __future__ import annotations

"""GIT-FRX-16 Git-bundle source transport for federated remote children.

The transport is intentionally separate from FRX-Y11 source management. Y11
owns local repository bootstrap/sync from a frozen lock; this module moves the
already-frozen Git object closure to a managed remote execution workspace.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from typing import Any, Mapping

from .federation_lock import git_remote_locator
from .git_state import GitStateError, collect_git_state

SOURCE_TRANSPORTS = frozenset({"auto", "git-bundle", "rsync"})


class FederationGitTransportError(ValueError):
    pass


def normalize_source_transport(value: object) -> str:
    text = str(value or "auto").strip().lower().replace("_", "-") or "auto"
    aliases = {"bundle": "git-bundle", "gitbundle": "git-bundle", "sync": "rsync"}
    text = aliases.get(text, text)
    if text not in SOURCE_TRANSPORTS:
        raise FederationGitTransportError(
            f"Federation source transport must be one of auto, git-bundle, rsync; got {text!r}"
        )
    return text


def _safe(value: object, fallback: str = "child") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return text[:96] or fallback


def _git(root: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], text=True, capture_output=True,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FederationGitTransportError(f"git {' '.join(args)} failed for {root}: {exc}") from exc


def _require(root: Path, *args: str, timeout: float = 30.0) -> str:
    result = _git(root, *args, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Git command failed").strip()
        raise FederationGitTransportError(f"git {' '.join(args)} failed for {root}: {detail}")
    return result.stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _config_digest(root: Path) -> str:
    path = root / ".devtool.toml"
    if not path.is_file():
        return ""
    return _sha256_file(path)


def _lineage_git(lineage: Mapping[str, Any]) -> dict[str, Any]:
    raw = lineage.get("git_lineage")
    return {str(k): v for k, v in raw.items()} if isinstance(raw, Mapping) else {}


@dataclass(frozen=True, slots=True)
class FederationGitSourcePlan:
    requested_transport: str
    effective_transport: str
    eligible: bool
    reason: str
    repository: str
    parent_run_id: str
    parent_node_id: str
    child_run_id: str
    federation_ref: str
    start_commit: str
    config_sha256: str
    git_remote_sha256: str
    remote_locator: str
    remote_locator_digest: str
    source_tree: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "federation-git-source-plan",
            "requested_transport": self.requested_transport,
            "effective_transport": self.effective_transport,
            "eligible": self.eligible,
            "reason": self.reason,
            "repository": self.repository,
            "parent_run_id": self.parent_run_id,
            "parent_node_id": self.parent_node_id,
            "child_run_id": self.child_run_id,
            "federation_ref": self.federation_ref,
            "start_commit": self.start_commit,
            "config_sha256": self.config_sha256,
            "git_remote_sha256": self.git_remote_sha256,
            "remote_locator": self.remote_locator,
            "remote_locator_digest": self.remote_locator_digest,
            "source_tree": self.source_tree,
        }


@dataclass(frozen=True, slots=True)
class FederationGitBundle:
    path: Path
    digest: str
    size: int
    transport_ref: str
    start_commit: str
    source_tree: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.digest,
            "size": self.size,
            "transport_ref": self.transport_ref,
            "start_commit": self.start_commit,
            "source_tree": self.source_tree,
        }


def plan_federated_git_source(
    repo_root: str | Path,
    *,
    lineage: Mapping[str, Any] | None,
    requested_transport: object = "auto",
) -> FederationGitSourcePlan:
    root = Path(repo_root).expanduser().resolve()
    requested = normalize_source_transport(requested_transport)
    lineage = {str(k): v for k, v in (lineage or {}).items()}
    is_child = str(lineage.get("relationship") or "").strip().lower() == "child"
    git_lineage = _lineage_git(lineage)
    repository = str(lineage.get("repository") or git_lineage.get("repository") or "").strip()
    start_commit = str(git_lineage.get("start_commit") or "").strip()
    remote_locator = str(git_lineage.get("remote_locator") or "").strip()
    remote_locator_digest = str(git_lineage.get("remote_locator_digest") or "").strip()
    frozen_config = str(git_lineage.get("config_digest") or "").strip()
    source_tree = str(git_lineage.get("head_tree") or "").strip()

    if not is_child:
        if requested == "git-bundle":
            raise FederationGitTransportError(
                "Strict git-bundle source transport requires a frozen federated child execution"
            )
        return FederationGitSourcePlan(
            requested, "rsync", False, "not a federated child execution", repository,
            str(lineage.get("parent_run_id") or ""), str(lineage.get("parent_node_id") or ""),
            str(lineage.get("child_run_id") or ""), str(lineage.get("federation_ref") or ""),
            start_commit, frozen_config, "", remote_locator, remote_locator_digest, source_tree,
        )

    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        eligible = False
        reason = f"Git state unavailable: {exc}"
        state = None
    else:
        if not state.available:
            eligible = False; reason = "repository is not Git-capable"
        elif state.dirty:
            eligible = False; reason = "working tree is dirty; bundle transport requires a clean/synthetic commit"
        elif state.shallow or state.partial_clone:
            eligible = False; reason = "shallow/partial clones may not contain the complete frozen object closure"
        elif not start_commit:
            eligible = False; reason = "federation lineage has no frozen start commit"
        elif str(state.head.get("sha") or "") != start_commit:
            eligible = False; reason = "current HEAD does not match the frozen federation start commit"
        elif _git(root, "cat-file", "-e", f"{start_commit}^{{commit}}").returncode != 0:
            eligible = False; reason = "frozen start commit is unavailable in the local object database"
        else:
            actual_tree = _require(root, "rev-parse", f"{start_commit}^{{tree}}")
            if source_tree and actual_tree != source_tree:
                eligible = False; reason = "frozen start tree does not match the local commit tree"
            else:
                source_tree = source_tree or actual_tree
                eligible = True; reason = "clean frozen Git commit can use bundle transport"

    actual_config = _config_digest(root)
    if frozen_config and actual_config != frozen_config:
        eligible = False; reason = "frozen .devtool.toml digest does not match the local child"
    config_sha = frozen_config or actual_config

    locator = git_remote_locator(root)
    if not remote_locator:
        remote_locator = locator
    actual_remote_digest = hashlib.sha256(locator.encode("utf-8")).hexdigest() if locator else ""
    if remote_locator_digest and actual_remote_digest and remote_locator_digest != actual_remote_digest:
        eligible = False; reason = "frozen normalized Git remote identity does not match the local child"
    if not remote_locator_digest and remote_locator:
        remote_locator_digest = hashlib.sha256(remote_locator.encode("utf-8")).hexdigest()
    git_remote_sha = remote_locator_digest or actual_remote_digest

    if requested == "rsync":
        effective = "rsync"
    elif eligible:
        effective = "git-bundle"
    elif requested == "git-bundle":
        raise FederationGitTransportError(
            "Strict git-bundle source transport cannot be used: " + reason
        )
    else:
        effective = "rsync"

    return FederationGitSourcePlan(
        requested, effective, eligible, reason, repository,
        str(lineage.get("parent_run_id") or ""), str(lineage.get("parent_node_id") or ""),
        str(lineage.get("child_run_id") or ""), str(lineage.get("federation_ref") or ""),
        start_commit, config_sha, git_remote_sha, remote_locator, remote_locator_digest, source_tree,
    )


def create_federated_git_bundle(
    repo_root: str | Path,
    plan: FederationGitSourcePlan,
    *,
    destination: str | Path | None = None,
) -> FederationGitBundle:
    if plan.effective_transport != "git-bundle" or not plan.eligible:
        raise FederationGitTransportError("Git bundle creation requires an eligible git-bundle source plan")
    root = Path(repo_root).expanduser().resolve()
    if destination is None:
        handle = tempfile.NamedTemporaryFile(prefix="devtool-frx16-", suffix=".bundle", delete=False)
        handle.close()
        path = Path(handle.name)
        path.unlink(missing_ok=True)
    else:
        path = Path(destination).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)

    token = hashlib.sha256(
        f"{plan.parent_run_id}\n{plan.child_run_id}\n{plan.repository}\n{plan.start_commit}".encode("utf-8")
    ).hexdigest()[:24]
    ref = f"refs/devtool/transport/{_safe(plan.repository)}/{token}"
    _require(root, "update-ref", ref, plan.start_commit)
    try:
        created = _git(root, "bundle", "create", str(path), ref, timeout=120.0)
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "git bundle create failed").strip()
            raise FederationGitTransportError(f"Unable to create Git bundle: {detail}")
    finally:
        _git(root, "update-ref", "-d", ref)
    verified = subprocess.run(
        ["git", "-C", str(root), "bundle", "verify", str(path)],
        text=True, capture_output=True, check=False, timeout=60,
    )
    if verified.returncode != 0:
        detail = (verified.stderr or verified.stdout or "git bundle verify failed").strip()
        path.unlink(missing_ok=True)
        raise FederationGitTransportError(f"Created Git bundle failed verification: {detail}")
    heads = subprocess.run(
        ["git", "bundle", "list-heads", str(path)], text=True, capture_output=True,
        check=False, timeout=30,
    )
    if heads.returncode != 0 or not any(
        line.split(maxsplit=1)[0] == plan.start_commit for line in heads.stdout.splitlines() if line.strip()
    ):
        path.unlink(missing_ok=True)
        raise FederationGitTransportError("Created Git bundle does not advertise the frozen start commit")
    return FederationGitBundle(
        path=path,
        digest=_sha256_file(path),
        size=path.stat().st_size,
        transport_ref=ref,
        start_commit=plan.start_commit,
        source_tree=plan.source_tree,
    )


def _home_assignment(name: str, value: str) -> str:
    raw = str(value or "").strip()
    if raw in {"~", "$HOME"}:
        return f'{name}="$HOME"'
    for prefix in ("~/", "$HOME/"):
        if raw.startswith(prefix):
            return f'{name}="$HOME"/' + shlex.quote(raw[len(prefix):])
    return f"{name}=" + shlex.quote(raw)


def build_remote_bundle_materialization_shell(
    plan: FederationGitSourcePlan,
    bundle: FederationGitBundle,
    *,
    remote_root: str,
    remote_bundle: str,
) -> str:
    if plan.effective_transport != "git-bundle":
        raise FederationGitTransportError("Remote bundle materialization requires git-bundle transport")
    if not str(remote_root or "").strip() or not str(remote_bundle or "").strip():
        raise FederationGitTransportError("Remote bundle materialization requires root and bundle paths")
    qcommit = shlex.quote(plan.start_commit)
    qtree = shlex.quote(plan.source_tree)
    qdigest = shlex.quote(bundle.digest)
    qconfig = shlex.quote(plan.config_sha256)
    qremote = shlex.quote(plan.git_remote_sha256)
    qlocator = shlex.quote(plan.remote_locator)
    qrepo = shlex.quote(plan.repository)
    qparent = shlex.quote(plan.parent_run_id)
    qnode = shlex.quote(plan.parent_node_id)
    qchild = shlex.quote(plan.child_run_id)
    qref = shlex.quote(plan.federation_ref)
    parts = [
        "set -eu",
        _home_assignment("frx_root", remote_root),
        _home_assignment("frx_bundle", remote_bundle),
        f"frx_commit={qcommit}", f"frx_tree={qtree}", f"frx_bundle_sha={qdigest}",
        f"frx_config_sha={qconfig}", f"frx_remote_sha={qremote}", f"frx_remote_locator={qlocator}",
        f"frx_repository={qrepo}", f"frx_parent_run={qparent}", f"frx_parent_node={qnode}",
        f"frx_child_run={qchild}", f"frx_federation_ref={qref}",
        'command -v git >/dev/null 2>&1 || { echo "Devtool federation: git is required for git-bundle transport" >&2; exit 74; }',
        'command -v sha256sum >/dev/null 2>&1 || { echo "Devtool federation: sha256sum is required for git-bundle transport" >&2; exit 74; }',
        '[ -f "$frx_bundle" ] || { echo "Devtool federation: transferred Git bundle is missing" >&2; exit 74; }',
        'frx_actual_bundle_sha="$(sha256sum "$frx_bundle")"; frx_actual_bundle_sha="${frx_actual_bundle_sha%% *}"',
        '[ "$frx_actual_bundle_sha" = "$frx_bundle_sha" ] || { echo "Devtool federation: transferred Git bundle digest mismatch" >&2; exit 74; }',
        'mkdir -p "$frx_root"',
        'if [ ! -d "$frx_root/.git" ]; then git -C "$frx_root" init -q; fi',
        'git -C "$frx_root" rev-parse --git-dir >/dev/null 2>&1 || { echo "Devtool federation: managed remote root is not a Git worktree" >&2; exit 74; }',
        'git -C "$frx_root" bundle verify "$frx_bundle" >/dev/null 2>&1 || { echo "Devtool federation: remote Git bundle verification failed" >&2; exit 74; }',
        'mkdir -p "$frx_root/.git/info"',
        'printf "%s\\n" "/.devtool-workspace-id" "/.devtool-remote-workspace.json" "/.devtool-sync-state.json" "/.devtool-federation-attestation.json" >> "$frx_root/.git/info/exclude"',
        'git -C "$frx_root" fetch -q "$frx_bundle" "refs/devtool/transport/*:refs/devtool/transport/incoming/*"',
        'git -C "$frx_root" cat-file -e "$frx_commit^{commit}" || { echo "Devtool federation: expected start commit missing after bundle fetch" >&2; exit 74; }',
        'git -C "$frx_root" reset --hard -q "$frx_commit"',
        'git -C "$frx_root" checkout --detach -q -f "$frx_commit"',
        'git -C "$frx_root" clean -fdx -e /.devtool-workspace-id -e /.devtool-remote-workspace.json -e /.devtool-sync-state.json -e /.devtool-federation-attestation.json >/dev/null',
        'frx_actual_head="$(git -C "$frx_root" rev-parse HEAD)"',
        '[ "$frx_actual_head" = "$frx_commit" ] || { echo "Devtool federation: remote checkout commit mismatch" >&2; exit 74; }',
    ]
    if plan.source_tree:
        parts.extend([
            'frx_actual_tree="$(git -C "$frx_root" rev-parse HEAD^{tree})"',
            '[ "$frx_actual_tree" = "$frx_tree" ] || { echo "Devtool federation: remote checkout tree mismatch" >&2; exit 74; }',
        ])
    if plan.remote_locator:
        parts.append('git -C "$frx_root" remote remove origin >/dev/null 2>&1 || true')
        parts.append('git -C "$frx_root" remote add origin "$frx_remote_locator"')
    if plan.config_sha256:
        parts.extend([
            '[ -f "$frx_root/.devtool.toml" ] || { echo "Devtool federation: remote .devtool.toml is missing after bundle checkout" >&2; exit 74; }',
            'frx_actual_config="$(sha256sum "$frx_root/.devtool.toml")"; frx_actual_config="${frx_actual_config%% *}"',
            '[ "$frx_actual_config" = "$frx_config_sha" ] || { echo "Devtool federation: remote config digest mismatch after bundle checkout" >&2; exit 74; }',
        ])
    if plan.git_remote_sha256:
        parts.extend([
            'frx_origin="$(git -C "$frx_root" config --get remote.origin.url 2>/dev/null || true)"',
            '[ -n "$frx_origin" ] || { echo "Devtool federation: remote Git origin is missing after bundle checkout" >&2; exit 74; }',
            'frx_actual_remote="$(printf %s "$frx_origin" | sha256sum)"; frx_actual_remote="${frx_actual_remote%% *}"',
            '[ "$frx_actual_remote" = "$frx_remote_sha" ] || { echo "Devtool federation: normalized remote identity mismatch after bundle checkout" >&2; exit 74; }',
        ])
    parts.extend([
        'frx_attest="$frx_root/.devtool-federation-attestation.json"',
        'printf \'{"schema_version":1,"repository":"%s","parent_run_id":"%s","parent_node_id":"%s","child_run_id":"%s","federation_ref":"%s","start_commit":"%s","bundle_sha256":"%s"}\\n\' "$frx_repository" "$frx_parent_run" "$frx_parent_node" "$frx_child_run" "$frx_federation_ref" "$frx_commit" "$frx_bundle_sha" > "$frx_attest"',
        'rm -f -- "$frx_bundle"',
    ])
    return "; ".join(parts)


def bundle_transport_evidence(
    plan: FederationGitSourcePlan,
    bundle: FederationGitBundle | None,
    *,
    remote_root: str,
    remote_bundle: str,
    verified: bool,
) -> dict[str, Any]:
    payload = plan.as_dict()
    payload.update({
        "remote_root": str(remote_root or ""),
        "remote_bundle": str(remote_bundle or ""),
        "verified": bool(verified),
    })
    if bundle is not None:
        payload["bundle"] = {key: value for key, value in bundle.as_dict().items() if key != "path"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["evidence_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload




def fixture_payload() -> dict[str, Any]:
    """Exercise the GIT-FRX-16 source-transport contract without mutating the repo."""
    with tempfile.TemporaryDirectory(prefix="devtool-frx16-fixture-") as td:
        root = Path(td) / "source"
        root.mkdir()
        init = subprocess.run(["git", "init", "-q", str(root)], text=True, capture_output=True, check=False)
        if init.returncode != 0:
            raise FederationGitTransportError("GIT-FRX-16 fixture requires git")
        _require(root, "config", "user.email", "devtool@example.invalid")
        _require(root, "config", "user.name", "Devtool")
        (root / ".devtool.toml").write_text('[project]\nrunner = "command"\n', encoding="utf-8")
        (root / "fixture.txt").write_text("frx16\n", encoding="utf-8")
        _require(root, "add", ".")
        _require(root, "commit", "-qm", "fixture")
        _require(root, "remote", "add", "origin", "ssh://git@example.invalid/devtool/frx16.git")
        start_commit = _require(root, "rev-parse", "HEAD")
        source_tree = _require(root, "rev-parse", "HEAD^{tree}")
        locator = git_remote_locator(root)
        lineage = {
            "kind": "federation", "relationship": "child", "repository": "fixture",
            "parent_run_id": "fixture-parent", "parent_node_id": "fixture-node",
            "child_run_id": "fixture-child", "federation_ref": "fixture::devtool::verify",
            "git_lineage": {
                "start_commit": start_commit,
                "head_tree": source_tree,
                "config_digest": _config_digest(root),
                "remote_locator": locator,
                "remote_locator_digest": hashlib.sha256(locator.encode("utf-8")).hexdigest(),
            },
        }
        plan = plan_federated_git_source(root, lineage=lineage, requested_transport="git-bundle")
        bundle = create_federated_git_bundle(root, plan)
        try:
            heads = subprocess.run(
                ["git", "bundle", "list-heads", str(bundle.path)], text=True, capture_output=True,
                check=False, timeout=30,
            )
            advertised = heads.returncode == 0 and start_commit in heads.stdout
            return {
                "schema_version": 1,
                "kind": "git-frx16-git-bundle-remote-attestation-fixture",
                "roadmap_id": "GIT-FRX-16",
                "ok": bool(plan.eligible and advertised),
                "source_transport": plan.as_dict(),
                "bundle": {k: v for k, v in bundle.as_dict().items() if k != "path"},
                "private_transport_ref_cleaned": not bool(_require(root, "for-each-ref", "--format=%(refname)", "refs/devtool/transport")),
                "global_git_bisect_state": (root / ".git" / "BISECT_START").exists(),
            }
        finally:
            bundle.path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m devtool.federation_git_transport")
    parser.add_argument("command", choices=("fixture",))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = fixture_payload()
    except FederationGitTransportError as exc:
        print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(payload, indent=2 if args.json else None, sort_keys=True))
    return 0 if payload.get("ok") else 1


__all__ = [
    "SOURCE_TRANSPORTS",
    "FederationGitBundle",
    "FederationGitSourcePlan",
    "FederationGitTransportError",
    "build_remote_bundle_materialization_shell",
    "bundle_transport_evidence",
    "create_federated_git_bundle",
    "normalize_source_transport",
    "plan_federated_git_source",
    "fixture_payload",
]


if __name__ == "__main__":
    raise SystemExit(main())
