from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .config_migration import migration_report
from .ui_core.machine import IPC_PROTOCOL_VERSION, MACHINE_SCHEMA_VERSION, TASKER_SCHEMA_VERSION
from .validation_sessions import SESSION_SCHEMA_VERSION

COMPATIBILITY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CompatibilityEntry:
    id: str
    category: str
    legacy: str
    canonical: str
    status: str
    migration: str
    notes: str = ""
    removal: str = "none-planned"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


COMPATIBILITY_ENTRIES: tuple[CompatibilityEntry, ...] = (
    CompatibilityEntry(
        "config.project.kind", "config", "[project].kind", "[project].runner + platform",
        "supported-alias", "devtool config migrate --write",
        "runner is the command planner; platform describes the product surface independently.",
    ),
    CompatibilityEntry(
        "config.target.kind", "config", "[targets.*].kind", "[targets.*].runner + platform",
        "supported-alias", "devtool config migrate --write",
    ),
    CompatibilityEntry(
        "config.target.type", "config", "[targets.*].type", "[targets.*].runner + platform",
        "supported-alias", "devtool config migrate --write",
    ),
    CompatibilityEntry(
        "config.project.default_target", "config", "[project].default_target", "[workspace].default_target",
        "supported-alias", "devtool config migrate --write",
    ),
    CompatibilityEntry(
        "config.ui.confirm_destructive", "config", "[ui].confirm_destructive", "[ui.confirmations]",
        "supported-shim", "devtool config migrate --write [--global]",
        "false maps to rollback/delete_artifact/snapshot_overwrite = never; true is the canonical default.",
    ),
    CompatibilityEntry(
        "config.ui.confirm_cancel", "config", "[ui].confirm_cancel", "[ui.confirmations].cancel",
        "supported-shim", "devtool config migrate --write [--global]",
        "true maps to cancel = always; false is the canonical default.",
    ),
    CompatibilityEntry(
        "cli.project_type", "cli", "--project-type", "--runner",
        "supported-alias", "replace --project-type with --runner in scripts when convenient",
    ),
    CompatibilityEntry(
        "cli.logs_print", "cli", "devtool logs --print", "devtool logs latest --print",
        "supported-alias", "replace the top-level --print form with the logs latest subcommand",
    ),
    CompatibilityEntry(
        "env.project_kind", "environment", "DEVTOOL_PROJECT_KIND", "DEVTOOL_PROJECT_RUNNER",
        "supported-alias", "replace DEVTOOL_PROJECT_KIND with DEVTOOL_PROJECT_RUNNER in new automation",
        "Both map through the project environment override layer; conflicting values are rejected.",
    ),
    CompatibilityEntry(
        "hud.gtk_window", "cli", "hud --mode gtk-window", "hud --mode desktop-window",
        "deprecated-alias", "replace gtk-window with desktop-window",
        "The alias still maps to the GTK4 HUD and emits a migration hint.",
    ),
    CompatibilityEntry(
        "python.DotnetTarget", "python-api", "devtool.models.DotnetTarget", "devtool.models.TargetConfig",
        "supported-alias", "import TargetConfig in new integrations",
    ),
    CompatibilityEntry(
        "machine.flat_v1", "machine", "legacy outer machine/HUD state fields", f"machine.schema_version = {MACHINE_SCHEMA_VERSION}",
        "supported-fallback", "prefer the nested machine object in new consumers",
    ),
    CompatibilityEntry(
        "ipc.cancel", "ipc", "cancel/cancel_ack", f"IPC v{IPC_PROTOCOL_VERSION} action/action_ack with operation.cancel",
        "supported-fallback", "new clients should dispatch semantic action IDs",
    ),
    CompatibilityEntry(
        "tasker.flat_fields", "tasker", "tasker_fields + legacy notification fields", f"tasker.schema_version = {TASKER_SCHEMA_VERSION} + tasker.fields",
        "supported-fallback", "prefer DEVTOOL_* semantic fields over parsing notification text",
    ),
    CompatibilityEntry(
        "android.bridge.v1", "android", "Android bridge protocol v1 legacy task/phase/status/progress extras", "additive UI Core semantic extras",
        "supported-fallback", "new companions prefer UI Core extras; protocol v1 remains interoperable",
    ),
    CompatibilityEntry(
        "artifact.manifest.repository_aliases", "artifact", "repo/repo_name/expected_repo", "repository.name",
        "supported-fallback", "new schema-v2 artifacts should declare repository.name",
        "Older overlay manifests remain inspectable/applicable when their legacy repository identity is unambiguous.",
    ),
    CompatibilityEntry(
        "artifact.manifest.commit_aliases", "artifact", "top-level commit/git/commit_message", "apply.commit",
        "supported-fallback", "new schema-v2 artifacts should declare apply.commit",
        "Legacy commit intent remains honored so replaying older overlay series preserves history.",
    ),
    CompatibilityEntry(
        "artifact.manifest.files", "artifact", "legacy files + .devtool-delete inventory", "schema-v2 inventory",
        "supported-fallback", "regenerate new artifacts with the schema-v2 inventory",
    ),
    CompatibilityEntry(
        "artifact.bundle.implicit", "artifact", "wrapper archives containing only ordered nested overlays", "devtool.overlay_bundle manifest + apply_order",
        "supported-fallback", "regenerate bundles with an explicit bundle manifest",
    ),
    CompatibilityEntry(
        "artifact.bundle.string_entries", "artifact", "string apply_order/overlays entries", "object apply_order entries",
        "supported-fallback", "use object entries with path/id/target metadata in new bundles",
    ),
    CompatibilityEntry(
        "artifact.history.v1_v2", "artifact", "repository-local artifact history/index records", "runtime operation history schema v3",
        "supported-fallback", "no manual migration required; older history remains readable",
    ),
    CompatibilityEntry(
        "validation.session.v1_v5", "validation", "validation session schemas v1-v5", f"validation session schema v{SESSION_SCHEMA_VERSION}",
        "automatic-migration", "no manual migration required; sessions upgrade in memory while preserving evidence",
    ),
    CompatibilityEntry(
        "state.transaction.active_singleton", "state", "legacy transaction active.json singleton marker", "per-transaction active/<transaction-id>.json markers",
        "supported-fallback", "no manual migration required; recovery reads both layouts",
    ),
    CompatibilityEntry(
        "workflow.ordered_lists", "workflow", "ordered string workflow lists", "explicit DAG step tables when branching/joining is needed",
        "supported-syntax", "no migration required",
        "Sequential lists remain canonical for simple workflows.",
    ),
)


