from __future__ import annotations

"""Shared non-secret trust/signing identity primitives for delivery and produced artifacts."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any, Mapping

from .logstore import state_dir


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _atomic_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        if os.name == "posix":
            dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        tmp.unlink(missing_ok=True)


def _reject_secret_fields(payload: Mapping[str, Any]) -> None:
    banned = ("password", "secret", "passphrase", "private_key", "private-key", "token")
    for key, value in payload.items():
        lowered = str(key).lower()
        if lowered in {"secret_provider", "secret_provider_identity"}:
            continue
        if any(token in lowered for token in banned) and value not in (None, "", False):
            raise ValueError(f"Signing identity evidence must not persist secret field {key!r}.")


@dataclass(frozen=True, slots=True)
class SigningIdentityId:
    value: str

    def __post_init__(self) -> None:
        text = self.value.strip()
        if not text or any(ch.isspace() for ch in text):
            raise ValueError("SigningIdentityId must be a non-empty whitespace-free identifier.")
        object.__setattr__(self, "value", text)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SecretProviderRef:
    kind: str
    reference: str

    def __post_init__(self) -> None:
        kind = self.kind.strip().lower().replace("_", "-")
        allowed = {"local", "keyring", "private-file", "fd", "stdin", "external-command"}
        if kind not in allowed:
            raise ValueError(f"Unsupported signing secret provider: {kind}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "reference", self.reference.strip())

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SigningIdentityRecord:
    identity_id: SigningIdentityId
    provider: str
    key_reference: str
    alias: str
    certificate_sha256: str
    purposes: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    secret_provider: SecretProviderRef | None = None
    policy_provenance: str = ""
    lifecycle: str = "durable"

    def __post_init__(self) -> None:
        cert = self.certificate_sha256.strip().lower()
        if cert and (len(cert) != 64 or any(ch not in "0123456789abcdef" for ch in cert)):
            raise ValueError("certificate_sha256 must be a SHA-256 digest.")
        if self.lifecycle not in {"durable", "ephemeral"}:
            raise ValueError("Signing identity lifecycle must be durable or ephemeral.")
        object.__setattr__(self, "certificate_sha256", cert)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "identity_id": str(self.identity_id),
            "provider": self.provider,
            "key_reference": self.key_reference,
            "alias": self.alias,
            "certificate_sha256": self.certificate_sha256,
            "purposes": list(self.purposes),
            "variants": list(self.variants),
            "secret_provider": self.secret_provider.as_dict() if self.secret_provider else None,
            "policy_provenance": self.policy_provenance,
            "lifecycle": self.lifecycle,
        }
        _reject_secret_fields(payload)
        return payload


@dataclass(frozen=True, slots=True)
class SigningIdentityBinding:
    identity_id: SigningIdentityId
    purposes: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    required: bool = True
    policy_provenance: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity_id": str(self.identity_id),
            "purposes": list(self.purposes),
            "variants": list(self.variants),
            "required": self.required,
            "policy_provenance": self.policy_provenance,
        }


def assert_certificate_continuity(
    identity: SigningIdentityRecord,
    observed_certificate_sha256: str,
    *,
    allow_change: bool = False,
) -> None:
    observed = observed_certificate_sha256.strip().lower()
    if not identity.certificate_sha256 or not observed or observed == identity.certificate_sha256:
        return
    if allow_change:
        return
    raise ValueError(
        f"Signing identity {identity.identity_id} certificate changed from "
        f"{identity.certificate_sha256} to {observed}; explicit authorization is required."
    )


@dataclass(frozen=True, slots=True)
class SigningEvidence:
    identity_id: str
    certificate_sha256: str
    artifact_sha256: str
    variant: str
    verification_tool: str
    verification_result: str
    evidence_id: str

    @classmethod
    def create(
        cls,
        *,
        identity: SigningIdentityRecord,
        artifact_sha256: str,
        variant: str,
        verification_tool: str,
        verification_result: str,
    ) -> "SigningEvidence":
        payload = {
            "identity_id": str(identity.identity_id),
            "certificate_sha256": identity.certificate_sha256,
            "artifact_sha256": artifact_sha256,
            "variant": variant,
            "verification_tool": verification_tool,
            "verification_result": verification_result,
        }
        return cls(**payload, evidence_id=f"signing:{_digest(payload)[:24]}")

    def as_dict(self) -> dict[str, str]:
        payload = asdict(self)
        _reject_secret_fields(payload)
        return payload


@dataclass(frozen=True, slots=True)
class TrustPolicy:
    require_signature: bool = False
    require_known_identity: bool = False
    minimum_assurance: str = "legacy"
    allowed_identity_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_ASSURANCE_ORDER = {"legacy": 0, "qualified": 1, "strict": 2}


def merge_trust_policy(global_policy: TrustPolicy, repository_policy: TrustPolicy, artifact_policy: TrustPolicy) -> TrustPolicy:
    """Monotonic merge: narrower/lower scopes can only strengthen policy."""
    policies = (global_policy, repository_policy, artifact_policy)
    minimum = max((p.minimum_assurance for p in policies), key=lambda value: _ASSURANCE_ORDER.get(value, -1))
    allowed_sets = [set(p.allowed_identity_ids) for p in policies if p.allowed_identity_ids]
    allowed = set.intersection(*allowed_sets) if allowed_sets else set()
    return TrustPolicy(
        require_signature=any(p.require_signature for p in policies),
        require_known_identity=any(p.require_known_identity for p in policies),
        minimum_assurance=minimum,
        allowed_identity_ids=tuple(sorted(allowed)),
    )


@contextmanager
def stage_ephemeral_secret(
    data: bytes,
    *,
    directory: str | Path | None = None,
) -> Any:
    """Stage secret bytes in a private file and remove them on every exit path.

    The returned path is intended for FD/private-file providers. Callers must not
    serialize its bytes or place secret values in argv/log/evidence.
    """
    base = Path(directory).expanduser() if directory else state_dir() / "signing" / "staging"
    base.mkdir(parents=True, exist_ok=True)
    try:
        base.chmod(0o700)
    except OSError:
        pass
    fd, raw = tempfile.mkstemp(prefix="secret-", dir=base)
    path = Path(raw)
    try:
        os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        yield path
    finally:
        try:
            if path.exists():
                with path.open("r+b") as handle:
                    size = path.stat().st_size
                    if size:
                        handle.write(b"\0" * size)
                        handle.flush()
                        os.fsync(handle.fileno())
        except OSError:
            pass
        path.unlink(missing_ok=True)


class SigningIdentityRegistry:
    """Private append/update registry outside repositories; records non-secret identity metadata only."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = str(os.environ.get("DEVTOOL_SIGNING_IDENTITY_REGISTRY") or "").strip()
        self.path = Path(path).expanduser() if path else (Path(configured).expanduser() if configured else state_dir() / "signing" / "identities.json")

    def _load_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "identities": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Signing identity registry must contain a JSON object.")
        identities = data.get("identities")
        if not isinstance(identities, dict):
            raise ValueError("Signing identity registry identities must be an object.")
        return {"schema_version": int(data.get("schema_version") or 1), "identities": identities}

    def list(self) -> tuple[SigningIdentityRecord, ...]:
        data = self._load_payload()
        records = []
        for identity_id, raw in sorted(data["identities"].items()):
            if not isinstance(raw, Mapping):
                continue
            secret = raw.get("secret_provider") if isinstance(raw.get("secret_provider"), Mapping) else None
            records.append(SigningIdentityRecord(
                identity_id=SigningIdentityId(identity_id),
                provider=str(raw.get("provider") or ""),
                key_reference=str(raw.get("key_reference") or ""),
                alias=str(raw.get("alias") or ""),
                certificate_sha256=str(raw.get("certificate_sha256") or ""),
                purposes=tuple(str(x) for x in raw.get("purposes", []) if str(x)),
                variants=tuple(str(x) for x in raw.get("variants", []) if str(x)),
                secret_provider=SecretProviderRef(str(secret.get("kind")), str(secret.get("reference"))) if secret else None,
                policy_provenance=str(raw.get("policy_provenance") or ""),
                lifecycle=str(raw.get("lifecycle") or "durable"),
            ))
        return tuple(records)

    def get(self, identity_id: str | SigningIdentityId) -> SigningIdentityRecord | None:
        needle = str(identity_id)
        return next((item for item in self.list() if str(item.identity_id) == needle), None)

    def register(self, record: SigningIdentityRecord, *, replace: bool = False) -> SigningIdentityRecord:
        data = self._load_payload()
        key = str(record.identity_id)
        existing = data["identities"].get(key)
        serialized = record.as_dict()
        serialized.pop("identity_id", None)
        if existing is not None and existing != serialized and not replace:
            raise FileExistsError(f"Signing identity {key!r} already exists; pass replace=True for an intentional metadata update.")
        data["identities"][key] = serialized
        _reject_secret_fields(serialized)
        _atomic_private_json(self.path, data)
        return record

    def create_reference(
        self,
        *,
        provider: str,
        key_reference: str,
        alias: str = "",
        certificate_sha256: str = "",
        purposes: tuple[str, ...] = (),
        variants: tuple[str, ...] = (),
        secret_provider: SecretProviderRef | None = None,
        identity_id: str = "",
        lifecycle: str = "durable",
    ) -> SigningIdentityRecord:
        generated = identity_id.strip() or f"sign-{secrets.token_hex(12)}"
        record = SigningIdentityRecord(
            identity_id=SigningIdentityId(generated),
            provider=provider,
            key_reference=key_reference,
            alias=alias,
            certificate_sha256=certificate_sha256,
            purposes=purposes,
            variants=variants,
            secret_provider=secret_provider,
            lifecycle=lifecycle,
        )
        return self.register(record, replace=False)


__all__ = [
    "SecretProviderRef",
    "SigningEvidence",
    "SigningIdentityBinding",
    "SigningIdentityId",
    "SigningIdentityRecord",
    "SigningIdentityRegistry",
    "TrustPolicy",
    "assert_certificate_continuity",
    "merge_trust_policy",
    "stage_ephemeral_secret",
]
