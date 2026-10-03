"""Android signing identities, task policy, and Devtool-managed Gradle injection.

AOPS03 deliberately keeps secret *values* out of persistent configuration,
command lines, logs, reports, and generated Gradle files.  Profiles persist
only secret sources (environment variable names, file descriptors, stdin or an
external secret command).  Values are materialized into short-lived canonical
environment variables when a child process actually needs them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Sequence

from ..models import ProjectConfig
from .capabilities import AndroidExecutionContext, resolve_android_execution_context

SIGNING_MODES = {"devtool", "project", "external"}
SIGNING_POLICIES = {"optional", "release-required", "external"}
PROTECTED_SIGNING_BACKENDS = ("native", "ssh-command", "ssh-proot-oci", "termux-host-chroot", "proot-distro")
_DESTINATION_SIGNING_BACKENDS = frozenset(PROTECTED_SIGNING_BACKENDS[1:])
_RELEASE_PRODUCER_MARKERS = (
    "assemble", "bundle", "package", "publish", "upload", "release", "distribution",
)
_NON_PRODUCER_MARKERS = ("lint", "test", "check", "compile", "process", "generate", "verify")
_FALSE_STRINGS = {"", "0", "false", "no", "off", "disabled", "none"}
_TRUE_STRINGS = {"1", "true", "yes", "on", "enabled"}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _FALSE_STRINGS:
            return False
        if text in _TRUE_STRINGS:
            return True
        return default
    return bool(value)


class AndroidSigningError(RuntimeError):
    """Signing configuration, credential or identity failure."""


@dataclass(frozen=True, slots=True)
class SigningProfile:
    name: str = "release"
    identity_id: str = ""
    mode: str = "project"
    policy: str = "optional"
    keystore: str = ""
    alias: str = ""
    store_file_env: str = ""
    key_alias_env: str = ""
    store_type: str = "PKCS12"
    expected_cert_sha256: str = ""
    store_password_env: str = ""
    key_password_env: str = ""
    store_password_file: str = ""
    key_password_file: str = ""
    store_password_fd: int | None = None
    key_password_fd: int | None = None
    store_password_command: str = ""
    key_password_command: str = ""
    store_password_stdin: bool = False
    key_password_stdin: bool = False
    allow_prompt: bool = False

    @property
    def require_for_release(self) -> bool:
        return self.policy == "release-required"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # External secret commands are executable source descriptors but may
        # themselves contain sensitive arguments. Never persist/report their
        # literal command text. Environment variable *names* and fd numbers are
        # safe descriptors; secret values are never members of this model.
        if payload.get("store_password_command"):
            payload["store_password_command"] = "<configured>"
        if payload.get("key_password_command"):
            payload["key_password_command"] = "<configured>"
        payload["secret_sources"] = {
            "store": _secret_source_label(self, "store"),
            "key": _secret_source_label(self, "key"),
        }
        return payload


@dataclass(frozen=True, slots=True)
class SigningIdentity:
    source: str
    alias: str = ""
    subject: str = ""
    issuer: str = ""
    serial_number: str = ""
    valid_from: str = ""
    valid_until: str = ""
    sha256: str = ""
    sha1: str = ""
    algorithm: str = ""
    expired: bool | None = None
    expires_in_days: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SigningVerification:
    status: str
    artifact: str
    signed: bool
    identity: SigningIdentity | None = None
    expected_sha256: str = ""
    detail: str = ""
    tool: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "artifact": self.artifact,
            "signed": self.signed,
            "identity": self.identity.as_dict() if self.identity else None,
            "expected_sha256": self.expected_sha256,
            "detail": self.detail,
            "tool": self.tool,
        }


def _normalize_fingerprint(value: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", str(value or "")).lower()


def _android_options(config: ProjectConfig | None) -> dict[str, Any]:
    if config is None:
        return {}
    value = config.runner_options.get("android", {})
    return dict(value) if isinstance(value, dict) else {}


def _release_profile_config(config: ProjectConfig | None, name: str = "release") -> dict[str, Any]:
    android = _android_options(config)
    signing = android.get("signing", {})
    if not isinstance(signing, dict):
        return {}
    profile = signing.get(name, {})
    if not isinstance(profile, dict):
        # Also support a flat [android.signing] block for single-profile projects.
        profile = signing if name == "release" else {}
    return dict(profile)


def signing_profile(config: ProjectConfig | None, name: str = "release") -> SigningProfile:
    raw = _release_profile_config(config, name)
    mode = str(raw.get("mode") or "project").strip().lower()
    if mode not in SIGNING_MODES:
        raise AndroidSigningError(f"Unsupported Android signing mode {mode!r}; expected one of {', '.join(sorted(SIGNING_MODES))}")
    policy = str(raw.get("policy") or ("release-required" if _coerce_bool(raw.get("require_for_release"), False) else "optional")).strip().lower()
    if policy not in SIGNING_POLICIES:
        raise AndroidSigningError(f"Unsupported Android signing policy {policy!r}; expected one of {', '.join(sorted(SIGNING_POLICIES))}")

    identity_id = str(raw.get("identity_id") or "").strip()
    identity = None
    if identity_id:
        from ..artifact_trust import SigningIdentityRegistry
        identity = SigningIdentityRegistry().get(identity_id)
        if identity is None:
            raise AndroidSigningError(f"Unknown LIFE signing identity {identity_id!r}")
        if identity.purposes and not set(identity.purposes).intersection({"android", "android-signing", "release-signing"}):
            raise AndroidSigningError(
                f"LIFE signing identity {identity_id!r} is not authorized for Android signing "
                f"(purposes={', '.join(identity.purposes)})"
            )
        if identity.variants and name not in identity.variants and "*" not in identity.variants:
            raise AndroidSigningError(
                f"LIFE signing identity {identity_id!r} is not authorized for signing profile {name!r}"
            )
    elif mode == "devtool":
        # REM01 fixed-point audit: devtool-managed Android signing must consume
        # a durable LIFE signing identity.  The previous compatibility branch
        # synthesized a canonical-looking ephemeral identity from Android-local
        # keystore/alias/certificate fields, which made legacy Android config an
        # independent identity authority.  Project/Gradle signing may still use
        # project-local settings, but mode=devtool is reserved for LIFE-owned
        # identities.
        legacy_fields = [
            key for key in (
                "keystore", "alias", "expected_cert_sha256", "store_file_env",
                "key_alias_env", "store_password_env", "key_password_env",
                "store_password_file", "key_password_file", "store_password_fd",
                "key_password_fd", "store_password_command", "key_password_command",
                "store_password_stdin", "key_password_stdin",
            )
            if raw.get(key) not in (None, "", False)
        ]
        detail = f"; legacy fields present: {', '.join(sorted(legacy_fields))}" if legacy_fields else ""
        raise AndroidSigningError(
            f"Android signing mode 'devtool' requires a registered LIFE identity_id for profile {name!r}{detail}"
        )

    def _fd(key: str) -> int | None:
        value = raw.get(key)
        if value in {None, ""}:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise AndroidSigningError(f"{key} must be a non-negative file descriptor") from exc
        if parsed < 0:
            raise AndroidSigningError(f"{key} must be a non-negative file descriptor")
        return parsed

    keystore = str(raw.get("keystore") or "").strip()
    alias = str(raw.get("alias") or "").strip()
    store_type = str(raw.get("store_type") or raw.get("type") or "PKCS12").strip().upper()
    expected_cert = _normalize_fingerprint(str(raw.get("expected_cert_sha256") or ""))
    secret_overrides: dict[str, Any] = {}
    if identity is not None:
        keystore = str(identity.key_reference or "").strip()
        alias = str(identity.alias or alias).strip()
        expected_cert = _normalize_fingerprint(identity.certificate_sha256 or expected_cert)
        provider_name = str(identity.provider or "").strip().lower()
        if "jks" in provider_name:
            store_type = "JKS"
        elif "pkcs12" in provider_name or "android-keystore" in provider_name:
            store_type = "PKCS12"
        provider = identity.secret_provider
        if provider is not None:
            if provider.kind == "local":
                secret_overrides["store_password_env"] = provider.reference
            elif provider.kind == "fd":
                try:
                    secret_overrides["store_password_fd"] = int(provider.reference)
                except ValueError as exc:
                    raise AndroidSigningError(f"Invalid LIFE signing fd reference {provider.reference!r}") from exc
            elif provider.kind == "stdin":
                secret_overrides["store_password_stdin"] = True
            elif provider.kind == "external-command":
                secret_overrides["store_password_command"] = provider.reference
            elif provider.kind == "private-file":
                secret_overrides["store_password_file"] = provider.reference
            else:
                raise AndroidSigningError(
                    f"LIFE signing secret provider {provider.kind!r} is not supported by Android signing"
                )
    if keystore:
        keystore = str(Path(keystore).expanduser())
    return SigningProfile(
        name=name, identity_id=identity_id,
        mode=mode,
        policy=policy,
        keystore=keystore,
        alias=alias,
        store_type=store_type,
        expected_cert_sha256=expected_cert,
        store_password_env=str(secret_overrides.get("store_password_env", raw.get("store_password_env") or "")).strip(),
        key_password_env=str(raw.get("key_password_env") or "").strip(),
        store_password_file=str(secret_overrides.get("store_password_file", raw.get("store_password_file") or "")).strip(),
        key_password_file=str(raw.get("key_password_file") or "").strip(),
        store_password_fd=secret_overrides.get("store_password_fd", _fd("store_password_fd")),
        key_password_fd=_fd("key_password_fd"),
        store_password_command=str(secret_overrides.get("store_password_command", raw.get("store_password_command") or "")).strip(),
        key_password_command=str(raw.get("key_password_command") or "").strip(),
        store_password_stdin=bool(secret_overrides.get("store_password_stdin", _coerce_bool(raw.get("store_password_stdin", False), False))),
        key_password_stdin=_coerce_bool(raw.get("key_password_stdin", False), False),
        allow_prompt=_coerce_bool(raw.get("allow_prompt", False), False),
    )


def task_requires_release_signing(task: str) -> bool:
    leaf = str(task or "").strip().rsplit(":", 1)[-1]
    low = leaf.lower()
    if not low or "release" not in low:
        return False
    if any(low.startswith(marker) for marker in _NON_PRODUCER_MARKERS):
        return False
    producer_prefixes = ("assemble", "bundle", "package", "publish", "upload")
    if low in {"release", "releasebundle", "releaseapk"}:
        return True
    return any(low.startswith(prefix) for prefix in producer_prefixes)

def tasks_require_release_signing(tasks: Sequence[str]) -> bool:
    return any(task_requires_release_signing(task) for task in tasks)


def _secret_source_label(profile: SigningProfile, which: str) -> str:
    env_name = getattr(profile, f"{which}_password_env")
    file_name = getattr(profile, f"{which}_password_file")
    fd = getattr(profile, f"{which}_password_fd")
    command = getattr(profile, f"{which}_password_command")
    stdin = getattr(profile, f"{which}_password_stdin")
    if env_name:
        return f"env:{env_name}"
    if file_name:
        return "private-file"
    if fd is not None:
        return f"fd:{fd}"
    if command:
        return "command"
    if stdin:
        return "stdin"
    if profile.allow_prompt:
        return "prompt"
    return "none"


def _read_fd(fd: int) -> str:
    try:
        data = os.read(fd, 1024 * 1024)
    except OSError as exc:
        raise AndroidSigningError(f"Could not read signing secret from fd {fd}: {exc}") from exc
    return data.decode("utf-8", errors="strict").rstrip("\r\n")


def _run_secret_command(command: str, *, env: Mapping[str, str]) -> str:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise AndroidSigningError(f"Signing secret command is malformed: {exc}") from exc
    if not argv:
        raise AndroidSigningError("Signing secret command is empty")
    try:
        result = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=30, env=dict(env))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AndroidSigningError(f"Signing secret provider failed: {exc}") from exc
    if result.returncode != 0:
        # Never include stdout because it may itself be the secret.
        raise AndroidSigningError(f"Signing secret provider exited with {result.returncode}")
    return result.stdout.rstrip("\r\n")


def _resolve_secret(profile: SigningProfile, which: str, *, env: Mapping[str, str], stdin_value: str | None = None, allow_prompt: bool = False) -> str:
    env_name = getattr(profile, f"{which}_password_env")
    file_name = getattr(profile, f"{which}_password_file")
    fd = getattr(profile, f"{which}_password_fd")
    command = getattr(profile, f"{which}_password_command")
    use_stdin = getattr(profile, f"{which}_password_stdin")
    if env_name:
        value = str(env.get(env_name) or "")
        if value:
            return value
        raise AndroidSigningError(f"Signing secret environment variable {env_name} is not set")
    if file_name:
        path = Path(file_name).expanduser()
        try:
            value = path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise AndroidSigningError(f"Could not read signing secret private file {path}: {exc}") from exc
        if value:
            return value
        raise AndroidSigningError(f"Signing secret private file {path} is empty")
    if fd is not None:
        value = _read_fd(fd)
        if value:
            return value
        raise AndroidSigningError(f"Signing secret from fd {fd} is empty")
    if command:
        value = _run_secret_command(command, env=env)
        if value:
            return value
        raise AndroidSigningError("Signing secret provider returned an empty value")
    if use_stdin:
        if stdin_value is None:
            try:
                stdin_value = sys.stdin.readline().rstrip("\r\n")
            except (OSError, AttributeError) as exc:
                raise AndroidSigningError(f"Could not read signing secret from stdin: {exc}") from exc
        if stdin_value:
            return stdin_value
        raise AndroidSigningError("Signing secret from stdin is empty")
    if allow_prompt or profile.allow_prompt:
        value = getpass.getpass(f"Android {which} password: ")
        if value:
            return value
        raise AndroidSigningError("Signing password cannot be empty")
    raise AndroidSigningError(f"No {which} password source is configured")


def signing_secret_environment(
    profile: SigningProfile,
    *,
    env: Mapping[str, str] | None = None,
    store_stdin: str | None = None,
    key_stdin: str | None = None,
    allow_prompt: bool = False,
) -> dict[str, str]:
    base = dict(os.environ if env is None else env)
    store = _resolve_secret(profile, "store", env=base, stdin_value=store_stdin, allow_prompt=allow_prompt)
    try:
        key = _resolve_secret(profile, "key", env=base, stdin_value=key_stdin, allow_prompt=allow_prompt)
    except AndroidSigningError:
        # PKCS12 commonly uses the same password for store/key.  Reuse only when
        # no key source was declared; an explicitly broken key source must fail.
        if _secret_source_label(profile, "key") == "none":
            key = store
        else:
            raise
    return {
        "DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD": store,
        "DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD": key,
    }


SIGNING_ENV_KEYS = frozenset({
    "DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD",
    "DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD",
})


def signing_environment_for_tasks(
    config: ProjectConfig | None,
    tasks: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Materialize signing secrets for an immediate child process.

    This remains a public/direct-use helper for signing tools and compatibility
    callers. Production command planning must use :func:`signing_plan_metadata`
    instead so secret values never enter ``CommandSpec.env`` or durable
    validation-session state.
    """

    base = dict(os.environ if env is None else env)
    if not tasks_require_release_signing(tasks):
        return base
    profile = signing_profile(config)
    if profile.mode != "devtool":
        return base
    secrets = signing_secret_environment(profile, env=base)
    base.update(secrets)
    # Bridge Devtool-managed secrets to StudyFlow Gradle variables when the
    # local release keystore exists. Values are materialized only at execution
    # time and never persisted or placed in argv/logs.
    store_file = Path(profile.keystore).expanduser() if profile.keystore else None
    if store_file and store_file.is_file():
        file_env = profile.store_file_env or "STUDYFLOW_RELEASE_STORE_FILE"
        alias_env = profile.key_alias_env or "STUDYFLOW_RELEASE_KEY_ALIAS"
        base.setdefault(file_env, str(store_file))
        if profile.alias:
            base.setdefault(alias_env, profile.alias)
        base[profile.store_password_env or "STUDYFLOW_RELEASE_STORE_PASSWORD"] = secrets["DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD"]
        base[profile.key_password_env or "STUDYFLOW_RELEASE_KEY_PASSWORD"] = secrets["DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD"]
    return base


