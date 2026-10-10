from __future__ import annotations

"""ANDROID-X02 Android execution, artifact, transport, and device authority.

The records in this module intentionally compose existing Devtool authorities:
Android environment truth from :mod:`devtool.android.environment`, Gradle truth
from ``GradleExecutionReceipt``, RUN V3 attachments, and Host-owned ADB/provider
selection.  Paths are retained as provenance but never become the sole semantic
identity for reusable Android work.
"""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence
import uuid

from ..core_runtime import private_run_root
from ..models import ProjectConfig
from ..run_store import AttachmentDescriptor, RunCatalog
from ..termux_toolchains import detect_runtime_identity
from .capabilities import AndroidToolResolution, resolve_adb
from .device_ops import AndroidDeviceOperations
from .devices import AndroidDevice, AndroidDeviceManager
from .environment import EnvironmentView, ResolvedAndroidEnvironment

ANDROID_EXECUTION_SCHEMA_VERSION = 1
_DEVICE_ACTIONS = {"device", "devices", "instrumentation", "install", "release-device", "smoke", "connected-test"}


def _stable(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def android_action_requires_device(action: str) -> bool:
    text = str(action or "").strip().lower().replace("_", "-")
    return text in _DEVICE_ACTIONS or text.startswith(("instrumentation-", "device-", "smoke-"))


@dataclass(frozen=True, slots=True)
class ArtifactMember:
    role: str
    artifact_type: str
    sha256: str
    size: int
    producer_task: str = ""
    producer_ref: str = ""
    run_attachment_ref: str = ""
    registry_id: str = ""
    path: str = ""
    source_path: str = ""

    @property
    def semantic_identity(self) -> str:
        # RUN attachment IDs, registry IDs and producer path refs are evidence
        # lineage/provenance. They can legitimately change when an equivalent
        # artifact is relocated, so the member's semantic identity is content +
        # producer role/task rather than path-derived evidence handles.
        return _stable({
            "role": self.role,
            "artifact_type": self.artifact_type,
            "sha256": self.sha256,
            "size": self.size,
            "producer_task": self.producer_task,
        })

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"semantic_identity": self.semantic_identity}


@dataclass(frozen=True, slots=True)
class ArtifactPair:
    pair_id: str
    module: str
    variant_identity: str
    environment_id: str
    source_identity: str
    config_identity: str
    gradle_receipt_id: str
    gradle_plan_digest: str
    app: ArtifactMember
    test: ArtifactMember | None = None
    signing_ref: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_EXECUTION_SCHEMA_VERSION,
            "pair_id": self.pair_id,
            "module": self.module,
            "variant_identity": self.variant_identity,
            "environment_id": self.environment_id,
            "source_identity": self.source_identity,
            "config_identity": self.config_identity,
            "gradle_receipt_id": self.gradle_receipt_id,
            "gradle_plan_digest": self.gradle_plan_digest,
            "app": self.app.as_dict(),
            "test": self.test.as_dict() if self.test else None,
            "signing_ref": self.signing_ref,
        }


@dataclass(frozen=True, slots=True)
class DeviceRequirements:
    requirements_id: str
    policy: str
    serial: str = ""
    preferred: str = ""
    min_api: int | None = None
    abis: tuple[str, ...] = ()
    transports: tuple[str, ...] = ()
    require_boot_completed: bool = True
    root_loopback: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_EXECUTION_SCHEMA_VERSION,
            "requirements_id": self.requirements_id,
            "policy": self.policy,
            "serial": self.serial,
            "preferred": self.preferred,
            "min_api": self.min_api,
            "abis": list(self.abis),
            "transports": list(self.transports),
            "require_boot_completed": self.require_boot_completed,
            "root_loopback": self.root_loopback,
        }


