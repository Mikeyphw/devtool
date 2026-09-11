from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

COPY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CopySpec:
    id: str
    label: str
    short: str = ""
    notification: str = ""
    description: str = ""
    surface: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def text(self, variant: str = "label", *, surface: str = "") -> str:
        if surface:
            override = dict(self.surface).get(surface)
            if override and variant == "label":
                return override
        if variant == "short":
            return self.short or self.label
        if variant == "notification":
            return self.notification or self.short or self.label
        if variant == "description":
            return self.description
        return self.label


_SPECS = (
    CopySpec("operation.cancel", "Cancel operation", "Cancel", "Stop", "Request cancellation of the active run."),
    CopySpec("operation.retry", "Retry operation", "Retry", "Retry", "Retry the failed or cancelled operation."),
    CopySpec("operation.rerun", "Run again", "Run again", "Run again", "Repeat the completed operation with the same selection."),
    CopySpec("logs.open", "Open log", "Open log", "Open log", "Open the run log."),
    CopySpec("summary.copy", "Copy summary", "Copy", "Copy summary", "Copy the canonical run summary."),
    CopySpec("diagnostics.copy", "Copy diagnostics", "Copy issues", "Copy issues", "Copy canonical diagnostics for the run."),
    CopySpec("artifact.apply", "Apply artifact", "Apply", "Apply", "Apply the selected overlay or snapshot."),
    CopySpec("artifact.rollback", "Rollback changes", "Rollback", "Rollback", "Restore the repository from the active apply transaction."),
    CopySpec("validation.retry_failed", "Retry failed validation", "Retry failed", "Retry failed", "Retry only failed validation phases."),
    CopySpec("validation.resume", "Resume validation", "Resume", "Resume", "Resume a paused or resumable validation session."),
    CopySpec("target.build", "Build target", "Build", "Build"),
    CopySpec("target.test", "Test target", "Tests", "Tests"),
    CopySpec("target.validate", "Validate target", "Validate", "Validate"),
    CopySpec("target.package", "Package target", "Package", "Package"),
    CopySpec("workflow.inspect", "Inspect workflow", "Inspect", "Inspect"),
    CopySpec("workflow.retry_node", "Retry workflow node", "Retry node", "Retry node"),
    CopySpec("tests.select", "Select tests", "Select tests", "Select tests"),
    CopySpec("tests.clear_selection", "Clear test selection", "Clear tests", "Clear"),
    CopySpec("tests.refresh_collection", "Refresh test collection", "Refresh tests", "Refresh"),
    CopySpec("nav.dashboard", "Overview", "Overview", surface=(("tui", "Dashboard"),)),
    CopySpec("nav.operations", "Active runs", "Runs"),
    CopySpec("nav.history", "History", "History"),
    CopySpec("nav.artifacts", "Artifacts", "Artifacts"),
    CopySpec("nav.diagnostics", "Diagnostics", "Issues"),
    CopySpec("nav.projects", "Repository", "Repo"),
    CopySpec("nav.targets", "Targets", "Targets"),
    CopySpec("nav.validation", "Run", "Run"),
    CopySpec("nav.workflows", "Workflows", "Workflows"),
    CopySpec("nav.logs", "Logs", "Logs"),
    CopySpec("nav.environment", "Environment", "Environment"),
    CopySpec("nav.settings", "Settings", "Settings"),
    CopySpec("nav.build", "Build", "Build"),
    CopySpec("nav.overlays", "Overlays", "Overlays"),
    CopySpec("status.idle", "Waiting", "Waiting"),
    CopySpec("status.success", "Succeeded", "Success", surface=(("hud", "Complete"),)),
    CopySpec("status.running", "Running", "Running"),
    CopySpec("status.pending", "Pending", "Pending"),
    CopySpec("status.failed", "Failed", "Failed"),
    CopySpec("status.warning", "Warnings", "Warnings"),
    CopySpec("status.skipped", "Skipped", "Skipped"),
    CopySpec("status.cancelled", "Cancelled", "Cancelled"),
    CopySpec("status.blocked", "Blocked", "Blocked"),
    CopySpec("diagnostic.what_happened", "What happened", "Problem"),
    CopySpec("diagnostic.why", "Why", "Cause"),
    CopySpec("diagnostic.where", "Where", "Location"),
    CopySpec("diagnostic.next_actions", "What you can do", "Next actions"),
    CopySpec("diagnostic.technical_details", "Technical details", "Details"),
    CopySpec("progress.operation", "Operation", "Operation"),
    CopySpec("progress.workflow", "Workflow", "Workflow"),
    CopySpec("progress.target", "Target", "Target"),
    CopySpec("progress.phase", "Phase", "Phase"),
    CopySpec("progress.step", "Step", "Step"),
)
COPY_REGISTRY = {item.id: item for item in _SPECS}


def copy_spec(copy_id: str) -> CopySpec:
    try:
        return COPY_REGISTRY[copy_id]
    except KeyError as exc:
        raise KeyError(f"Unknown UI copy key: {copy_id}") from exc


def copy_text(copy_id: str, variant: str = "label", *, surface: str = "") -> str:
    return copy_spec(copy_id).text(variant, surface=surface)


def copy_catalog() -> list[dict[str, Any]]:
    return [asdict(item) for item in COPY_REGISTRY.values()]
