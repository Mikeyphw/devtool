from __future__ import annotations

from dataclasses import dataclass
from html import escape
from importlib.resources import files
import json
import re
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class VisualScenario:
    id: str
    surface: str
    title: str
    subtitle: str
    status: str
    progress: int
    errors: int
    warnings: int
    lines: tuple[str, ...]
    actions: tuple[str, ...]


def load_contract() -> dict[str, Any]:
    resource = files("devtool").joinpath("resources/ui/ui-regression-contract.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def visual_scenarios(contract: Mapping[str, Any] | None = None) -> tuple[VisualScenario, ...]:
    source = contract or load_contract()
    scenarios: list[VisualScenario] = []
    for raw in source.get("visual_scenarios", []):
        scenarios.append(
            VisualScenario(
                id=str(raw["id"]),
                surface=str(raw["surface"]),
                title=str(raw["title"]),
                subtitle=str(raw.get("subtitle", "")),
                status=str(raw.get("status", "waiting")),
                progress=max(0, min(100, int(raw.get("progress", 0)))),
                errors=max(0, int(raw.get("errors", 0))),
                warnings=max(0, int(raw.get("warnings", 0))),
                lines=tuple(str(value) for value in raw.get("lines", [])),
                actions=tuple(str(value) for value in raw.get("actions", [])),
            )
        )
    return tuple(scenarios)


def canonical_label(group: str, key: str, *, contract: Mapping[str, Any] | None = None) -> str:
    source = contract or load_contract()
    values = source.get("terminology", {}).get(group, {})
    return str(values.get(key, key.replace("_", " ").replace("-", " ").title()))


def normalize_terminal_snapshot(value: str) -> str:
    value = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def render_visual_svg(scenario: VisualScenario, *, width: int = 720, height: int = 420) -> str:
    """Render a deterministic golden image for a cross-platform UI state.

    The SVG is intentionally font-independent: tests compare the source image,
    while CI artifact viewers can open it directly for a visual diff.
    """

    status_colors = {
        "waiting": "#6b7280",
        "running": "#3b82f6",
        "ok": "#10b981",
        "failed": "#ef4444",
        "cancelled": "#f59e0b",
        "rolling_back": "#a855f7",
        "rolled_back": "#10b981",
    }
    accent = status_colors.get(scenario.status, "#6b7280")
    card_x, card_y, card_w, card_h = 34, 28, width - 68, height - 56
    progress_w = card_w - 48
    progress_fill = int(progress_w * scenario.progress / 100)
    line_y = 190
    action_y = height - 76

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0b1020"/>',
        f'<rect x="{card_x}" y="{card_y}" width="{card_w}" height="{card_h}" rx="22" fill="#151b2e" stroke="#2a3350"/>',
        f'<rect x="{card_x}" y="{card_y}" width="8" height="{card_h}" rx="4" fill="{accent}"/>',
        f'<text x="{card_x + 28}" y="70" fill="#f8fafc" font-family="sans-serif" font-size="25" font-weight="700">{escape(scenario.title)}</text>',
        f'<text x="{card_x + 28}" y="98" fill="#aab4ca" font-family="sans-serif" font-size="15">{escape(scenario.subtitle)}</text>',
        f'<rect x="{card_x + 28}" y="119" width="{progress_w}" height="12" rx="6" fill="#29324a"/>',
        f'<rect x="{card_x + 28}" y="119" width="{progress_fill}" height="12" rx="6" fill="{accent}"/>',
        f'<text x="{card_x + 28}" y="157" fill="#dbe4f5" font-family="monospace" font-size="14">{scenario.progress}% · {escape(scenario.status.replace("_", " ").title())}</text>',
        f'<text x="{width - 215}" y="157" fill="#fca5a5" font-family="monospace" font-size="14">{scenario.errors} errors</text>',
        f'<text x="{width - 120}" y="157" fill="#fde68a" font-family="monospace" font-size="14">{scenario.warnings} warnings</text>',
    ]
    for line in scenario.lines[:5]:
        parts.append(
            f'<text x="{card_x + 34}" y="{line_y}" fill="#d5dcef" font-family="sans-serif" font-size="16">• {escape(line)}</text>'
        )
        line_y += 31

    action_x = card_x + 28
    for action in scenario.actions[:4]:
        action_w = max(92, min(160, 28 + len(action) * 8))
        parts.extend(
            [
                f'<rect x="{action_x}" y="{action_y}" width="{action_w}" height="38" rx="11" fill="#202a44" stroke="#3c496d"/>',
                f'<text x="{action_x + action_w / 2:.1f}" y="{action_y + 25}" text-anchor="middle" fill="#eef2ff" font-family="sans-serif" font-size="14">{escape(action)}</text>',
            ]
        )
        action_x += action_w + 12
    parts.append(f'<text x="{width - 48}" y="{height - 24}" text-anchor="end" fill="#68738d" font-family="monospace" font-size="11">{escape(scenario.id)}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def filter_history_records(
    records: Sequence[Mapping[str, Any]],
    query: str = "",
    status: str = "all",
    repository: str = "",
) -> list[Mapping[str, Any]]:
    """Reference implementation used by large-history regression tests."""

    needle = query.strip().casefold()
    wanted = status.strip().casefold()
    repository = repository.strip()
    result: list[Mapping[str, Any]] = []
    fields = ("title", "repo_name", "repo_root", "target", "summary", "command", "artifact_name", "git_commit", "status")
    for record in records:
        item_status = str(record.get("status", "")).casefold()
        if wanted and wanted != "all" and item_status != wanted:
            continue
        if repository and str(record.get("repo_root", "")) != repository:
            continue
        if needle:
            cached = record.get("_search_text")
            haystack = str(cached) if cached is not None else "\n".join(str(record.get(field, "")) for field in fields).casefold()
            if needle not in haystack:
                continue
        result.append(record)
    return result


def with_history_search_index(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    fields = ("title", "repo_name", "repo_root", "target", "summary", "command", "artifact_name", "git_commit", "status")
    indexed: list[dict[str, Any]] = []
    for value in records:
        record = dict(value)
        record["_search_text"] = "\n".join(str(record.get(field, "")) for field in fields).casefold()
        indexed.append(record)
    return indexed
