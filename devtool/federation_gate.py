from __future__ import annotations

"""FRX-04 repository-boundary and identity validation gate.

The gate is deliberately read-only.  It proves that the repository identity,
explicit federation enrollment, child-owned config boundary, public wrapper / 
target-local discovery surfaces, and EXO repository metadata all agree before
later FRX overlays are allowed to introduce cross-repository planning.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .core.events import EventBus, RunStarted
from .federation_discovery import FederationDiscoveryError, resolve_federated_repository
from .repository_identity import same_repository_identity
from .target_local_adoption import target_local_adoption_report
from .wrapper_help import wrapper_discovery_payload

if TYPE_CHECKING:
    from .models import ProjectConfig

FEDERATION_GATE1_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FederationGateCheck:
    section: str
    name: str
    status: str
    detail: str
    repository: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.repository:
            payload.pop("repository", None)
        if not self.data:
            payload.pop("data", None)
        return payload


@dataclass(frozen=True, slots=True)
class FederationGateReport:
    schema_version: int
    gate: str
    repository: str
    mode: str
    status: str
    checks: tuple[FederationGateCheck, ...]

    @property
    def ok(self) -> bool:
        return self.status != "error"


def _check(
    section: str,
    name: str,
    status: str,
    detail: str,
    *,
    repository: str = "",
    **data: Any,
) -> FederationGateCheck:
    return FederationGateCheck(
        section=section,
        name=name,
        status=status,
        detail=detail,
        repository=repository,
        data={key: value for key, value in data.items() if value not in (None, "", [], {}, ())},
    )


def _overall(checks: list[FederationGateCheck]) -> str:
    if any(item.status == "error" for item in checks):
        return "error"
    if any(item.status == "warning" for item in checks):
        return "warning"
    return "ok"


def _canonical(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _load_child_config(resolution: Any) -> "ProjectConfig":
    # Import locally to avoid config -> federation_config/model import cycles.
    from .config import load_project_config

    config_path = str(resolution.config_path or "") or None
    return load_project_config(
        str(resolution.root),
        config_path,
        target_name="",
        variant_name="",
        allow_ambiguous_target=True,
        allow_ambiguous_project_kind=True,
        allow_parent_config=False,
    )


def _exo_identity_probe(config: "ProjectConfig", *, run_id: str) -> tuple[bool, dict[str, Any]]:
    identity = config.repository_identity
    if identity is None:
        return False, {}
    bus = EventBus()
    bus.publish(
        RunStarted(
            run_id=run_id,
            command="federation-gate1-probe",
            project=str(config.repo_root),
            source="federation-gate1",
            metadata={"repository_identity": identity.as_dict()},
        )
    )
    snapshot = bus.execution_store.snapshot(run_id)
    payload = dict(getattr(snapshot, "repository_identity", {}) or {}) if snapshot is not None else {}
    return payload == identity.as_dict(), payload


def _append_project_surface_checks(
    checks: list[FederationGateCheck],
    config: "ProjectConfig",
    *,
    label: str,
) -> None:
    identity = config.repository_identity
    root = str(identity.root if identity is not None else config.repo_root)

    try:
        wrapper = wrapper_discovery_payload(config)
    except Exception as exc:  # fail closed: discovery itself must be safe/read-only
        checks.append(_check("public-contract", f"{label}:wrapper", "error", f"wrapper discovery failed: {exc}", repository=label))
    else:
        wrapper_repo = _canonical(str(wrapper.get("repository") or config.repo_root))
        requested = _canonical(config.repo_root)
        ok = wrapper_repo == requested
        checks.append(_check(
            "public-contract",
            f"{label}:wrapper",
            "ok" if ok else "error",
            "wrapper discovery remains repository-local" if ok else "wrapper discovery escaped the selected repository",
            repository=label,
            command_count=len(wrapper.get("commands") or []),
            wrapper_repository=str(wrapper_repo),
        ))

    try:
        tlj = target_local_adoption_report(config)
    except Exception as exc:
        checks.append(_check("public-contract", f"{label}:target-local-jobs", "error", f"target-local job discovery failed: {exc}", repository=label))
    else:
        summary = tlj.get("summary") if isinstance(tlj.get("summary"), dict) else {}
        checks.append(_check(
            "public-contract",
            f"{label}:target-local-jobs",
            "ok",
            "target-local job discovery is readable without execution",
            repository=label,
            target_count=int(summary.get("targets") or 0),
            target_local_job_count=int(summary.get("target_local_job_count") or 0),
        ))

    try:
        exo_ok, persisted = _exo_identity_probe(config, run_id=f"frx04-{label.replace(':', '-')}")
    except Exception as exc:
        checks.append(_check("exo", f"{label}:repository-identity", "error", f"EXO identity probe failed: {exc}", repository=label))
    else:
        checks.append(_check(
            "exo",
            f"{label}:repository-identity",
            "ok" if exo_ok else "error",
            "repository identity persists through EXO store" if exo_ok else "repository identity did not persist through EXO store",
            repository=label,
            repository_root=root,
            persisted_fingerprint=str(persisted.get("fingerprint") or ""),
        ))


def build_federation_gate1_report(config: "ProjectConfig") -> FederationGateReport:
    checks: list[FederationGateCheck] = []
    owner_identity = config.repository_identity
    owner_root = _canonical(owner_identity.root if owner_identity is not None else config.repo_root)

    if owner_identity is None:
        checks.append(_check("identity", "owner", "error", "repository identity is missing"))
    else:
        requested = _canonical(owner_identity.requested_root or config.repo_root)
        owned = _canonical(owner_identity.root)
        try:
            requested.relative_to(owned)
        except ValueError:
            ownership_ok = False
        else:
            ownership_ok = True
        checks.append(_check(
            "identity",
            "owner",
            "ok" if ownership_ok else "error",
            "repository/config ownership is deterministic" if ownership_ok else "requested repository root is outside the config owner",
            repository="owner",
            root=str(owned),
            requested_root=str(requested),
            fingerprint=owner_identity.fingerprint,
            config_path=owner_identity.config_path,
        ))

    checks.append(_check(
        "federation",
        "schema",
        "ok",
        f"schema {config.federation.schema}; explicit enrollment only",
        enabled=config.federation.enabled,
        repository_count=len(config.federation.repositories),
        group_count=len(config.federation.groups),
    ))

    _append_project_surface_checks(checks, config, label="owner")

    try:
        from .federation_capabilities import build_capability_graph
        capability_graph = build_capability_graph(config, strict=True)
    except Exception as exc:
        checks.append(_check(
            "capabilities",
            "dependency-graph",
            "error",
            f"capability dependency preflight failed: {exc}",
        ))
    else:
        checks.append(_check(
            "capabilities",
            "dependency-graph",
            "ok",
            "mandatory capability requirements resolve to exactly one enrolled provider",
            edge_count=len(capability_graph.edges),
            requirement_count=len(capability_graph.resolutions),
        ))

    # FRX-Y06: interface conformance is read-only and parent-owned. A bound
    # interface is a preflight contract, never permission to mutate child
    # configuration into conformance.
    try:
        from .federation_interfaces import all_interface_reports
        interface_reports = all_interface_reports(config)
    except Exception as exc:
        checks.append(_check(
            "interfaces",
            "conformance",
            "error",
            f"federation interface conformance failed: {exc}",
        ))
    else:
        for report in interface_reports:
            checks.append(_check(
                "interfaces",
                f"interface:{report.interface}:{','.join(report.groups) or '-'}",
                "ok" if report.ok else "error",
                "all bound repositories conform to the parent-owned public interface"
                if report.ok else "one or more bound repositories do not conform to the public interface",
                interface=report.interface,
                groups=list(report.groups),
                members=[member.as_dict() for member in report.members],
            ))

    seen_fingerprints: dict[str, str] = {}
    if owner_identity is not None and owner_identity.fingerprint:
        seen_fingerprints[owner_identity.fingerprint] = "owner"

    for name in sorted(config.federation.repositories):
        enrollment = config.federation.repositories[name]
        try:
            resolution = resolve_federated_repository(config, name)
        except FederationDiscoveryError as exc:
            checks.append(_check("boundary", f"repository:{name}", "error", str(exc), repository=name))
            continue

        if not resolution.available:
            status = "error" if enrollment.required else "optional"
            checks.append(_check(
                "boundary",
                f"repository:{name}",
                status,
                resolution.boundary_detail or "repository unavailable",
                repository=name,
                root=resolution.root,
                required=enrollment.required,
            ))
            continue

        identity = resolution.repository_identity
        if identity is None:
            checks.append(_check("identity", f"repository:{name}", "error", "child repository identity is missing", repository=name))
            continue

        child_root = _canonical(resolution.root)
        identity_root = _canonical(identity.root)
        config_path = _canonical(resolution.config_path) if resolution.config_path else None
        expected_config = child_root / ".devtool.toml"
        config_owned = identity_root == child_root and (
            config_path is None or config_path == expected_config.resolve(strict=False)
        )
        checks.append(_check(
            "boundary",
            f"repository:{name}",
            "ok" if config_owned else "error",
            "child root/config ownership is isolated" if config_owned else "child config ownership escaped its enrolled root",
            repository=name,
            root=str(child_root),
            config_path=str(config_path) if config_path is not None else "",
            relation=resolution.relation,
            selected_target=resolution.selected_target,
        ))

        distinct = owner_identity is None or not same_repository_identity(owner_identity, identity)
        duplicate = bool(identity.fingerprint and identity.fingerprint in seen_fingerprints)
        identity_ok = distinct and not duplicate
        checks.append(_check(
            "identity",
            f"repository:{name}",
            "ok" if identity_ok else "error",
            "child identity is distinct and unambiguous" if identity_ok else "child identity collides with another federation repository",
            repository=name,
            fingerprint=identity.fingerprint,
            root=identity.root,
            duplicate_of=seen_fingerprints.get(identity.fingerprint, "") if duplicate else "",
        ))
        if identity.fingerprint:
            seen_fingerprints.setdefault(identity.fingerprint, name)

        try:
            child = _load_child_config(resolution)
        except (SystemExit, Exception) as exc:
            checks.append(_check("boundary", f"repository:{name}:reload", "error", f"isolated child config reload failed: {exc}", repository=name))
            continue

        reloaded = child.repository_identity
        reload_ok = bool(
            reloaded is not None
            and same_repository_identity(identity, reloaded)
            and _canonical(reloaded.root) == child_root
        )
        checks.append(_check(
            "boundary",
            f"repository:{name}:reload",
            "ok" if reload_ok else "error",
            "isolated reload resolves the same child identity" if reload_ok else "isolated reload changed child identity",
            repository=name,
            selected_target=child.selected_target,
        ))

        _append_project_surface_checks(checks, child, label=f"child:{name}")

    mode = "federated" if config.federation.repositories else "standalone"
    if mode == "standalone":
        checks.append(_check(
            "compatibility",
            "standalone",
            "ok",
            "no federation enrollment; legacy standalone repository behavior remains active",
        ))
    else:
        checks.append(_check(
            "compatibility",
            "standalone",
            "ok",
            "federation is opt-in; enrollment is confined to the repository-owned federation section",
        ))

    return FederationGateReport(
        schema_version=FEDERATION_GATE1_SCHEMA_VERSION,
        gate="FRX-04",
        repository=str(owner_root),
        mode=mode,
        status=_overall(checks),
        checks=tuple(checks),
    )


def federation_gate1_as_dict(report: FederationGateReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "gate": report.gate,
        "repository": report.repository,
        "mode": report.mode,
        "status": report.status,
        "ok": report.ok,
        "summary": {
            "checks": len(report.checks),
            "errors": sum(item.status == "error" for item in report.checks),
            "warnings": sum(item.status == "warning" for item in report.checks),
            "optional": sum(item.status == "optional" for item in report.checks),
        },
        "checks": [item.as_dict() for item in report.checks],
    }


def render_federation_gate1(report: FederationGateReport) -> str:
    lines = [
        f"Federation boundary preflight · {report.gate}",
        f"Repository: {report.repository}",
        f"Mode: {report.mode}",
        f"Status: {report.status}",
        "",
    ]
    for item in report.checks:
        owner = f" [{item.repository}]" if item.repository else ""
        lines.append(f"{item.status.upper():8} {item.section}/{item.name}{owner}: {item.detail}")
    return "\n".join(lines) + "\n"


__all__ = [
    "FEDERATION_GATE1_SCHEMA_VERSION",
    "FederationGateCheck",
    "FederationGateReport",
    "build_federation_gate1_report",
    "federation_gate1_as_dict",
    "render_federation_gate1",
]