def signing_plan_metadata(
    config: ProjectConfig | None,
    tasks: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return non-secret metadata that defers credential materialization.

    The metadata is intentionally safe for validation-session JSON, logs,
    retries and backend planning. Password values and external secret command
    strings are never included.
    """

    normalized = list(dict.fromkeys(str(item).strip() for item in tasks if str(item).strip()))
    from .variants import has_explicit_signing_policy, resolve_android_variant
    variant_profile = resolve_android_variant(config)
    required = variant_profile.requires_release_signing
    if not required and not has_explicit_signing_policy(config):
        required = tasks_require_release_signing(normalized)
    if not required:
        return {}
    profile = validate_signing_profile_for_tasks(config, normalized, env=env)
    if profile.mode != "devtool":
        return {}
    return {
        "android_signing_required": True,
        "android_signing_profile": profile.name,
        "android_signing_tasks": normalized,
        "android_signing_secret_transport": "execution-boundary",
        "android_signing_transport_matrix": signing_transport_matrix(),
        "android_signing_store_source": _secret_source_label(profile, "store"),
        "android_signing_key_source": _secret_source_label(profile, "key"),
    }


def signing_transport_matrix() -> dict[str, Any]:
    """Return the non-secret release-signing transport matrix.

    LIFE-A04 is a production-boundary invariant, not only a test assertion:
    command planners persist this matrix with the signing plan so retries,
    validation sessions and provider wrappers can prove which backends are
    allowed to materialize credentials directly and which must stage them via a
    protected provider-owned channel.  The payload contains no secret values or
    secret-provider commands.
    """

    return {
        "schema_version": 1,
        "authority": "LIFE-A04/android-signing-transport",
        "native_backend": "native",
        "protected_destination_backends": sorted(_DESTINATION_SIGNING_BACKENDS),
        "all_supported_backends": list(PROTECTED_SIGNING_BACKENDS),
        "unsafe_shell_wrapped_env_backends": sorted(_DESTINATION_SIGNING_BACKENDS),
        "secret_value_persisted": False,
    }


def signing_environment_for_execution(
    config: ProjectConfig | None,
    metadata: Mapping[str, Any],
    *,
    env: Mapping[str, str],
) -> dict[str, str]:
    """Resolve signing secrets exactly at the final native execution boundary.

    Backends that turn environment assignments into shell command text are
    deliberately rejected unless the backend has already consumed the secret
    values through its protected staging channel.  In that case the wrapped
    CommandSpec carries only a non-secret attestation marker and this function
    must *not* resolve the passwords a second time into the local wrapper
    process environment.
    """

    base = dict(env)
    if not bool(metadata.get("android_signing_required")):
        return base
    if bool(metadata.get("android_signing_backend_secret_staged")):
        # The SSH/chroot/PRoot wrapper has already removed the secret values
        # from CommandSpec.env and staged them through a mode-0600 private
        # channel.  Re-materialising here would put the credentials in the
        # environment of the local shell/transport wrapper and would no longer
        # correspond to the destination process that performs signing.
        return base
    tasks = [str(item) for item in metadata.get("android_signing_tasks", []) or [] if str(item).strip()]
    # `android_signing_required` is canonical plan metadata. Do not re-derive
    # intent from Gradle task spelling here: custom production-signed variants
    # need not contain the word "release".
    execution_mode = str(metadata.get("execution_mode") or "").strip().lower()
    unsafe_modes = {"chroot", "ssh", *_DESTINATION_SIGNING_BACKENDS}
    if execution_mode in unsafe_modes:
        raise AndroidSigningError(
            "Devtool-managed Android signing refuses the selected execution backend "
            f"({execution_mode}) because it cannot transport signing secrets without "
            "serializing environment values into shell argv. Use native execution, "
            "project/external signing, or a future backend with a protected secret channel."
        )
    profile = validate_signing_profile_for_tasks(config, tasks, env=base)
    if profile.mode != "devtool":
        return base
    # Interactive runs may securely prompt for missing passwords. CI and
    # redirected/non-TTY executions remain fail-closed unless explicitly opted in
    # through metadata (which still uses getpass and never logs the value).
    interactive = bool(metadata.get("android_signing_interactive")) or (
        sys.stdin.isatty() and sys.stderr.isatty()
    )
    secrets = signing_secret_environment(profile, env=base, allow_prompt=interactive)
    base.update(secrets)
    return base


def signing_environment_for_protected_backend(
    config: ProjectConfig | None,
    metadata: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    backend: str,
) -> dict[str, str]:
    """Materialise signing credentials for a backend-owned private channel.

    This is intentionally separate from :func:`signing_environment_for_execution`.
    It is called immediately before a destination wrapper takes ownership of a
    CommandSpec.  The caller MUST remove the returned sensitive values from the
    resulting CommandSpec and stage them through its private channel before the
    spec reaches normal execution/persistence.

    Only backends with an implemented protected channel are accepted here.
    The return value contains secrets; callers must never serialize it into
    metadata, argv, reports, or durable state.
    """

    backend_name = str(backend or "").strip().lower()
    if backend_name not in _DESTINATION_SIGNING_BACKENDS:
        raise AndroidSigningError(
            f"Android signing backend {backend_name or '<unknown>'} has no protected secret transport"
        )
    base = dict(env)
    if not bool(metadata.get("android_signing_required")):
        return base
    tasks = [str(item) for item in metadata.get("android_signing_tasks", []) or [] if str(item).strip()]
    profile = validate_signing_profile_for_tasks(config, tasks, env=base)
    if profile.mode != "devtool":
        return base
    interactive = bool(metadata.get("android_signing_interactive")) or (
        sys.stdin.isatty() and sys.stderr.isatty()
    )
    secrets = signing_secret_environment(profile, env=base, allow_prompt=interactive)
    base.update(secrets)
    return base


def strip_signing_secrets(env: Mapping[str, str]) -> dict[str, str]:
    """Return an environment safe for planning/persistence."""

    return {str(key): str(value) for key, value in env.items() if str(key) not in SIGNING_ENV_KEYS}


def strip_configured_signing_source_values(
    config: ProjectConfig | None, env: Mapping[str, str]
) -> dict[str, str]:
    """Remove configured password-source variables from planned environments."""

    safe = strip_signing_secrets(env)
    try:
        profile = signing_profile(config)
    except AndroidSigningError:
        return safe
    for key in (profile.store_password_env, profile.key_password_env):
        if key:
            safe.pop(key, None)
    return safe


def _escape_groovy(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _signing_runtime_dir() -> Path:
    explicit = os.environ.get("DEVTOOL_ANDROID_SIGNING_RUNTIME_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return (root / "devtool" / "runtime" / "android" / "signing").resolve()


def validate_signing_profile_for_tasks(config: ProjectConfig | None, tasks: Sequence[str], *, env: Mapping[str, str] | None = None) -> SigningProfile:
    from .variants import has_explicit_signing_policy, resolve_android_variant
    variant_profile = resolve_android_variant(config)
    profile = signing_profile(config, variant_profile.signing_profile or "release")
    task_fallback = tasks_require_release_signing(tasks) if not has_explicit_signing_policy(config) else False
    if not (variant_profile.requires_release_signing or task_fallback):
        return profile
    if profile.policy == "external" or profile.mode == "external":
        return profile
    if profile.mode == "project":
        if profile.require_for_release:
            # Project mode explicitly delegates credentials/configuration to the
            # project; Devtool cannot assert their presence before Gradle.
            return profile
        return profile
    if not profile.keystore:
        raise AndroidSigningError("Devtool-managed release signing requires android.signing.release.keystore")
    if not Path(profile.keystore).expanduser().is_file():
        raise AndroidSigningError(f"Android signing keystore does not exist: {profile.keystore}")
    if not profile.alias:
        raise AndroidSigningError("Devtool-managed release signing requires android.signing.release.alias")
    # Validate source descriptors without consuming fd/stdin sources. Actual
    # materialization happens exactly once when the child Gradle environment is
    # constructed, still before the process starts. This matters for one-shot
    # file descriptors and piped stdin.
    base = dict(os.environ if env is None else env)
    store_source = _secret_source_label(profile, "store")
    if store_source == "none":
        raise AndroidSigningError("Devtool-managed release signing requires a store password source")
    if config is not None and profile.store_password_env and profile.store_password_env in config.env:
        raise AndroidSigningError(
            f"Signing secret environment variable {profile.store_password_env} must come from the process environment, "
            "not project [env] configuration"
        )
    if profile.store_password_env and not str(base.get(profile.store_password_env) or ""):
        raise AndroidSigningError(f"Signing secret environment variable {profile.store_password_env} is not set")
    if profile.store_password_fd is not None:
        try:
            os.fstat(profile.store_password_fd)
        except OSError as exc:
            raise AndroidSigningError(f"Signing secret file descriptor {profile.store_password_fd} is unavailable: {exc}") from exc
    if profile.store_password_file and not Path(profile.store_password_file).expanduser().is_file():
        raise AndroidSigningError(f"Signing secret file does not exist: {profile.store_password_file}")
    if config is not None and profile.key_password_env and profile.key_password_env in config.env:
        raise AndroidSigningError(
            f"Signing secret environment variable {profile.key_password_env} must come from the process environment, "
            "not project [env] configuration"
        )
    if profile.key_password_env and not str(base.get(profile.key_password_env) or ""):
        raise AndroidSigningError(f"Signing secret environment variable {profile.key_password_env} is not set")
    if profile.key_password_fd is not None:
        try:
            os.fstat(profile.key_password_fd)
        except OSError as exc:
            raise AndroidSigningError(f"Signing secret file descriptor {profile.key_password_fd} is unavailable: {exc}") from exc
    if profile.key_password_file and not Path(profile.key_password_file).expanduser().is_file():
        raise AndroidSigningError(f"Signing secret file does not exist: {profile.key_password_file}")
    return profile


def stage_gradle_signing_init_script(config: ProjectConfig | None, tasks: Sequence[str], *, env: Mapping[str, str] | None = None) -> Path | None:
    from .variants import has_explicit_signing_policy, resolve_android_variant
    variant_profile = resolve_android_variant(config)
    task_required = tasks_require_release_signing(tasks)
    # Explicit task names are authoritative at this late execution boundary.
    # A debug task must not receive release credentials merely because the
    # surrounding ProjectConfig currently selects release.
    required = task_required
    if task_required and has_explicit_signing_policy(config):
        required = variant_profile.requires_release_signing
    if not required:
        return None
    profile = validate_signing_profile_for_tasks(config, tasks, env=env)
    if profile.mode != "devtool":
        return None
    fingerprint = hashlib.sha256(json.dumps({
        "keystore": str(Path(profile.keystore).expanduser().resolve(strict=False)),
        "alias": profile.alias,
        "store_type": profile.store_type,
        "name": profile.name,
        "build_type": variant_profile.build_type,
    }, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    target = _signing_runtime_dir() / f"{profile.name}-{fingerprint}.init.gradle"
    target.parent.mkdir(parents=True, exist_ok=True)
    keystore = _escape_groovy(str(Path(profile.keystore).expanduser().resolve(strict=False)))
    alias = _escape_groovy(profile.alias)
    store_type = _escape_groovy(profile.store_type or "PKCS12")
    build_type = _escape_groovy(variant_profile.build_type)
    text = f"""// Generated by Devtool AOPS03. Contains no secret values.\n\ngradle.beforeProject {{ project ->\n    project.plugins.withId('com.android.application') {{\n        def androidExt = project.extensions.findByName('android')\n        if (androidExt == null) return\n        def storePassword = System.getenv('DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD')\n        def keyPassword = System.getenv('DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD')\n        if (!storePassword || !keyPassword) {{\n            throw new GradleException('Devtool release signing secrets are unavailable in the Gradle environment')\n        }}\n        def signing = androidExt.signingConfigs.findByName('devtoolRelease')\n        if (signing == null) signing = androidExt.signingConfigs.create('devtoolRelease')\n        signing.storeFile = project.file('{keystore}')\n        signing.storePassword = storePassword\n        signing.keyAlias = '{alias}'\n        signing.keyPassword = keyPassword\n        if (signing.hasProperty('storeType')) signing.storeType = '{store_type}'\n        androidExt.buildTypes.all {{ buildType ->\n            if (buildType.name.equalsIgnoreCase('{build_type}')) {{\n                buildType.signingConfig = signing\n            }}\n        }}\n    }}\n}}\n"""
    if not target.is_file() or target.read_text(encoding="utf-8", errors="replace") != text:
        fd, temporary_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def signing_gradle_args_for_tasks(config: ProjectConfig | None, tasks: Sequence[str], *, env: Mapping[str, str] | None = None) -> list[str]:
    script = stage_gradle_signing_init_script(config, tasks, env=env)
    return ["--init-script", str(script)] if script is not None else []


class AndroidSigningManager:
    def __init__(self, context: AndroidExecutionContext | None = None, *, env: Mapping[str, str] | None = None) -> None:
        self.env = dict(os.environ if env is None else env)
        self.context = context or resolve_android_execution_context(None, env=self.env)

    def _keytool(self) -> str:
        java = self.context.tool("java")
        if java.path:
            sibling = Path(java.path).resolve(strict=False).parent / "keytool"
            if sibling.is_file() and os.access(sibling, os.X_OK):
                return str(sibling)
        found = shutil.which("keytool", path=self.env.get("PATH"))
        if found:
            return found
        raise AndroidSigningError("keytool is unavailable; install/use a JDK before managing Android signing identities")

    def _jarsigner(self) -> str:
        java = self.context.tool("java")
        if java.path:
            sibling = Path(java.path).resolve(strict=False).parent / "jarsigner"
            if sibling.is_file() and os.access(sibling, os.X_OK):
                return str(sibling)
        found = shutil.which("jarsigner", path=self.env.get("PATH"))
        if found:
            return found
        raise AndroidSigningError("jarsigner is unavailable; install/use a JDK")

    def _run(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None, timeout: float = 60) -> tuple[int, str]:
        try:
            result = subprocess.run(list(argv), text=True, capture_output=True, check=False, timeout=timeout, env=dict(env or self.env))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AndroidSigningError(f"Signing tool execution failed: {exc}") from exc
        return result.returncode, (result.stdout + "\n" + result.stderr).strip()

    @staticmethod
    def _ephemeral_env(base: Mapping[str, str], **values: str) -> tuple[dict[str, str], dict[str, str]]:
        env = dict(base)
        names: dict[str, str] = {}
        for label, value in values.items():
            name = f"DEVTOOL_SIGNING_{label.upper()}_{os.getpid()}"
            env[name] = value
            names[label] = name
        return env, names

    def create(
        self,
        path: str | Path,
        *,
        alias: str,
        store_password: str,
        key_password: str | None = None,
        distinguished_name: str = "CN=Devtool Android Release",
        validity_days: int = 9125,
        key_algorithm: str = "RSA",
        key_size: int = 4096,
        store_type: str = "PKCS12",
        overwrite: bool = False,
    ) -> SigningIdentity:
        target = Path(path).expanduser().resolve(strict=False)
        if target.exists() and not overwrite:
            raise AndroidSigningError(f"Refusing to overwrite existing keystore: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        key_password = key_password or store_password
        env, names = self._ephemeral_env(self.env, store=store_password, key=key_password)
        argv = [
            self._keytool(), "-genkeypair", "-noprompt", "-keystore", str(target),
            "-storetype", store_type, "-alias", alias,
            f"-storepass:env", names["store"], f"-keypass:env", names["key"],
            "-dname", distinguished_name, "-validity", str(validity_days),
            "-keyalg", key_algorithm, "-keysize", str(key_size),
        ]
        rc, output = self._run(argv, env=env)
        if rc != 0:
            raise AndroidSigningError(f"keytool could not create the keystore (exit {rc}): {output.splitlines()[-1] if output else 'no detail'}")
        try:
            target.chmod(0o600)
        except OSError:
            pass
        profile = SigningProfile(keystore=str(target), alias=alias, store_type=store_type)
        return self.inspect_profile(profile, store_password=store_password)

    def import_keystore(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        source_password: str,
        destination_password: str,
        source_type: str = "JKS",
        destination_type: str = "PKCS12",
        alias: str = "",
        overwrite: bool = False,
    ) -> list[SigningIdentity]:
        src = Path(source).expanduser().resolve(strict=False)
        dst = Path(destination).expanduser().resolve(strict=False)
        if not src.is_file():
            raise AndroidSigningError(f"Source keystore does not exist: {src}")
        if dst.exists() and not overwrite:
            raise AndroidSigningError(f"Refusing to overwrite existing keystore: {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        env, names = self._ephemeral_env(self.env, src=source_password, dst=destination_password)
        argv = [
            self._keytool(), "-importkeystore", "-noprompt",
            "-srckeystore", str(src), "-srcstoretype", source_type, "-srcstorepass:env", names["src"],
            "-destkeystore", str(dst), "-deststoretype", destination_type, "-deststorepass:env", names["dst"],
        ]
        if alias:
            argv.extend(["-srcalias", alias, "-destalias", alias])
        rc, output = self._run(argv, env=env, timeout=120)
        if rc != 0:
            raise AndroidSigningError(f"keytool import failed (exit {rc}): {output.splitlines()[-1] if output else 'no detail'}")
        try:
            dst.chmod(0o600)
        except OSError:
            pass
        return self.list_aliases(SigningProfile(keystore=str(dst), store_type=destination_type), store_password=destination_password)

    @staticmethod
    def _identity_from_keytool(source: str, text: str, *, alias: str = "") -> SigningIdentity:
        fingerprint = ""
        sha1 = ""
        subject = issuer = serial = valid_from = valid_until = algorithm = ""
        current_alias = alias
        for raw in text.splitlines():
            line = raw.strip()
            if line.lower().startswith("alias name:"):
                current_alias = line.split(":", 1)[1].strip()
            elif line.lower().startswith("owner:"):
                subject = line.split(":", 1)[1].strip()
            elif line.lower().startswith("issuer:"):
                issuer = line.split(":", 1)[1].strip()
            elif line.lower().startswith("serial number:"):
                serial = line.split(":", 1)[1].strip()
            elif "SHA256:" in line.upper():
                fingerprint = _normalize_fingerprint(line.split(":", 1)[1])
            elif re.search(r"\bSHA1:", line, re.I):
                sha1 = _normalize_fingerprint(line.split(":", 1)[1])
            elif line.lower().startswith("valid from:"):
                match = re.match(r"Valid from:\s*(.*?)\s*until:\s*(.*)$", line, re.I)
                if match:
                    valid_from, valid_until = match.group(1).strip(), match.group(2).strip()
            elif "signature algorithm name:" in line.lower():
                algorithm = line.split(":", 1)[1].strip()
        expired: bool | None = None
        expires_in_days: int | None = None
        if valid_until:
            parsed = None
            try:
                parsed = parsedate_to_datetime(valid_until)
            except (TypeError, ValueError, OverflowError):
                for fmt in ("%a %b %d %H:%M:%S %Z %Y", "%a %b %d %H:%M:%S %Y"):
                    try:
                        parsed = datetime.strptime(valid_until, fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                delta = parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)
                expires_in_days = int(delta.total_seconds() // 86400)
                expired = delta.total_seconds() < 0
        return SigningIdentity(source=source, alias=current_alias, subject=subject, issuer=issuer, serial_number=serial, valid_from=valid_from, valid_until=valid_until, sha256=fingerprint, sha1=sha1, algorithm=algorithm, expired=expired, expires_in_days=expires_in_days)

    def list_aliases(self, profile: SigningProfile, *, store_password: str | None = None) -> list[SigningIdentity]:
        if not profile.keystore:
            raise AndroidSigningError("Keystore path is required")
        password = store_password or _resolve_secret(profile, "store", env=self.env, allow_prompt=False)
        env, names = self._ephemeral_env(self.env, store=password)
        argv = [self._keytool(), "-list", "-v", "-keystore", str(Path(profile.keystore).expanduser()), "-storetype", profile.store_type, "-storepass:env", names["store"]]
        rc, output = self._run(argv, env=env)
        if rc != 0:
            raise AndroidSigningError(f"Could not inspect keystore (exit {rc}): {output.splitlines()[-1] if output else 'no detail'}")
        chunks = re.split(r"(?=Alias name:)", output, flags=re.I)
        identities = [self._identity_from_keytool(profile.keystore, chunk) for chunk in chunks if "Alias name:" in chunk]
        return identities

    def inspect_profile(self, profile: SigningProfile, *, store_password: str | None = None) -> SigningIdentity:
        aliases = self.list_aliases(profile, store_password=store_password)
        if profile.alias:
            for identity in aliases:
                if identity.alias == profile.alias:
                    if profile.expected_cert_sha256 and _normalize_fingerprint(identity.sha256) != profile.expected_cert_sha256:
                        raise AndroidSigningError("Configured signing certificate SHA-256 does not match the keystore identity")
                    return identity
            raise AndroidSigningError(f"Signing alias {profile.alias!r} was not found in {profile.keystore}")
        if len(aliases) == 1:
            return aliases[0]
        if not aliases:
            raise AndroidSigningError("No key aliases were found in the keystore")
        raise AndroidSigningError("Multiple signing aliases exist; configure android.signing.release.alias")

    def export_certificate(self, profile: SigningProfile, destination: str | Path, *, store_password: str | None = None, pem: bool = True) -> Path:
        identity = self.inspect_profile(profile, store_password=store_password)
        password = store_password or _resolve_secret(profile, "store", env=self.env)
        env, names = self._ephemeral_env(self.env, store=password)
        target = Path(destination).expanduser().resolve(strict=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        argv = [self._keytool(), "-exportcert", "-keystore", str(Path(profile.keystore).expanduser()), "-storetype", profile.store_type, "-storepass:env", names["store"], "-alias", identity.alias, "-file", str(target)]
        if pem:
            argv.append("-rfc")
        rc, output = self._run(argv, env=env)
        if rc != 0:
            raise AndroidSigningError(f"Certificate export failed (exit {rc}): {output.splitlines()[-1] if output else 'no detail'}")
        return target

    def verify_artifact(self, artifact: str | Path, *, expected_sha256: str = "") -> SigningVerification:
        path = Path(artifact).expanduser().resolve(strict=False)
        if not path.is_file():
            raise AndroidSigningError(f"Android artifact does not exist: {path}")
        expected = _normalize_fingerprint(expected_sha256)
        suffix = path.suffix.lower()
        if suffix == ".apk":
            apksigner = self.context.tool("apksigner")
            if not apksigner.path or apksigner.compatible is False:
                raise AndroidSigningError(apksigner.rejection_reason or "apksigner is unavailable")
            rc, output = self._run([apksigner.path, "verify", "--verbose", "--print-certs", str(path)])
            digest_match = re.search(r"certificate\s+SHA-256\s+digest:\s*([^\r\n]+)", output, re.I)
            subject_match = re.search(r"certificate\s+DN:\s*([^\r\n]+)", output, re.I)
            sha = _normalize_fingerprint(digest_match.group(1)) if digest_match else ""
            identity = SigningIdentity(source=str(path), subject=subject_match.group(1).strip() if subject_match else "", sha256=sha)
            ok = rc == 0 and bool(sha)
            if expected and sha != expected:
                ok = False
            detail = "APK signature verified" if ok else ("certificate identity mismatch" if rc == 0 and expected and sha != expected else (output.splitlines()[-1] if output else f"apksigner exit {rc}"))
            return SigningVerification("passed" if ok else "failed", str(path), rc == 0, identity, expected, detail, apksigner.path)
        if suffix in {".aab", ".jar"}:
            keytool = self._keytool()
            rc, output = self._run([keytool, "-printcert", "-jarfile", str(path)])
            identity = self._identity_from_keytool(str(path), output)
            ok = rc == 0 and bool(identity.sha256)
            if expected and _normalize_fingerprint(identity.sha256) != expected:
                ok = False
            detail = "JAR/AAB signer certificate verified" if ok else ("certificate identity mismatch" if rc == 0 and expected else (output.splitlines()[-1] if output else f"keytool exit {rc}"))
            return SigningVerification("passed" if ok else "failed", str(path), rc == 0, identity, expected, detail, keytool)
        raise AndroidSigningError(f"Unsupported signed Android artifact type: {path.suffix or '<none>'}")

    def compare_artifacts(self, left: str | Path, right: str | Path) -> dict[str, Any]:
        left_result = self.verify_artifact(left)
        right_result = self.verify_artifact(right)
        left_sha = _normalize_fingerprint(left_result.identity.sha256 if left_result.identity else "")
        right_sha = _normalize_fingerprint(right_result.identity.sha256 if right_result.identity else "")
        same = bool(left_sha and right_sha and left_sha == right_sha)
        return {
            "same_identity": same,
            "left": left_result.as_dict(),
            "right": right_result.as_dict(),
        }


__all__ = [
    "AndroidSigningError", "AndroidSigningManager", "SigningIdentity", "SigningProfile", "SigningVerification",
    "SIGNING_MODES", "SIGNING_POLICIES", "PROTECTED_SIGNING_BACKENDS", "signing_profile", "task_requires_release_signing",
    "tasks_require_release_signing", "validate_signing_profile_for_tasks", "signing_secret_environment",
    "signing_environment_for_tasks", "signing_plan_metadata", "signing_transport_matrix",
    "signing_environment_for_execution", "signing_environment_for_protected_backend",
    "strip_signing_secrets", "strip_configured_signing_source_values", "SIGNING_ENV_KEYS",
    "stage_gradle_signing_init_script", "signing_gradle_args_for_tasks",
]
