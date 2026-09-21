from __future__ import annotations

"""Named SSH worker profile management.

SSHW-06 makes build workers a user-level resource.  Global profiles live in
``~/.config/devtool/config.toml`` and can be reused by every repository;
repository-local profiles remain supported and override matching global
profiles.  Mutations are deliberately narrow and preserve unrelated TOML.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Any, Mapping

from .config import CONFIG_NAME, global_config_path

_PROFILE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_name(name: str) -> str:
    value = str(name or "").strip()
    if not _PROFILE_RE.fullmatch(value):
        raise ValueError("SSH profile names may contain only letters, numbers, '_' and '-'")
    return value


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _parse(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Unable to read SSH profile configuration from {path}: {exc}") from exc
    return value if isinstance(value, dict) else {}


def repository_config_path(repo: str | Path = "", config_path: str | Path = "") -> Path:
    if str(config_path or "").strip():
        path = Path(config_path).expanduser()
        if not path.is_absolute():
            path = (Path(repo).expanduser() if str(repo or "").strip() else Path.cwd()) / path
        return path.resolve(strict=False)
    root = Path(repo).expanduser() if str(repo or "").strip() else Path.cwd()
    return root.resolve(strict=False) / CONFIG_NAME


def profile_config_path(*, repo: str | Path = "", config_path: str | Path = "", global_scope: bool = True) -> Path:
    return global_config_path() if global_scope else repository_config_path(repo, config_path)


def _ssh_table(data: Mapping[str, Any]) -> Mapping[str, Any]:
    value = data.get("ssh", {}) if isinstance(data, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def _profile_map(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    top = _ssh_table(data)
    result: dict[str, dict[str, Any]] = {}
    for container_name in ("profiles", "workers", "hosts"):
        container = top.get(container_name, {})
        if not isinstance(container, Mapping):
            continue
        for raw_name, raw_value in container.items():
            if isinstance(raw_value, Mapping):
                result[str(raw_name)] = dict(raw_value)
    return result


def list_profiles(*, repo: str | Path = "", config_path: str | Path = "") -> dict[str, Any]:
    global_path = global_config_path()
    local_path = repository_config_path(repo, config_path)
    global_data = _parse(global_path)
    local_data = _parse(local_path)
    global_profiles = _profile_map(global_data)
    local_profiles = _profile_map(local_data)
    names = sorted(set(global_profiles) | set(local_profiles))
    global_default = str(_ssh_table(global_data).get("profile", _ssh_table(global_data).get("default_profile", "")) or "").strip()
    local_default = str(_ssh_table(local_data).get("profile", _ssh_table(local_data).get("default_profile", "")) or "").strip()
    records: list[dict[str, Any]] = []
    for name in names:
        gp = global_profiles.get(name, {})
        lp = local_profiles.get(name, {})
        effective = _deep_merge(gp, lp)
        records.append({
            "name": name,
            "scope": "repo" if name in local_profiles else "global",
            "global": name in global_profiles,
            "repo": name in local_profiles,
            "host": str(effective.get("host", "") or ""),
            "port": int(effective.get("port", 22) or 22),
            "user": str(effective.get("user", "") or ""),
            "mode": str(effective.get("mode", "commands") or "commands"),
            "workspace_mode": str((effective.get("workspace") or {}).get("mode", "") if isinstance(effective.get("workspace"), Mapping) else ""),
            "default": name == (local_default or global_default),
        })
    return {
        "schema_version": 1,
        "kind": "ssh-profiles",
        "ok": True,
        "global_config": str(global_path),
        "repo_config": str(local_path),
        "default_profile": local_default or global_default,
        "records": records,
    }


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _section_name(line: str) -> str:
    match = re.match(r"^\s*\[([^]]+)]\s*(?:#.*)?$", line)
    return match.group(1).strip() if match else ""


def _remove_profile_sections(text: str, profile: str) -> str:
    """Remove one profile from every supported SSH profile container.

    Devtool historically accepted ``ssh.hosts``, ``ssh.profiles`` and
    ``ssh.workers``.  SSHW-06 writes the canonical ``ssh.hosts`` form, but a
    force-replace or remove must not leave an older alias behind to be picked
    up by another supported reader.
    """
    prefixes = tuple(f"ssh.{container}.{profile}" for container in ("hosts", "profiles", "workers"))
    lines = text.splitlines(keepends=True)
    keep: list[str] = []
    dropping = False
    for line in lines:
        section = _section_name(line)
        if section:
            dropping = any(section == prefix or section.startswith(prefix + ".") for prefix in prefixes)
        if not dropping:
            keep.append(line)
    return "".join(keep).rstrip() + ("\n" if keep else "")


def _set_ssh_default(text: str, profile: str | None) -> str:
    lines = text.splitlines(keepends=True)
    ssh_start = None
    ssh_end = None
    for index, line in enumerate(lines):
        section = _section_name(line)
        if section == "ssh":
            ssh_start = index
            continue
        if ssh_start is not None and index > ssh_start and section:
            ssh_end = index
            break
    if ssh_start is not None and ssh_end is None:
        ssh_end = len(lines)
    pattern = re.compile(r"^\s*(?:profile|default_profile)\s*=")
    if ssh_start is not None:
        assert ssh_end is not None
        matches = [index for index in range(ssh_start + 1, ssh_end) if pattern.match(lines[index])]
        if profile:
            rendered = f"profile = {_toml_string(profile)}\n"
            if matches:
                lines[matches[0]] = rendered
                for index in reversed(matches[1:]):
                    del lines[index]
            else:
                lines.insert(ssh_start + 1, rendered)
        else:
            for index in reversed(matches):
                del lines[index]
        return "".join(lines).rstrip() + "\n"
    if not profile:
        return text.rstrip() + ("\n" if text else "")
    prefix = text.rstrip()
    return (prefix + "\n\n" if prefix else "") + "[ssh]\n" + f"profile = {_toml_string(profile)}\n"


def _atomic_write(path: Path, text: str, *, default_mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Validate before replacing user configuration.
    try:
        tomllib.loads(text or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Refusing to write invalid TOML to {path}: {exc}") from exc
    existing_mode = (path.stat().st_mode & 0o777) if path.exists() else int(default_mode)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, existing_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def add_profile(
    name: str,
    *,
    host: str,
    port: int = 22,
    user: str = "",
    identity: str = "",
    cwd: str = "",
    repo: str | Path = "",
    config_path: str | Path = "",
    global_scope: bool = True,
    set_default: bool = True,
    force: bool = False,
    workspace_mode: str = "managed",
    workspace_root: str = "~/.cache/devtool/workspaces",
    bootstrap_policy: str = "missing",
    host_key_policy: str = "accept-new",
    min_free_mb: int = 1024,
) -> dict[str, Any]:
    name = _validate_name(name)
    host = str(host or "").strip()
    if not host:
        raise ValueError("SSH worker host cannot be empty")
    if not 1 <= int(port) <= 65535:
        raise ValueError("SSH worker port must be between 1 and 65535")
    if workspace_mode not in {"managed", "existing", "ephemeral"}:
        raise ValueError("SSH worker workspace mode must be managed, existing, or ephemeral")
    if workspace_mode == "existing" and not str(cwd or "").strip():
        raise ValueError("SSH worker workspace.mode=existing requires --cwd for the remote checkout")
    if bootstrap_policy not in {"missing", "never"}:
        raise ValueError("SSH worker bootstrap policy must be missing or never")
    if host_key_policy not in {"accept-new", "strict", "system"}:
        raise ValueError("SSH worker host-key policy must be accept-new, strict, or system")
    if int(min_free_mb) < 0:
        raise ValueError("SSH worker minimum free storage cannot be negative")
    path = profile_config_path(repo=repo, config_path=config_path, global_scope=global_scope)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    parsed = _parse(path)
    exists = name in _profile_map(parsed)
    if exists and not force:
        raise ValueError(f"SSH profile {name!r} already exists in {path}; use --force to replace it")
    text = _remove_profile_sections(original, name)
    block = [
        f"[ssh.hosts.{name}]",
        f"host = {_toml_string(host)}",
        f"port = {int(port)}",
    ]
    if user:
        block.append(f"user = {_toml_string(str(user).strip())}")
    if identity:
        block.append(f"identity = {_toml_string(str(identity).strip())}")
    if cwd:
        block.append(f"cwd = {_toml_string(str(cwd).strip())}")
    block.extend([
        'mode = "commands"',
        "",
        f"[ssh.hosts.{name}.bootstrap]",
        f"policy = {_toml_string(bootstrap_policy)}",
        "",
        f"[ssh.hosts.{name}.worker]",
        "tmux = true",
        "",
        f"[ssh.hosts.{name}.workspace]",
        f"mode = {_toml_string(workspace_mode)}",
        f"root = {_toml_string(workspace_root)}",
        "",
        f"[ssh.hosts.{name}.sync]",
        'mode = "incremental"',
        "delete = true" if workspace_mode != "existing" else "delete = false",
        "default_excludes = true",
        "",
        f"[ssh.hosts.{name}.resilience]",
        f"host_key_policy = {_toml_string(host_key_policy)}",
        'run_root = "~/.cache/devtool/ssh-runs"',
        "lease_timeout_seconds = 900",
        f"min_free_mb = {int(min_free_mb)}",
    ])
    suffix = "\n".join(block).rstrip() + "\n"
    text = text.rstrip()
    text = (text + "\n\n" if text else "") + suffix
    if set_default:
        text = _set_ssh_default(text, name)
    _atomic_write(path, text, default_mode=0o600 if global_scope else 0o644)
    return {
        "schema_version": 1,
        "kind": "ssh-profile-add",
        "ok": True,
        "profile": name,
        "scope": "global" if global_scope else "repo",
        "path": str(path),
        "created": not exists,
        "replaced": exists,
        "default": bool(set_default),
    }


def remove_profile(
    name: str,
    *,
    repo: str | Path = "",
    config_path: str | Path = "",
    global_scope: bool = True,
) -> dict[str, Any]:
    name = _validate_name(name)
    path = profile_config_path(repo=repo, config_path=config_path, global_scope=global_scope)
    if not path.exists():
        raise ValueError(f"SSH profile configuration does not exist: {path}")
    original = path.read_text(encoding="utf-8")
    parsed = _parse(path)
    if name not in _profile_map(parsed):
        raise ValueError(f"SSH profile {name!r} is not defined in {path}")
    current_default = str(_ssh_table(parsed).get("profile", _ssh_table(parsed).get("default_profile", "")) or "").strip()
    text = _remove_profile_sections(original, name)
    if current_default == name:
        text = _set_ssh_default(text, None)
    _atomic_write(path, text, default_mode=0o600 if global_scope else 0o644)
    return {
        "schema_version": 1,
        "kind": "ssh-profile-remove",
        "ok": True,
        "profile": name,
        "scope": "global" if global_scope else "repo",
        "path": str(path),
        "cleared_default": current_default == name,
    }


def set_default_profile(
    name: str,
    *,
    repo: str | Path = "",
    config_path: str | Path = "",
    global_scope: bool = True,
) -> dict[str, Any]:
    name = _validate_name(name)
    path = profile_config_path(repo=repo, config_path=config_path, global_scope=global_scope)
    parsed = _parse(path)
    if name not in _profile_map(parsed):
        raise ValueError(f"SSH profile {name!r} is not defined in {path}")
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    _atomic_write(path, _set_ssh_default(original, name), default_mode=0o600 if global_scope else 0o644)
    return {
        "schema_version": 1,
        "kind": "ssh-profile-default",
        "ok": True,
        "profile": name,
        "scope": "global" if global_scope else "repo",
        "path": str(path),
        "default": True,
    }
