from __future__ import annotations

"""GIT-FRX-17 federation-aware audit pass persistence and delta truth."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .federation_discovery import resolve_federated_repository
from .git_campaign import GitCampaignError, load_campaign
from .git_state import GitStateError, collect_git_state
from .xdg import state_dir

AUDIT_SCHEMA_VERSION = 1
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class GitAuditError(RuntimeError):
    pass


def _safe(value: str, fallback: str = "audit") -> str:
    clean = _SAFE.sub("-", str(value or "").strip()).strip(".-")
    return clean or fallback


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(root: Path, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], text=True, encoding="utf-8", errors="surrogateescape",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitAuditError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _require(root: Path, *args: str) -> str:
    proc = _git(root, *args)
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    detail = (proc.stderr or proc.stdout or "git command failed").strip()
    raise GitAuditError(detail)


def audit_root() -> Path:
    return state_dir() / "git-audits"


def campaign_audit_root(campaign_id: str) -> Path:
    return audit_root() / _safe(campaign_id, "campaign")


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _file_evidence(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.is_file():
        raise GitAuditError(f"Audit evidence does not exist or is not a file: {resolved}")
    data = resolved.read_bytes()
    return {"path": str(resolved), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _dirty_delta_identity(root: Path, state: Any) -> dict[str, Any]:
    # Git's binary diff gives a deterministic identity for tracked/index state.
    tracked = _git(root, "diff", "--binary", "HEAD")
    staged = _git(root, "diff", "--binary", "--cached", "HEAD")
    if tracked.returncode not in {0, 1} or staged.returncode not in {0, 1}:
        raise GitAuditError(f"Unable to capture dirty delta for {root}")
    untracked_rows: list[dict[str, Any]] = []
    for rel in sorted(str(item) for item in getattr(state, "untracked", ())):
        path = root / rel
        if path.is_file() and not path.is_symlink():
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            untracked_rows.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        else:
            untracked_rows.append({"path": rel, "sha256": "", "size": 0})
    payload = {
        "tracked_diff_sha256": hashlib.sha256((tracked.stdout or "").encode("utf-8", "surrogatepass")).hexdigest(),
        "staged_diff_sha256": hashlib.sha256((staged.stdout or "").encode("utf-8", "surrogatepass")).hexdigest(),
        "untracked": untracked_rows,
        "unmerged": sorted(str(item) for item in getattr(state, "unmerged", ())),
        "dirty": bool(getattr(state, "dirty", False)),
    }
    payload["digest"] = _digest(payload)
    return payload


def _devtool_refs(root: Path, prefix: str) -> list[dict[str, str]]:
    proc = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)", prefix)
    if proc.returncode != 0:
        return []
    rows: list[dict[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        name, _, sha = line.partition("\0")
        if name:
            rows.append({"ref": name, "sha": sha})
    return rows


def _repository_snapshot(name: str, root: Path, *, roadmap_range: str) -> dict[str, Any]:
    try:
        state = collect_git_state(root)
    except GitStateError as exc:
        raise GitAuditError(f"Audit repository {name!r} is unavailable: {exc}") from exc
    head = str(state.head.get("sha") or "")
    tree = _require(root, "rev-parse", f"{head}^{{tree}}") if head else ""
    payload = {
        "repository": name,
        "root": str(root.resolve()),
        "repository_id": str(state.repository_id or ""),
        "repository_fingerprint": str((state.repository_identity or {}).get("fingerprint") or ""),
        "commit": head,
        "tree": tree,
        "dirty_delta": _dirty_delta_identity(root, state),
        "campaign_refs": _devtool_refs(root, "refs/devtool/campaigns"),
        "federation_refs": _devtool_refs(root, "refs/devtool/federations"),
        "inspected_roadmap_range": roadmap_range,
    }
    payload["source_identity_digest"] = _digest({
        "repository_id": payload["repository_id"], "commit": head, "tree": tree,
        "dirty_delta": payload["dirty_delta"]["digest"],
    })
    return payload


def _campaign_repositories(config: Any, campaign_id: str) -> list[tuple[str, Path]]:
    try:
        campaign = load_campaign(campaign_id)
    except GitCampaignError:
        campaign = {}
    generations = campaign.get("generations") if isinstance(campaign.get("generations"), list) else []
    if generations:
        latest = generations[-1] if isinstance(generations[-1], Mapping) else {}
        rows = latest.get("repositories") if isinstance(latest.get("repositories"), list) else []
        result: list[tuple[str, Path]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("repository") or "").strip()
            root = Path(str(row.get("root") or "")).expanduser().resolve(strict=False)
            if name and str(root):
                result.append((name, root))
        if result:
            return result
    # A campaign can be audited before a Git campaign generation exists. In
    # that case the federation declaration is the authoritative participant set.
    result = []
    for name in sorted(getattr(config.federation, "repositories", {}) or {}):
        resolution = resolve_federated_repository(config, name)
        if resolution.available:
            result.append((name, Path(resolution.root).resolve()))
    if not result:
        result.append(("root", Path(config.repo_root).resolve()))
    return result


def record_audit_pass(
    config: Any,
    *,
    campaign_id: str,
    roadmap_range: str = "",
    evidence: Iterable[str | Path] = (),
    gap_count: int = 0,
    pass_id: str = "",
) -> dict[str, Any]:
    campaign = str(campaign_id or "").strip()
    if not campaign:
        raise GitAuditError("Audit campaign id is required")
    if int(gap_count) < 0:
        raise GitAuditError("Audit gap count cannot be negative")
    pid = str(pass_id or "").strip() or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + os.urandom(4).hex()
    repositories = [
        _repository_snapshot(name, root, roadmap_range=str(roadmap_range or ""))
        for name, root in _campaign_repositories(config, campaign)
    ]
    evidence_rows = [_file_evidence(item) for item in evidence]
    payload: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "kind": "devtool.git-federation-audit-pass",
        "audit_pass_id": pid,
        "campaign_id": campaign,
        "created_at": _now(),
        "inspected_roadmap_range": str(roadmap_range or ""),
        "gap_count": int(gap_count),
        "zero_gap": int(gap_count) == 0,
        "repositories": repositories,
        "evidence": evidence_rows,
    }
    payload["evidence_set_digest"] = _digest(evidence_rows)
    payload["audit_digest"] = _digest({key: value for key, value in payload.items() if key != "audit_digest"})
    path = campaign_audit_root(campaign) / "passes" / f"{_safe(pid, 'pass')}.json"
    if path.exists():
        raise GitAuditError(f"Audit pass already exists: {pid}")
    _atomic_write(path, payload)
    payload["path"] = str(path)
    return payload


def _iter_pass_paths() -> Iterable[Path]:
    root = audit_root()
    if not root.is_dir():
        return []
    return root.glob("*/passes/*.json")


def load_audit_pass(identifier: str, *, campaign_id: str = "") -> dict[str, Any]:
    raw = str(identifier or "").strip()
    direct = Path(raw).expanduser()
    candidates: list[Path] = []
    if direct.is_file():
        candidates = [direct]
    elif campaign_id:
        path = campaign_audit_root(campaign_id) / "passes" / f"{_safe(raw, 'pass')}.json"
        if path.is_file():
            candidates = [path]
    else:
        candidates = [path for path in _iter_pass_paths() if path.stem == _safe(raw, "pass")]
    if len(candidates) != 1:
        raise GitAuditError(f"Audit pass {raw!r} matched {len(candidates)} records")
    path = candidates[0]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitAuditError(f"Unable to read audit pass {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "devtool.git-federation-audit-pass":
        raise GitAuditError(f"Invalid audit pass: {path}")
    payload["path"] = str(path)
    return payload


def audit_delta(old_pass: str, new_pass: str, *, campaign_id: str = "") -> dict[str, Any]:
    old = load_audit_pass(old_pass, campaign_id=campaign_id)
    new = load_audit_pass(new_pass, campaign_id=campaign_id)
    if str(old.get("campaign_id")) != str(new.get("campaign_id")):
        raise GitAuditError("Audit delta requires passes from the same campaign")
    old_by = {str(row.get("repository")): row for row in old.get("repositories", []) if isinstance(row, Mapping)}
    new_by = {str(row.get("repository")): row for row in new.get("repositories", []) if isinstance(row, Mapping)}
    rows: list[dict[str, Any]] = []
    for name in sorted(set(old_by) | set(new_by)):
        before, after = old_by.get(name), new_by.get(name)
        fields: list[str] = []
        if before is None or after is None:
            fields.append("membership")
        else:
            for key in ("repository_id", "commit", "tree", "source_identity_digest"):
                if str(before.get(key) or "") != str(after.get(key) or ""):
                    fields.append(key)
            if _digest(before.get("campaign_refs") or []) != _digest(after.get("campaign_refs") or []):
                fields.append("campaign_refs")
            if _digest(before.get("federation_refs") or []) != _digest(after.get("federation_refs") or []):
                fields.append("federation_refs")
        rows.append({"repository": name, "changed": bool(fields), "changed_fields": fields, "old": before, "new": after})
    report = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "kind": "devtool.git-federation-audit-delta",
        "campaign_id": str(old.get("campaign_id") or ""),
        "old_pass": str(old.get("audit_pass_id") or ""),
        "new_pass": str(new.get("audit_pass_id") or ""),
        "repositories": rows,
        "source_changed": any(row["changed"] for row in rows),
        "gap_delta": int(new.get("gap_count", 0)) - int(old.get("gap_count", 0)),
        "evidence_changed": str(old.get("evidence_set_digest") or "") != str(new.get("evidence_set_digest") or ""),
    }
    report["delta_digest"] = _digest(report)
    return report


def _campaign_passes(campaign_id: str) -> list[dict[str, Any]]:
    directory = campaign_audit_root(campaign_id) / "passes"
    result: list[dict[str, Any]] = []
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.json")):
        try:
            result.append(load_audit_pass(str(path)))
        except GitAuditError:
            continue
    result.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("audit_pass_id") or "")))
    return result


def audit_status(config: Any, *, campaign_id: str) -> dict[str, Any]:
    campaign = str(campaign_id or "").strip()
    if not campaign:
        raise GitAuditError("--campaign is required")
    passes = _campaign_passes(campaign)
    zero_gap = [item for item in passes if bool(item.get("zero_gap"))]
    latest = passes[-1] if passes else None
    latest_zero = zero_gap[-1] if zero_gap else None
    current_rows = [
        _repository_snapshot(name, root, roadmap_range=str((latest_zero or {}).get("inspected_roadmap_range") or ""))
        for name, root in _campaign_repositories(config, campaign)
    ]
    changes: list[dict[str, Any]] = []
    if latest_zero:
        prior = {str(row.get("repository")): row for row in latest_zero.get("repositories", []) if isinstance(row, Mapping)}
        current = {str(row.get("repository")): row for row in current_rows}
        for name in sorted(set(prior) | set(current)):
            before, after = prior.get(name), current.get(name)
            changed = before is None or after is None or str((before or {}).get("source_identity_digest") or "") != str((after or {}).get("source_identity_digest") or "")
            if changed:
                changes.append({"repository": name, "expected": before, "current": after})
    report = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "kind": "devtool.git-federation-audit-status",
        "campaign_id": campaign,
        "passes": len(passes),
        "latest_pass": latest,
        "latest_zero_gap_pass": latest_zero,
        "current_repositories": current_rows,
        "source_changes_since_zero_gap": changes,
        "zero_gap_fresh": bool(latest_zero) and not changes,
        "seal_eligible": bool(latest_zero) and not changes,
    }
    report["status_digest"] = _digest({key: value for key, value in report.items() if key != "status_digest"})
    return report


def verify_zero_gap_audit_freshness(config: Any, campaign_id: str) -> dict[str, Any]:
    """Fail-closed source-freshness contract consumed by the future GIT-FRX-18 seal."""
    return audit_status(config, campaign_id=campaign_id)


__all__ = [
    "AUDIT_SCHEMA_VERSION", "GitAuditError", "audit_delta", "audit_status", "load_audit_pass",
    "record_audit_pass", "verify_zero_gap_audit_freshness",
]
