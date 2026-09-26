from __future__ import annotations

"""GIT-FRX-18 Git SSH signing policy and verification helpers.

Devtool deliberately supports one signing mechanism first: Git's native SSH
signing mode.  The policy is independent of Git's historical commit.gpgSign
boolean so an apply/campaign can be explicit and fail closed when signing is
required.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

SIGNING_MODES = ("off", "if-configured", "required")


class GitSigningError(RuntimeError):
    pass


def _git(root: Path, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], text=True, encoding="utf-8", errors="surrogateescape",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitSigningError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _config(root: Path, key: str) -> str:
    proc = _git(root, "config", "--get", key)
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    if proc.returncode == 1:
        return ""
    detail = (proc.stderr or proc.stdout or f"unable to read {key}").strip()
    raise GitSigningError(detail)


def normalize_signing_mode(value: object) -> str:
    mode = str(value or "off").strip().lower() or "off"
    if mode not in SIGNING_MODES:
        raise GitSigningError(f"Unsupported Git signing policy {value!r}; expected off, if-configured, or required")
    return mode


@dataclass(frozen=True, slots=True)
class GitSigningDecision:
    mode: str
    enabled: bool
    configured: bool
    format: str
    signing_key: str
    allowed_signers_file: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": self.mode,
            "enabled": self.enabled,
            "configured": self.configured,
            "format": self.format,
            "signing_key_configured": bool(self.signing_key),
            "allowed_signers_file_configured": bool(self.allowed_signers_file),
            "reason": self.reason,
        }


def signing_mode_from_config(config: Any, override: str = "") -> str:
    if str(override or "").strip():
        return normalize_signing_mode(override)
    raw = getattr(config, "git_config", {})
    value = raw.get("signing", "off") if isinstance(raw, dict) else "off"
    return normalize_signing_mode(value)


def resolve_signing_decision(repo_root: str | Path, mode: str) -> GitSigningDecision:
    root = Path(repo_root).resolve()
    selected = normalize_signing_mode(mode)
    fmt = _config(root, "gpg.format").lower()
    key = _config(root, "user.signingKey") or _config(root, "user.signingkey")
    allowed = _config(root, "gpg.ssh.allowedSignersFile")
    configured = fmt == "ssh" and bool(key) and bool(allowed)
    if selected == "off":
        return GitSigningDecision(selected, False, configured, fmt, key, allowed, "Devtool signing policy is off")
    if selected == "required" and not configured:
        raise GitSigningError(
            "Git SSH signing is required but not configured. Configure gpg.format=ssh, user.signingKey, and gpg.ssh.allowedSignersFile, or choose a different Devtool signing policy."
        )
    if selected == "if-configured" and not configured:
        return GitSigningDecision(selected, False, False, fmt, key, allowed, "SSH signing is not fully configured")
    return GitSigningDecision(selected, True, True, fmt, key, allowed, "Git SSH signing enabled")



def final_tag_argv(decision: GitSigningDecision, tag_name: str, target: str, message: str) -> list[str]:
    return ["tag", "-s" if decision.enabled else "-a", str(tag_name), str(target), "-m", str(message)]

def _signature_present(root: Path, revision: str) -> bool:
    proc = _git(root, "cat-file", "-p", revision)
    if proc.returncode != 0:
        return False
    return "\ngpgsig " in "\n" + (proc.stdout or "")


def verify_commit_signature(repo_root: str | Path, revision: str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    proc = _git(root, "verify-commit", "--raw", revision)
    present = _signature_present(root, revision)
    detail = (proc.stderr or proc.stdout or "").strip()
    return {
        "schema_version": 1,
        "kind": "devtool.git-signing-verification",
        "object_type": "commit",
        "revision": revision,
        "signature_present": present,
        "verified": proc.returncode == 0,
        "returncode": proc.returncode,
        "detail": detail,
        "digest": hashlib.sha256(json.dumps({"revision": revision, "present": present, "verified": proc.returncode == 0, "detail": detail}, sort_keys=True).encode()).hexdigest(),
    }


def verify_tag_signature(repo_root: str | Path, tag: str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    proc = _git(root, "verify-tag", "--raw", tag)
    detail = (proc.stderr or proc.stdout or "").strip()
    cat = _git(root, "cat-file", "-p", tag)
    present = cat.returncode == 0 and "-----BEGIN SSH SIGNATURE-----" in (cat.stdout or "")
    return {
        "schema_version": 1,
        "kind": "devtool.git-signing-verification",
        "object_type": "tag",
        "revision": tag,
        "signature_present": present,
        "verified": proc.returncode == 0,
        "returncode": proc.returncode,
        "detail": detail,
        "digest": hashlib.sha256(json.dumps({"tag": tag, "present": present, "verified": proc.returncode == 0, "detail": detail}, sort_keys=True).encode()).hexdigest(),
    }


__all__ = [
    "SIGNING_MODES", "GitSigningDecision", "GitSigningError", "normalize_signing_mode",
    "final_tag_argv", "resolve_signing_decision", "signing_mode_from_config", "verify_commit_signature", "verify_tag_signature",
]
