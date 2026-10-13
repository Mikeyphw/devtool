"""Shared presentation helpers for Devtool's concrete Textual surfaces.

This module is deliberately import-safe: it contains no Textual imports and owns
no execution or lifecycle truth. The standalone control center, normal execution
host, and artifact-apply host use it only to keep responsive layout, labels,
activity projection, and status rendering visually consistent.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

TEXTUAL_COMPACT_WIDTH = 72
TEXTUAL_ULTRA_COMPACT_WIDTH = 52
TEXTUAL_SHORT_HEIGHT = 24
TEXTUAL_TINY_HEIGHT = 16

TEXTUAL_SHARED_CSS = r"""
.ux-kicker {
    height: 1;
    color: $text-muted;
}
.ux-context {
    height: auto;
    min-height: 2;
    padding: 0 1;
    border-bottom: solid $panel;
}
.ux-title {
    text-style: bold;
}
.ux-muted {
    color: $text-muted;
}
.ux-summary {
    height: auto;
    min-height: 2;
    padding: 0 1;
    background: $surface-lighten-1;
}
.ux-card {
    height: auto;
    padding: 0 1;
    border: round $panel;
}
.ux-toolbar {
    height: auto;
    min-height: 2;
    padding: 0 1;
    border-top: solid $panel;
}
.compact .ux-card {
    border-left: none;
    border-right: none;
}
.compact .ux-context,
.compact .ux-summary,
.compact .ux-toolbar {
    padding-left: 0;
    padding-right: 0;
}
.short .ux-kicker {
    display: none;
}
.short .ux-context {
    min-height: 1;
}
.tiny Header,
.tiny Footer {
    display: none;
}
"""

APPLY_STAGES: tuple[str, ...] = (
    "Review",
    "Qualify",
    "Apply",
    "Validate",
    "Commit",
    "Finish",
)


def is_compact_width(width: int) -> bool:
    return int(width) < TEXTUAL_COMPACT_WIDTH


def is_ultra_compact_width(width: int) -> bool:
    return int(width) < TEXTUAL_ULTRA_COMPACT_WIDTH


def is_short_height(height: int) -> bool:
    return int(height) < TEXTUAL_SHORT_HEIGHT


def is_tiny_height(height: int) -> bool:
    return int(height) < TEXTUAL_TINY_HEIGHT


def responsive_class_names(width: int, height: int) -> tuple[str, ...]:
    """Return renderer-only viewport classes for Textual surfaces."""

    names: list[str] = []
    if is_compact_width(width):
        names.append("compact")
    if is_ultra_compact_width(width):
        names.append("ultra-compact")
    if is_short_height(height):
        names.append("short")
    if is_tiny_height(height):
        names.append("tiny")
    return tuple(names)


def fit_middle(value: Any, width: int, *, minimum: int = 12) -> str:
    """Bound one-line UI text while preserving both useful ends.

    Textual will still perform the final clipping/wrapping. This merely keeps
    paths, artifact names, and node ids from consuming several scarce phone rows.
    """

    text = " ".join(str(value or "").replace("\n", " ").split())
    limit = max(int(minimum), int(width))
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    left = max(1, (limit - 1) // 2)
    right = max(1, limit - left - 1)
    return f"{text[:left]}…{text[-right:]}"


def normalized_status(value: Any, *, default: str = "working") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    aliases = {
        "complete": "completed",
        "done": "completed",
        "success": "completed",
        "succeeded": "completed",
        "pass": "completed",
        "passed": "completed",
        "ok": "completed",
        "failure": "failed",
        "error": "failed",
        "cancel": "cancelled",
        "canceled": "cancelled",
    }
    return aliases.get(raw, raw)


def status_label(value: Any, *, default: str = "WORKING") -> str:
    return normalized_status(value, default=default.lower()).replace("_", " ").upper()


def progress_percent(snapshot: Mapping[str, Any]) -> float | None:
    progress = snapshot.get("progress") if isinstance(snapshot.get("progress"), Mapping) else {}
    percent = progress.get("percent", snapshot.get("percent"))
    if isinstance(percent, (int, float)):
        return max(0.0, min(100.0, float(percent)))
    completed = progress.get("completed", progress.get("current", snapshot.get("completed", snapshot.get("current"))))
    total = progress.get("total", snapshot.get("total"))
    if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and float(total) > 0:
        return max(0.0, min(100.0, 100.0 * float(completed) / float(total)))
    return None


def progress_counter(snapshot: Mapping[str, Any]) -> str:
    progress = snapshot.get("progress") if isinstance(snapshot.get("progress"), Mapping) else {}
    completed = progress.get("completed", progress.get("current", snapshot.get("completed", snapshot.get("current"))))
    total = progress.get("total", snapshot.get("total"))
    if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and float(total) > 0:
        if float(completed).is_integer() and float(total).is_integer():
            return f"{int(completed)}/{int(total)}"
        return f"{completed}/{total}"
    percent = progress_percent(snapshot)
    return f"{percent:.0f}%" if percent is not None else ""


def artifact_stage(state: Mapping[str, Any], *, returncode: int | None = None) -> str:
    """Project canonical artifact state into one coarse presentation stage.

    This is intentionally a display-only classification. It never decides whether
    a lifecycle step ran, passed, or may be skipped; canonical LIFE/RUN state keeps
    that authority.
    """

    if returncode is not None:
        return "Finish"
    phase = state.get("phase")
    if isinstance(phase, Mapping):
        phase_text = " ".join(str(phase.get(key) or "") for key in ("name", "label", "detail"))
    else:
        phase_text = str(phase or "")
    progress = state.get("progress") if isinstance(state.get("progress"), Mapping) else {}
    text = " ".join(
        str(value or "")
        for value in (
            phase_text,
            progress.get("detail"),
            state.get("message"),
            state.get("status"),
        )
    ).lower()
    if any(token in text for token in ("rollback", "recover", "restore", "cleanup", "finaliz")):
        return "Finish"
    if any(token in text for token in ("commit", "git")):
        return "Commit"
    if any(token in text for token in ("validat", "test", "lint", "build", "compile", "gradle")):
        return "Validate"
    if any(token in text for token in ("extract", "apply", "transaction", "mutat", "write")):
        return "Apply"
    if any(token in text for token in ("qualif", "preflight", "manifest", "checksum", "inventory", "conflict")):
        return "Qualify"
    return "Review" if not state else "Qualify"


def artifact_stage_line(state: Mapping[str, Any], *, returncode: int | None = None) -> str:
    current = artifact_stage(state, returncode=returncode)
    return "  ".join(f"▶ {name}" if name == current else f"· {name}" for name in APPLY_STAGES)


def artifact_stage_compact_line(state: Mapping[str, Any], *, returncode: int | None = None, tiny: bool = False) -> str:
    current = artifact_stage(state, returncode=returncode)
    index = APPLY_STAGES.index(current) + 1
    if tiny:
        return f"{index}/{len(APPLY_STAGES)} {current}"
    remaining = APPLY_STAGES[index:]
    suffix = f" · next {' → '.join(remaining[:2])}" if remaining else ""
    return f"{current} {index}/{len(APPLY_STAGES)}{suffix}"



class ApplyStageTracker:
    """Phone-friendly apply markers backed by observed lifecycle evidence.

    This tracks *presentation only*. Neither output text nor the model's stage
    classification can grant validation, Git commit, or successful application.
    The canonical child exit code and transaction records remain authoritative.
    """

    def __init__(self) -> None:
        self.outcomes: dict[str, str] = {name: "pending" for name in APPLY_STAGES}
        self.current = "Review"
        self.outcomes["Review"] = "active"

    def begin(self) -> None:
        self._set("Review", "done")
        self._activate("Qualify")

    def _set(self, name: str, value: str) -> None:
        if name not in self.outcomes:
            return
        # A late stale event must not turn a failed or completed stage active.
        if self.outcomes[name] in {"done", "failed", "skipped"} and value == "active":
            return
        self.outcomes[name] = value

    def _activate(self, name: str) -> None:
        if self.outcomes.get(name) in {"done", "failed", "skipped"}:
            return
        if APPLY_STAGES.index(name) < APPLY_STAGES.index(self.current):
            return
        if self.outcomes.get(self.current) == "active" and self.current != name:
            self.outcomes[self.current] = "pending"
        self.current = name
        self.outcomes[name] = "active"

    def observe_line(self, line: str) -> None:
        """Consume narrowly recognized canonical child output, not arbitrary words.

        Log output may be delayed or may omit individual lifecycle phases; we
        only mark a stage done from direct evidence of its actual completion.
        """
        text = _ANSI_RE.sub("", str(line or "")).strip()
        lower = text.lower()
        if lower.startswith("applied overlay from ") or lower.startswith("applied changes from "):
            self._set("Qualify", "done")
            self._set("Apply", "done")
            self._activate("Validate")
        elif lower.startswith("artifact-validation:") and (
            "base contract verified" in lower or "artifact validation passed" in lower
        ):
            self._set("Qualify", "done")
            # This line is the *artifact* preflight, not the validation child.
            if self.outcomes["Apply"] == "pending":
                self._activate("Apply")
        elif (text.startswith("$ ") and
              (".devtool-artifact/validate.sh" in text or "pytest" in text)):
            self._activate("Validate")
        elif lower.startswith("devtool apply · finalizing transaction"):
            # Finalization can occur without Git commit (commit_mode=none).
            # Do not claim Commit was completed merely because a transaction ended.
            if self.outcomes["Commit"] == "pending":
                self._activate("Finish")
        elif lower.startswith("devtool apply · source artifact cleanup passed"):
            self._activate("Finish")
        elif lower.startswith("devtool apply · finalizing"):
            self._activate("Finish")
        elif lower.startswith("$ git commit") or lower.startswith("commit changes"):
            self._activate("Commit")
        elif lower.startswith("$ git add") or lower.startswith("stage changes"):
            self._activate("Commit")
        # A canonical Git commit receipt (including detached-HEAD output) is
        # also evidence that the validator and commit finished successfully.
        elif (text.startswith("[") and re.match(r"^\[[^]]+ [0-9a-f]{7,40}\] ", text)):
            self._set("Validate", "done")
            self._set("Commit", "done")
            self._activate("Finish")

    def observe_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping) or not state:
            return
        phase = state.get("phase")
        if not isinstance(phase, Mapping):
            return
        label = str(phase.get("name") or "").lower()
        command = str(phase.get("command_name") or "").lower()
        if "preflight" in label or "preflight" in command or "qualif" in label:
            stage = "Qualify"
        elif "commit" in label or "git-commit" in command or "git-add" in command:
            stage = "Commit"
        elif any(word in label for word in ("validation", "validate", "test")) or                 "validate" in command or "artifact-post" in command:
            stage = "Validate"
        elif command in {"apply-overlay", "apply-overlays", "apply-snapshot"} or                 "apply overlay" in label:
            stage = "Apply"
        else:
            return
        event = str(state.get("event_type") or "").lower()
        if event == "phase_finished":
            result = state.get("result")
            if isinstance(result, Mapping):
                if bool(result.get("skipped")):
                    self._set(stage, "skipped")
                elif result.get("returncode") is not None:
                    try:
                        ok = int(result["returncode"]) == 0
                    except (TypeError, ValueError):
                        ok = False
                    self._set(stage, "done" if ok else "failed")
                    if not ok:
                        self.current = stage
        elif event == "phase_started":
            self._activate(stage)
        # For progress events the phase name is descriptive but may represent
        # a stale phase, so do not advance without an explicit start event.

    def finish(self, returncode: int) -> None:
        rc = int(returncode)
        if rc == 0:
            # A completed process does not prove an unobserved intermediate
            # stage ran. Remove its spinner, but never turn it green.
            if self.current != "Finish" and self.outcomes.get(self.current) == "active":
                self._set(self.current, "pending")
            self._set("Finish", "done")
        else:
            # Do not replace the actual failing stage with a green Finish.
            if self.current != "Finish" and self.outcomes.get(self.current) == "active":
                self._set(self.current, "failed" if rc != 130 else "skipped")
            self._set("Finish", "failed" if rc != 130 else "skipped")
        self.current = "Finish"

    def visible_current(self) -> str:
        return self.current

def artifact_review_text(review: Mapping[str, Any]) -> str:
    rows = (
        ("Artifact", review.get("artifact") or review.get("path") or "-"),
        ("Target", review.get("target") or "-"),
        ("Changes", review.get("changes") or "-"),
        ("Validation", review.get("validation") or "-"),
        ("Lifecycle", review.get("lifecycle") or "-"),
        ("Apply", review.get("apply") or "-"),
    )
    return "\n".join(f"{label:<10} {value}" for label, value in rows)


def artifact_running_summary(review: Mapping[str, Any], *, width: int = 80) -> str:
    artifact = str(review.get("artifact") or review.get("path") or "Artifact")
    target = str(review.get("target") or "-")
    changes = str(review.get("changes") or "")
    validation = str(review.get("validation") or "")
    text = f"{artifact} → {target}"
    extras = [value for value in (changes, f"validation {validation}" if validation else "") if value]
    if extras:
        text += " · " + " · ".join(extras)
    return fit_middle(text, max(20, int(width)))


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_PYTEST_PROGRESS_RE = re.compile(r"\[\s*(?P<percent>\d{1,3})%\]\s*$")
_PYTEST_SUMMARY_RE = re.compile(
    r"(?P<passed>\d+)\s+passed(?:,\s*(?P<failed>\d+)\s+failed)?"
    r"(?:,\s*(?P<skipped>\d+)\s+skipped)?(?:,\s*(?P<deselected>\d+)\s+deselected)?"
    r"\s+in\s+(?P<seconds>\d+(?:\.\d+)?)s"
)
_HEARTBEAT_RE = re.compile(r"^Devtool heartbeat:\s*(?P<operation>.+?)\s+running after\s+(?P<seconds>\d+(?:\.\d+)?)s", re.I)
_LOW_VALUE_PROGRESS_RE = re.compile(r"^[.sFxXE]+(?:\s*\[\s*\d+%\])?\s*$")
_SEPARATOR_RE = re.compile(r"^[\s=_─━-]{8,}$")


@dataclass(slots=True)
class ArtifactActivityTracker:
    """Renderer-only interpretation of child output for mobile-friendly context.

    The tracker never decides success, failure, phase completion, rollback, or
    validation authority. It only summarizes already-visible child output so a
    Textual surface can avoid pinning raw pytest dots/heartbeats above the log.
    """

    current_operation: str = ""
    test_summary: str = ""
    latest_meaningful: str = ""
    heartbeat: str = ""
    pytest_percent: float | None = None
    raw_line_count: int = 0
    surfaced_line_count: int = 0
    _slow_tests_heading: bool = False

    def observe(self, line: Any) -> bool:
        text = _ANSI_RE.sub("", str(line or "").strip())
        if not text:
            return False
        self.raw_line_count += 1

        heartbeat = _HEARTBEAT_RE.search(text)
        if heartbeat:
            operation = heartbeat.group("operation").strip()
            self.current_operation = operation
            self.heartbeat = f"{operation} · {heartbeat.group('seconds')}s without child output"
            return False

        progress_match = _PYTEST_PROGRESS_RE.search(text)
        if progress_match:
            self.pytest_percent = max(0.0, min(100.0, float(progress_match.group("percent"))))

        summary = _PYTEST_SUMMARY_RE.search(text)
        if summary:
            pieces = [f"{summary.group('passed')} passed"]
            for key, label in (("failed", "failed"), ("skipped", "skipped"), ("deselected", "deselected")):
                value = summary.group(key)
                if value:
                    pieces.append(f"{value} {label}")
            self.test_summary = ", ".join(pieces) + f" · {summary.group('seconds')}s"
            self.latest_meaningful = self.test_summary
            self.pytest_percent = 100.0
            self.surfaced_line_count += 1
            return True

        lowered = text.lower()
        if "devtool slow tests" in lowered:
            self._slow_tests_heading = True
            self.latest_meaningful = "Slow-test report available in raw log"
            return False

        if self._slow_tests_heading and re.match(r"^\d+(?:\.\d+)?s\s+\S", text):
            self._slow_tests_heading = False
            self.latest_meaningful = fit_middle(text, 120)
            self.surfaced_line_count += 1
            return True

        if text.startswith("$"):
            self.current_operation = fit_middle(text[1:].strip(), 120)
            self.latest_meaningful = self.current_operation
            self.pytest_percent = None
            self.surfaced_line_count += 1
            return True

        if any(token in lowered for token in (
            "artifact validation passed", "artifact validation failed", "applied overlay", "transaction backup",
            "git commit", "failure reason", "error:", "warning:", "traceback", "failed", "passed",
        )):
            self.latest_meaningful = fit_middle(text, 140)
            self.surfaced_line_count += 1
            return True

        if _LOW_VALUE_PROGRESS_RE.match(text) or _SEPARATOR_RE.match(text):
            return False
        if progress_match and len(text) < 120:
            # A pytest dot/progress row is useful for semantic progress, but not
            # useful enough to dominate the default activity log.
            return False

        self.latest_meaningful = fit_middle(text, 140)
        self.surfaced_line_count += 1
        return True

    def activity_text(self, *, elapsed_seconds: float | None = None, max_lines: int = 2, width: int = 96) -> str:
        lines: list[str] = []
        operation = self.current_operation.strip()
        if operation:
            prefix = "Running"
            if elapsed_seconds is not None and elapsed_seconds >= 0:
                prefix += f" · {elapsed_seconds:.0f}s"
            lines.append(f"{prefix}: {fit_middle(operation, max(24, width - len(prefix) - 3))}")
        if self.test_summary:
            lines.append(self.test_summary)
        if self.latest_meaningful and self.latest_meaningful not in lines and self.latest_meaningful != operation:
            lines.append(self.latest_meaningful)
        if not lines and self.heartbeat:
            lines.append(self.heartbeat)
        return "\n".join(lines[: max(1, int(max_lines))])


def artifact_display_progress(
    state: Mapping[str, Any],
    activity: ArtifactActivityTracker | None = None,
    *,
    returncode: int | None = None,
) -> tuple[float | None, str, str]:
    """Choose truthful display progress without inventing an overall denominator.

    Canonical percentage/counters win. While canonical artifact progress is
    unknown (or still 0 during a validator subprocess), parsed pytest percentage
    may be shown explicitly as *validation child* progress, never as artifact
    completion. Unknown progress remains indeterminate instead of a fake 0%.
    """

    if returncode == 0:
        return 100.0, "100%", "overall"
    canonical = progress_percent(state)
    counter = progress_counter(state)
    stage = artifact_stage(state, returncode=returncode)
    child_percent = activity.pytest_percent if activity is not None else None
    if canonical is not None and (canonical > 0.0 or stage != "Validate"):
        return canonical, counter or f"{canonical:.0f}%", "overall"
    if stage == "Validate" and child_percent is not None:
        return child_percent, f"pytest {child_percent:.0f}%", "validation child"
    # A canonical 0% while a validator child is active commonly means the parent
    # has no child denominator yet. Do not turn that absence into a fake progress
    # claim; render the stage as indeterminate until either source has evidence.
    if stage == "Validate" and canonical == 0.0:
        return None, "working", "indeterminate"
    return None, counter or "working", "indeterminate"


__all__ = [
    "APPLY_STAGES",
    "ApplyStageTracker",
    "ArtifactActivityTracker",
    "TEXTUAL_COMPACT_WIDTH",
    "TEXTUAL_SHARED_CSS",
    "TEXTUAL_SHORT_HEIGHT",
    "TEXTUAL_TINY_HEIGHT",
    "TEXTUAL_ULTRA_COMPACT_WIDTH",
    "artifact_display_progress",
    "artifact_review_text",
    "artifact_running_summary",
    "artifact_stage",
    "artifact_stage_compact_line",
    "artifact_stage_line",
    "fit_middle",
    "is_compact_width",
    "is_short_height",
    "is_tiny_height",
    "is_ultra_compact_width",
    "normalized_status",
    "progress_counter",
    "progress_percent",
    "responsive_class_names",
    "status_label",
]