def compatibility_entries(*, category: str = "") -> list[CompatibilityEntry]:
    wanted = str(category or "").strip().lower()
    return [entry for entry in COMPATIBILITY_ENTRIES if not wanted or entry.category == wanted]


def compatibility_report(
    config_path: Path | None = None,
    *,
    category: str = "",
    active_only: bool = False,
) -> dict[str, Any]:
    migration: dict[str, Any] = {
        "schema_version": 1,
        "path": str(config_path) if config_path else "",
        "needs_migration": False,
        "findings": [],
    }
    if config_path is not None and config_path.exists():
        migration = migration_report(config_path)

    active_ids = {str(item.get("compatibility_id") or "") for item in migration.get("findings", [])}
    entries = compatibility_entries(category=category)
    if active_only:
        entries = [entry for entry in entries if entry.id in active_ids]

    status_counts: dict[str, int] = {}
    for entry in entries:
        status_counts[entry.status] = status_counts.get(entry.status, 0) + 1
    return {
        "schema_version": COMPATIBILITY_SCHEMA_VERSION,
        "machine_schema_version": MACHINE_SCHEMA_VERSION,
        "ipc_protocol_version": IPC_PROTOCOL_VERSION,
        "tasker_schema_version": TASKER_SCHEMA_VERSION,
        "entries": [entry.as_dict() for entry in entries],
        "summary": {
            "entries": len(entries),
            "statuses": dict(sorted(status_counts.items())),
            "active_config_migrations": len(migration.get("findings", [])),
        },
        "config_migration": migration,
    }


def render_compatibility_text(payload: dict[str, Any]) -> str:
    lines = [
        f"compatibility schema: {payload.get('schema_version')}",
        f"machine schema: {payload.get('machine_schema_version')}",
        f"ipc protocol: {payload.get('ipc_protocol_version')}",
        f"tasker schema: {payload.get('tasker_schema_version')}",
    ]
    for item in payload.get("entries", []):
        lines.append(f"{item['id']}: {item['status']} | {item['legacy']} -> {item['canonical']}")
        lines.append(f"  migrate: {item['migration']}")
        if item.get("notes"):
            lines.append(f"  note: {item['notes']}")
    findings = payload.get("config_migration", {}).get("findings", [])
    if findings:
        lines.append("active config migrations:")
        for finding in findings:
            lines.append(
                f"  line {finding.get('line')}: {finding.get('compatibility_id')} "
                f"[{finding.get('section')}] {finding.get('key')} -> {finding.get('replacement_key')}"
            )
    else:
        lines.append("active config migrations: none")
    return "\n".join(lines)