@dataclass(frozen=True, slots=True)
class AndroidTransport:
    transport_id: str
    destination_id: str
    provider: str
    adb_path: str
    adb_version: str
    runtime: str
    runtime_arch: str
    sdk_environment_id: str
    mode: str = "adb"
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_EXECUTION_SCHEMA_VERSION,
            "transport_id": self.transport_id,
            "destination_id": self.destination_id,
            "provider": self.provider,
            "adb_path": self.adb_path,
            "adb_version": self.adb_version,
            "runtime": self.runtime,
            "runtime_arch": self.runtime_arch,
            "sdk_environment_id": self.sdk_environment_id,
            "mode": self.mode,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class DeviceLease:
    lease_id: str
    attempt_id: str
    requirements_id: str
    transport_id: str
    serial: str
    transport_serial: str
    api_level: str
    abi: str
    connection_type: str
    model: str
    boot_completed: str
    freshness_id: str
    device: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_EXECUTION_SCHEMA_VERSION,
            "lease_id": self.lease_id,
            "attempt_id": self.attempt_id,
            "requirements_id": self.requirements_id,
            "transport_id": self.transport_id,
            "serial": self.serial,
            "transport_serial": self.transport_serial,
            "api_level": self.api_level,
            "abi": self.abi,
            "connection_type": self.connection_type,
            "model": self.model,
            "boot_completed": self.boot_completed,
            "freshness_id": self.freshness_id,
            "device": dict(self.device),
        }


@dataclass(frozen=True, slots=True)
class AdbdMutationLease:
    mutation_lease_id: str
    transport_id: str
    serial: str
    previous_port: int
    requested_port: int
    restore_required: bool
    provider_receipt: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AdbdMutationReceipt:
    receipt_id: str
    mutation_lease_id: str
    action: str
    ok: bool
    observed_port: int
    provider_receipt: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DestinationAndroidEnvironment:
    destination_id: str
    environment_id: str
    component_identities: dict[str, str]
    environment: dict[str, Any]
    returned_snapshot_id: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AndroidExecutionSnapshot:
    snapshot_id: str
    target: str
    action: str
    environment_id: str
    environment_components: dict[str, str]
    gradle_receipt_ids: tuple[str, ...]
    gradle_plan_digests: tuple[str, ...]
    gradle_context_digests: tuple[str, ...]
    jdk_identities: tuple[str, ...]
    launcher_identities: tuple[str, ...]
    artifact_pair: ArtifactPair | None = None
    device_lease: DeviceLease | None = None
    destination_id: str = "local"
    run_refs: tuple[str, ...] = ()
    rer_refs: tuple[str, ...] = ()
    exo_refs: tuple[str, ...] = ()
    source_identity: str = ""
    config_identity: str = ""
    signing_ref: str = ""
    path_provenance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_EXECUTION_SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "target": self.target,
            "action": self.action,
            "environment_id": self.environment_id,
            "environment_components": dict(self.environment_components),
            "gradle_receipt_ids": list(self.gradle_receipt_ids),
            "gradle_plan_digests": list(self.gradle_plan_digests),
            "gradle_context_digests": list(self.gradle_context_digests),
            "jdk_identities": list(self.jdk_identities),
            "launcher_identities": list(self.launcher_identities),
            "artifact_pair": self.artifact_pair.as_dict() if self.artifact_pair else None,
            "device_lease": self.device_lease.as_dict() if self.device_lease else None,
            "destination_id": self.destination_id,
            "run_refs": list(self.run_refs),
            "rer_refs": list(self.rer_refs),
            "exo_refs": list(self.exo_refs),
            "source_identity": self.source_identity,
            "config_identity": self.config_identity,
            "signing_ref": self.signing_ref,
            "path_provenance": dict(self.path_provenance),
        }


def _android_options(config: ProjectConfig) -> dict[str, Any]:
    raw = config.runner_options.get("android", {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def device_requirements_from_config(config: ProjectConfig, *, overrides: Mapping[str, Any] | None = None) -> DeviceRequirements:
    android = _android_options(config)
    adb_raw = android.get("adb", {})
    adb = dict(adb_raw) if isinstance(adb_raw, Mapping) else {}
    device_raw = android.get("device", {})
    device = dict(device_raw) if isinstance(device_raw, Mapping) else {}
    merged = {**device, **adb, **dict(overrides or {})}
    policy = str(merged.get("device_policy") or merged.get("policy") or "exactly-one").strip().lower()
    serial = str(merged.get("serial") or config.device_serial or "").strip()
    preferred = str(merged.get("preferred") or "").strip()
    min_api_raw = merged.get("min_api") or merged.get("api_min")
    try:
        min_api = int(min_api_raw) if min_api_raw not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError(f"Invalid Android device min_api: {min_api_raw!r}")
    abi_raw = merged.get("abis") or merged.get("abi") or ()
    if isinstance(abi_raw, str):
        abis = tuple(item.strip() for item in abi_raw.split(",") if item.strip())
    else:
        abis = tuple(str(item).strip() for item in abi_raw or () if str(item).strip())
    transport_raw = merged.get("transports") or merged.get("transport") or ()
    if isinstance(transport_raw, str):
        transports = tuple(item.strip().lower() for item in transport_raw.split(",") if item.strip())
    else:
        transports = tuple(str(item).strip().lower() for item in transport_raw or () if str(item).strip())
    root_loopback = bool(merged.get("root_loopback") or str(merged.get("mode") or "").strip().lower() == "root-loopback")
    require_boot_completed = bool(merged.get("require_boot_completed", True))
    payload = {
        "policy": policy, "serial": serial, "preferred": preferred, "min_api": min_api,
        "abis": list(abis), "transports": list(transports), "require_boot_completed": require_boot_completed,
        "root_loopback": root_loopback,
    }
    return DeviceRequirements(_stable(payload), policy, serial, preferred, min_api, abis, transports, require_boot_completed, root_loopback)


def resolve_android_transport(
    config: ProjectConfig,
    environment: ResolvedAndroidEnvironment,
    *,
    action: str,
    env_view: EnvironmentView,
    destination_id: str = "local",
    device_required: bool | None = None,
) -> AndroidTransport | None:
    required = android_action_requires_device(action) if device_required is None else bool(device_required)
    if not required:
        return None
    android = _android_options(config)
    adb_raw = android.get("adb", {})
    adb = dict(adb_raw) if isinstance(adb_raw, Mapping) else {}
    requested = str(adb.get("provider") or "auto").strip().lower()
    mode = "root-loopback" if requested == "root-loopback" or bool(adb.get("root_loopback")) else "adb"
    low_provider = "auto" if requested == "root-loopback" else requested
    runtime = detect_runtime_identity(env_view.values)
    resolution = resolve_adb(
        sdk_root=Path(environment.sdk_root) if environment.sdk_root else None,
        provider=low_provider,
        explicit_path=str(adb.get("path") or "") or None,
        env=env_view.values,
        runtime=runtime,
    )
    if not resolution.available or resolution.compatible is False or not resolution.path:
        raise RuntimeError(resolution.rejection_reason or "Android device transport is unavailable")
    semantic = {
        "destination_id": destination_id,
        "provider": resolution.provider,
        "version": resolution.version,
        "runtime": runtime.environment,
        "arch": runtime.arch,
        "environment_id": environment.environment_id,
        "mode": mode,
    }
    return AndroidTransport(
        transport_id=_stable(semantic), destination_id=destination_id, provider=resolution.provider,
        adb_path=str(resolution.path), adb_version=str(resolution.version or ""), runtime=runtime.environment,
        runtime_arch=runtime.arch, sdk_environment_id=environment.environment_id, mode=mode,
        provenance={
            "selection_reason": resolution.selection_reason,
            "provenance": resolution.provenance,
            "metadata": dict(resolution.metadata or {}),
        },
    )


def _tool_for_transport(transport: AndroidTransport) -> AndroidToolResolution:
    return AndroidToolResolution(
        "adb", transport.provider, True, path=transport.adb_path, version=transport.adb_version,
        compatible=True, selection_reason=str(transport.provenance.get("selection_reason") or ""),
        provenance=str(transport.provenance.get("provenance") or ""), runtime=transport.runtime,
        metadata=dict(transport.provenance.get("metadata") or {}),
    )


def _validate_device(device: AndroidDevice, requirements: DeviceRequirements) -> None:
    if requirements.min_api is not None:
        try:
            api = int(device.api_level or 0)
        except ValueError:
            api = 0
        if api < requirements.min_api:
            raise RuntimeError(f"Android device {device.serial} API {api or 'unknown'} is below required API {requirements.min_api}")
    if requirements.abis and device.abi not in requirements.abis:
        raise RuntimeError(f"Android device {device.serial} ABI {device.abi or 'unknown'} is not one of {', '.join(requirements.abis)}")
    if requirements.transports and device.connection_type not in requirements.transports:
        raise RuntimeError(
            f"Android device {device.serial} transport {device.connection_type or 'unknown'} is not one of {', '.join(requirements.transports)}"
        )
    if requirements.require_boot_completed and device.boot_completed not in {"", "1"}:
        raise RuntimeError(f"Android device {device.serial} has not completed boot")


def acquire_device_lease(
    transport: AndroidTransport,
    requirements: DeviceRequirements,
    *,
    env: Mapping[str, str] | None = None,
    attempt_id: str = "",
    manager: AndroidDeviceManager | None = None,
) -> DeviceLease:
    env_map = dict(os.environ if env is None else env)
    manager = manager or AndroidDeviceManager(_tool_for_transport(transport), env=env_map)
    selected = manager.select(
        requirements.policy if not requirements.serial else "serial",
        serial=requirements.serial,
        preferred=requirements.preferred,
    )
    if len(selected) != 1:
        raise RuntimeError("Android DeviceLease requires exactly one selected device")
    device = selected[0]
    try:
        enriched = [item for item in manager.list_devices(enrich=True) if item.serial == device.serial]
        if enriched:
            device = enriched[0]
    except Exception:
        # Selection remains authoritative; constraints that depend on missing
        # enrichment below fail closed instead of changing device identity.
        pass
    _validate_device(device, requirements)
    attempt = str(attempt_id or env_map.get("DEVTOOL_RUN_ID") or uuid.uuid4().hex)
    freshness = _stable({
        "attempt": attempt,
        "serial": device.serial,
        "transport_id": device.transport_id,
        "boot_completed": device.boot_completed,
        "api": device.api_level,
        "abi": device.abi,
        "connection_type": device.connection_type,
    })
    semantic = {
        "attempt": attempt, "requirements_id": requirements.requirements_id,
        "transport_id": transport.transport_id, "serial": device.serial,
        "transport_serial": device.transport_id, "freshness": freshness,
    }
    return DeviceLease(
        lease_id=_stable(semantic), attempt_id=attempt, requirements_id=requirements.requirements_id,
        transport_id=transport.transport_id, serial=device.serial, transport_serial=device.transport_id,
        api_level=device.api_level, abi=device.abi, connection_type=device.connection_type, model=device.model,
        boot_completed=device.boot_completed, freshness_id=freshness, device=device.as_dict(),
    )


def operations_for_device_lease(
    transport: AndroidTransport,
    lease: DeviceLease,
    *,
    env: Mapping[str, str] | None = None,
) -> AndroidDeviceOperations:
    manager = AndroidDeviceManager(_tool_for_transport(transport), env=env)
    return AndroidDeviceOperations(
        manager, env=env, default_policy="serial", default_serial=lease.serial, preferred=lease.serial,
        device_lease=lease,
    )


def _artifact_member(
    raw: Mapping[str, Any],
    *,
    role: str,
    repo_root: Path,
    gradle_receipt: Mapping[str, Any] | None,
    run_catalog: RunCatalog | None,
    run_id: str,
) -> ArtifactMember:
    value = str(raw.get("verified_path") or raw.get("path") or "").strip()
    if not value:
        raise ValueError(f"{role} artifact has no path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve(strict=False)
    if not path.is_file():
        raise FileNotFoundError(f"{role} artifact is not materialized: {path}")
    actual = _sha_file(path)
    declared = str(raw.get("current_sha256") or raw.get("sha256") or "").strip().lower()
    if declared and declared != actual:
        raise ValueError(f"{role} artifact hash drift: expected {declared}, got {actual}")
    kind = str(raw.get("type") or path.suffix.lstrip(".") or "file").strip().lower()
    if role in {"app", "test"} and kind != "apk":
        raise ValueError(f"{role} artifact must be an APK, got {kind!r}")
    producer = str(raw.get("producer_task") or "").strip()
    raw_ref = str(raw.get("attachment_id") or raw.get("ref_id") or raw.get("registry_id") or raw.get("path") or "").strip()
    receipt = dict(gradle_receipt or {})
    receipt_refs = {str(x) for x in receipt.get("artifact_refs") or () if str(x)}
    producer_refs = {str(x) for x in dict(receipt.get("producer_artifact_refs") or {}).get(producer, ()) if str(x)} if producer else set()
    if receipt_refs and raw_ref not in receipt_refs and str(raw.get("path") or "") not in receipt_refs and str(path) not in receipt_refs:
        raise ValueError(f"{role} artifact is not linked by the Gradle execution receipt")
    if producer_refs and raw_ref not in producer_refs and str(raw.get("path") or "") not in producer_refs and str(path) not in producer_refs:
        raise ValueError(f"{role} artifact does not match producer receipt {producer!r}")

    attachment_ref = str(raw.get("attachment_id") or "").strip()
    if run_catalog is not None:
        if attachment_ref:
            descriptor = run_catalog.attachments.get(attachment_ref)
            if descriptor is None or descriptor.sha256 != actual or descriptor.state == "tombstoned":
                raise ValueError(f"{role} artifact RUN attachment is missing, stale, or hash-mismatched")
        elif run_id:
            descriptor = AttachmentDescriptor.from_file(
                run_id=run_id, path=path, kind=f"android-{role}-apk", privacy="private",
                media_type="application/vnd.android.package-archive",
            )
            run_catalog.add_attachment(descriptor)
            attachment_ref = descriptor.attachment_id

    return ArtifactMember(
        role=role, artifact_type=kind, sha256=actual, size=path.stat().st_size,
        producer_task=producer, producer_ref=raw_ref, run_attachment_ref=attachment_ref,
        registry_id=str(raw.get("registry_id") or ""), path=str(path), source_path=str(raw.get("source_path") or ""),
    )


def build_artifact_pair(
    *,
    repo_root: Path,
    module: str,
    variant_identity: str,
    environment: ResolvedAndroidEnvironment,
    app: Mapping[str, Any],
    test: Mapping[str, Any] | None = None,
    gradle_receipt: Mapping[str, Any] | None = None,
    source_identity: str = "",
    config_identity: str = "",
    signing_ref: str = "",
    run_catalog: RunCatalog | None = None,
    run_id: str = "",
) -> ArtifactPair:
    receipt = dict(gradle_receipt or {})
    app_member = _artifact_member(app, role="app", repo_root=repo_root, gradle_receipt=receipt, run_catalog=run_catalog, run_id=run_id)
    test_member = _artifact_member(test, role="test", repo_root=repo_root, gradle_receipt=receipt, run_catalog=run_catalog, run_id=run_id) if test else None
    semantic = {
        "module": module,
        "variant_identity": variant_identity,
        "environment_id": environment.environment_id,
        "source_identity": source_identity,
        "config_identity": config_identity,
        # receipt_id can include path-shaped artifact references. Preserve the
        # exact receipt on the record but base ArtifactPair semantic identity on
        # the canonical Gradle plan digest plus verified artifact content.
        "gradle_plan_digest": str(receipt.get("plan_digest") or ""),
        "app": app_member.semantic_identity,
        "test": test_member.semantic_identity if test_member else "",
        "signing_ref": signing_ref,
    }
    return ArtifactPair(
        pair_id=_stable(semantic), module=module, variant_identity=variant_identity,
        environment_id=environment.environment_id, source_identity=source_identity, config_identity=config_identity,
        gradle_receipt_id=str(receipt.get("receipt_id") or ""), gradle_plan_digest=str(receipt.get("plan_digest") or ""),
        app=app_member, test=test_member, signing_ref=signing_ref,
    )


def build_android_execution_snapshot(
    *,
    target: str,
    action: str,
    environment: ResolvedAndroidEnvironment,
    gradle_receipts: Sequence[Mapping[str, Any]] = (),
    artifact_pair: ArtifactPair | None = None,
    device_lease: DeviceLease | None = None,
    destination_id: str = "local",
    source_identity: str = "",
    config_identity: str = "",
    signing_ref: str = "",
) -> AndroidExecutionSnapshot:
    receipts = [dict(item) for item in gradle_receipts if isinstance(item, Mapping)]
    receipt_ids = tuple(dict.fromkeys(str(item.get("receipt_id") or "") for item in receipts if str(item.get("receipt_id") or "")))
    plans = tuple(dict.fromkeys(str(item.get("plan_digest") or "") for item in receipts if str(item.get("plan_digest") or "")))
    contexts = tuple(dict.fromkeys(str(item.get("context_digest") or "") for item in receipts if str(item.get("context_digest") or "")))
    jdks = tuple(dict.fromkeys(str(item.get("jdk_identity") or "") for item in receipts if str(item.get("jdk_identity") or "")))
    launchers = tuple(dict.fromkeys(str(item.get("launcher_identity") or "") for item in receipts if str(item.get("launcher_identity") or "")))
    run_refs = tuple(dict.fromkeys(str(item.get("run_ref") or "") for item in receipts if str(item.get("run_ref") or "")))
    rer_refs = tuple(dict.fromkeys(str(item.get("rer_ref") or "") for item in receipts if str(item.get("rer_ref") or "")))
    exo_refs = tuple(dict.fromkeys(str(item.get("exo_run_ref") or "") for item in receipts if str(item.get("exo_run_ref") or "")))
    semantic = {
        "target": target, "action": action, "environment_id": environment.environment_id,
        "components": dict(environment.component_identities), "gradle_receipts": list(receipt_ids),
        "gradle_plans": list(plans), "artifact_pair": artifact_pair.pair_id if artifact_pair else "",
        "device_lease": device_lease.lease_id if device_lease else "", "destination_id": destination_id,
        "source_identity": source_identity, "config_identity": config_identity, "signing_ref": signing_ref,
    }
    return AndroidExecutionSnapshot(
        snapshot_id=_stable(semantic), target=target, action=action, environment_id=environment.environment_id,
        environment_components=dict(environment.component_identities), gradle_receipt_ids=receipt_ids,
        gradle_plan_digests=plans, gradle_context_digests=contexts, jdk_identities=jdks, launcher_identities=launchers,
        artifact_pair=artifact_pair, device_lease=device_lease, destination_id=destination_id,
        run_refs=run_refs, rer_refs=rer_refs, exo_refs=exo_refs, source_identity=source_identity,
        config_identity=config_identity, signing_ref=signing_ref,
        path_provenance={"android_environment": dict(environment.path_provenance)},
    )


def persist_android_execution_snapshot(repo_root: Path, run_id: str, snapshot: AndroidExecutionSnapshot) -> tuple[Path, str]:
    root = private_run_root(Path(repo_root))
    target = root / run_id / "android-execution" / f"{snapshot.snapshot_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(snapshot.as_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    if target.exists():
        if target.read_bytes() != encoded:
            raise FileExistsError(f"Android execution snapshot collision: {target}")
    else:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    catalog = RunCatalog.open(root)
    if run_id not in catalog.runs:
        catalog.add_run({"run_id": run_id, "command_id": "android.execution", "target": snapshot.target, "status": "recorded"})
    attachment = AttachmentDescriptor.from_file(
        run_id=run_id, path=target, kind="android-execution-snapshot", privacy="private", media_type="application/json",
    )
    catalog.add_attachment(attachment)
    catalog.save()
    return target, attachment.attachment_id


def consume_destination_android_environment(payload: Mapping[str, Any], *, destination_id: str) -> DestinationAndroidEnvironment:
    """Accept destination-produced evidence only when its claimed owner matches.

    A controller-supplied ``destination_id`` cannot itself attest the returned
    environment: the producer must identify the destination in its payload.
    The SSH/provider transport still owns authentication of that assertion.
    """
    expected = str(destination_id or "").strip()
    if not expected:
        raise ValueError("Destination Android environment requires a destination identity")
    nested = isinstance(payload.get("android_environment"), Mapping)
    raw = payload.get("android_environment") if nested else payload
    env = dict(raw) if isinstance(raw, Mapping) else {}
    outer_owner = str(payload.get("destination_id") or "").strip() if nested else ""
    inner_owner = str(env.get("destination_id") or "").strip()
    if not (outer_owner or inner_owner) or any(
        candidate != expected for candidate in (outer_owner, inner_owner) if candidate
    ):
        raise ValueError("Destination Android environment identity does not match requested destination")
    environment_id = str(env.get("environment_id") or "").strip()
    components = env.get("component_identities")
    if (
        not environment_id or not isinstance(components, Mapping) or not components
        or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
               for k, v in components.items())
    ):
        raise ValueError("Destination Android environment payload is missing canonical environment/component identity")
    canonical_components = {k: v for k, v in components.items()}
    returned = _stable({"destination_id": expected, "environment_id": environment_id, "components": canonical_components})
    return DestinationAndroidEnvironment(expected, environment_id, canonical_components, env, returned)


def acquire_adbd_mutation_lease(
    transport: AndroidTransport,
    *,
    serial: str,
    requested_port: int = 5555,
    mutator: Callable[[str, int], Mapping[str, Any]],
) -> tuple[AdbdMutationLease, AdbdMutationReceipt]:
    """Acquire a root-loopback adbd mutation through an injected Host owner.

    X02 intentionally does not run ``su``/``sudo`` itself.  The injected
    ``mutator`` is the Host/ENV authority and must return the observed previous
    and current port plus any privilege/provider evidence.
    """
    if transport.mode != "root-loopback":
        raise ValueError("adbd mutation leases require root-loopback transport mode")
    raw = dict(mutator(serial, int(requested_port)) or {})
    previous = int(raw.get("previous_port") or 0)
    observed = int(raw.get("observed_port") or requested_port)
    ok = bool(raw.get("ok", observed == requested_port))
    lease_payload = {
        "transport_id": transport.transport_id, "serial": serial, "previous_port": previous,
        "requested_port": int(requested_port), "provider_receipt": raw.get("provider_receipt") or {},
    }
    lease = AdbdMutationLease(
        mutation_lease_id=_stable(lease_payload), transport_id=transport.transport_id, serial=serial,
        previous_port=previous, requested_port=int(requested_port), restore_required=previous != int(requested_port),
        provider_receipt=dict(raw.get("provider_receipt") or {}),
    )
    receipt_payload = {"lease": lease.mutation_lease_id, "action": "acquire", "ok": ok, "observed_port": observed}
    receipt = AdbdMutationReceipt(_stable(receipt_payload), lease.mutation_lease_id, "acquire", ok, observed, dict(raw.get("provider_receipt") or {}))
    if not ok:
        raise RuntimeError(f"Host adbd mutation did not reach requested TCP port {requested_port}")
    return lease, receipt


def restore_adbd_mutation_lease(
    lease: AdbdMutationLease,
    *,
    restorer: Callable[[str, int], Mapping[str, Any]],
) -> AdbdMutationReceipt:
    raw = dict(restorer(lease.serial, int(lease.previous_port)) or {})
    observed = int(raw.get("observed_port") or lease.previous_port)
    ok = bool(raw.get("ok", observed == lease.previous_port))
    payload = {"lease": lease.mutation_lease_id, "action": "restore", "ok": ok, "observed_port": observed}
    return AdbdMutationReceipt(_stable(payload), lease.mutation_lease_id, "restore", ok, observed, dict(raw.get("provider_receipt") or {}))


__all__ = [
    "ANDROID_EXECUTION_SCHEMA_VERSION", "ArtifactMember", "ArtifactPair", "DeviceRequirements", "AndroidTransport",
    "DeviceLease", "AdbdMutationLease", "AdbdMutationReceipt", "DestinationAndroidEnvironment", "AndroidExecutionSnapshot",
    "android_action_requires_device", "device_requirements_from_config", "resolve_android_transport", "acquire_device_lease",
    "operations_for_device_lease", "build_artifact_pair", "build_android_execution_snapshot", "persist_android_execution_snapshot",
    "consume_destination_android_environment", "acquire_adbd_mutation_lease", "restore_adbd_mutation_lease",
]
