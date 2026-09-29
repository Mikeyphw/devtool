"""PRES-X04 Textual TUI substrate contracts.

This module makes Textual the intended primary TUI substrate while preserving
Devtool's presentation authority boundary: Textual owns rendering and user
interaction only.  RUN/EXO/RER/validation/ENV records remain the source of
truth and are consumed through :mod:`devtool.presentation_surfaces`.

The implementation is deliberately import-safe when Textual is not installed so
bootstrap, CI, and minimal Termux environments can still validate the contract.
The concrete Textual app can be materialized by later PRES overlays once parity
and default-entrypoint migration are ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import util as importlib_util
from typing import Any, Iterable, Mapping, Sequence

from devtool.presentation_surfaces import (
    PresentationBundle,
    PresentationConsumer,
    PresentationProfile,
    RenderedSurface,
    assert_bundle_is_reference_only,
    build_presentation_bundle,
    projection_digest,
)

_TEXTUAL_DISTRIBUTION_NAME = "textual"
_FORBIDDEN_TRUTH_FIELDS = (
    "stdout",
    "stderr",
    "argv",
    "heartbeat_owner",
    "process_attempt",
    "owned_progress",
    "validation_truth",
    "reuse_truth",
)


@dataclass(frozen=True)
class TextualAvailability:
    """Result of probing whether the Textual runtime can be imported."""

    available: bool
    import_name: str = _TEXTUAL_DISTRIBUTION_NAME
    reason: str | None = None

    @property
    def requirement(self) -> str:
        return _TEXTUAL_DISTRIBUTION_NAME

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "import_name": self.import_name,
            "requirement": self.requirement,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TextualBinding:
    """Renderer-owned key binding mapped to a canonical action intent."""

    key: str
    action_id: str
    intent: str
    label: str
    visible: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "action_id": self.action_id,
            "intent": self.intent,
            "label": self.label,
            "visible": self.visible,
        }


@dataclass(frozen=True)
class TextualWidgetSpec:
    """Widget intent consumed by the future concrete Textual app."""

    id: str
    widget: str
    title: str
    source: str
    refs: tuple[Mapping[str, Any], ...] = ()
    actions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "widget": self.widget,
            "title": self.title,
            "source": self.source,
            "refs": [dict(ref) for ref in self.refs],
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class TextualScreenSpec:
    """Renderer-neutral screen model for the Textual TUI."""

    id: str
    title: str
    route: str
    widgets: tuple[TextualWidgetSpec, ...]
    bindings: tuple[TextualBinding, ...]
    profile: str
    width: int
    height: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "route": self.route,
            "profile": self.profile,
            "width": self.width,
            "height": self.height,
            "widgets": [widget.as_dict() for widget in self.widgets],
            "bindings": [binding.as_dict() for binding in self.bindings],
        }


@dataclass(frozen=True)
class TextualAppSpec:
    """Complete reference-only specification for the future Textual app."""

    schema_version: int
    app_id: str
    backend: str
    source_bundle: PresentationBundle
    screens: tuple[TextualScreenSpec, ...]
    default_screen: str
    availability: TextualAvailability
    session_transport: str = "local"
    owns_truth: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "app_id": self.app_id,
            "backend": self.backend,
            "default_screen": self.default_screen,
            "availability": self.availability.as_dict(),
            "session_transport": self.session_transport,
            "owns_truth": self.owns_truth,
            "source_bundle_digest": self.source_bundle.as_machine()["bundle_digest"],
            "source_view_digest": self.source_bundle.source_view.as_machine()["view_digest"],
            "screens": [screen.as_dict() for screen in self.screens],
            "refs": [ref.as_dict() for ref in self.source_bundle.source_view.refs],
            "action_intents": sorted({action.intent for action in self.source_bundle.source_view.actions}),
            "metadata": dict(self.metadata),
        }
        payload["spec_digest"] = projection_digest({k: v for k, v in payload.items() if k != "spec_digest"})
        return payload


@dataclass(frozen=True)
class TextualLaunchPlan:
    """Launch decision for TUI entrypoints."""

    selected_backend: str
    fallback_backend: str | None
    availability: TextualAvailability
    command: tuple[str, ...]
    reason: str
    session_transport: str = "local"

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_backend": self.selected_backend,
            "fallback_backend": self.fallback_backend,
            "availability": self.availability.as_dict(),
            "command": list(self.command),
            "reason": self.reason,
            "session_transport": self.session_transport,
        }


def probe_textual_availability() -> TextualAvailability:
    """Return Textual import availability without importing the framework."""

    if importlib_util.find_spec(_TEXTUAL_DISTRIBUTION_NAME) is None:
        return TextualAvailability(False, reason="textual package is not importable")
    return TextualAvailability(True)


def normalize_session_transport(value: str | None) -> str:
    normalized = (value or "local").strip().lower()
    if normalized in {"ssh", "termux-ssh", "remote-ssh"}:
        return "ssh"
    if normalized in {"local", "termux", "native-termux"}:
        return "local"
    return normalized or "local"


def classify_terminal_profile(*, width: int, height: int, session_transport: str = "local") -> PresentationProfile:
    """Choose the Textual profile for phone/tablet/SSH terminal sizes."""

    width = max(20, int(width))
    height = max(8, int(height))
    transport = normalize_session_transport(session_transport)
    if width < 72 or height < 20:
        return PresentationProfile.COMPACT
    if width >= 132 and height >= 32 and transport != "ssh":
        return PresentationProfile.DETAILED
    return PresentationProfile.STANDARD


def _surface_render(bundle: PresentationBundle, consumer: PresentationConsumer) -> RenderedSurface:
    for render in bundle.renders:
        if render.consumer == consumer:
            return render
    raise ValueError(f"presentation bundle has no render for {consumer.value}")


def _widget_refs(render: RenderedSurface) -> tuple[Mapping[str, Any], ...]:
    return tuple(ref.as_dict() for ref in render.refs)


def default_textual_bindings(bundle: PresentationBundle) -> tuple[TextualBinding, ...]:
    """Map canonical presentation actions to stable Textual key bindings."""

    key_by_action = {
        "open-evidence": "e",
        "copy-summary": "c",
        "watch": "w",
        "next-page": "n",
        "rerun-failed": "r",
        "cancel": "ctrl+c",
        "help": "?",
        "focus-next": "tab",
        "focus-previous": "shift+tab",
        "quit": "q",
    }
    bindings: list[TextualBinding] = [
        TextualBinding("?", "help", "presentation:show_help", "Help"),
        TextualBinding("tab", "focus-next", "presentation:focus_next", "Next pane", visible=False),
        TextualBinding("shift+tab", "focus-previous", "presentation:focus_previous", "Previous pane", visible=False),
        TextualBinding("q", "quit", "presentation:quit", "Quit"),
    ]
    seen = {binding.action_id for binding in bindings}
    for action in bundle.source_view.actions:
        if not action.enabled or action.id in seen:
            continue
        key = key_by_action.get(action.id) or key_by_action.get(action.intent.rsplit(":", 1)[-1])
        if key is None:
            continue
        bindings.append(TextualBinding(key, action.id, action.intent, action.label))
        seen.add(action.id)
    return tuple(bindings)


def build_textual_screens(
    bundle: PresentationBundle,
    *,
    width: int = 100,
    height: int = 28,
    session_transport: str = "local",
) -> tuple[TextualScreenSpec, ...]:
    """Build reference-only screens for the Textual app shell."""

    profile = classify_terminal_profile(width=width, height=height, session_transport=session_transport)
    tui_render = _surface_render(bundle, PresentationConsumer.TUI)
    terminal_render = _surface_render(bundle, PresentationConsumer.TERMINAL)
    machine_render = _surface_render(bundle, PresentationConsumer.MACHINE)
    bindings = default_textual_bindings(bundle)
    refs = _widget_refs(tui_render)
    dashboard_widgets = (
        TextualWidgetSpec("status", "StatusHeader", bundle.source_view.projection.title, "presentation-view", refs=refs),
        TextualWidgetSpec("progress", "ProjectionTimeline", "Progress", "presentation-events", refs=refs),
        TextualWidgetSpec("diagnostics", "DiagnosticSections", "Diagnostics", "presentation-sections", refs=refs),
        TextualWidgetSpec("actions", "ActionBar", "Actions", "presentation-actions", refs=refs, actions=tuple(binding.action_id for binding in bindings)),
    )
    detail_widgets = (
        TextualWidgetSpec("terminal", "PlainTextLog", "Terminal projection", "terminal-render", refs=_widget_refs(terminal_render)),
        TextualWidgetSpec("machine", "JsonInspector", "Machine projection", "machine-render", refs=_widget_refs(machine_render)),
    )
    evidence_widgets = (
        TextualWidgetSpec("refs", "ReferenceTable", "Canonical references", "presentation-refs", refs=refs),
        TextualWidgetSpec("digest", "DigestPanel", "Projection digest", "projection-digest", refs=refs),
    )
    return (
        TextualScreenSpec("dashboard", "Devtool", "/", dashboard_widgets, bindings, profile.value, max(20, width), max(8, height)),
        TextualScreenSpec("details", "Run details", "/details", detail_widgets, bindings, profile.value, max(20, width), max(8, height)),
        TextualScreenSpec("evidence", "Evidence", "/evidence", evidence_widgets, bindings, profile.value, max(20, width), max(8, height)),
    )


def build_textual_app_spec(
    bundle: PresentationBundle,
    *,
    availability: TextualAvailability | None = None,
    width: int = 100,
    height: int = 28,
    session_transport: str = "local",
    metadata: Mapping[str, Any] | None = None,
) -> TextualAppSpec:
    """Create the Textual app contract from a canonical PresentationBundle."""

    assert_bundle_is_reference_only(bundle)
    availability = availability or probe_textual_availability()
    transport = normalize_session_transport(session_transport)
    spec = TextualAppSpec(
        schema_version=1,
        app_id="devtool.textual.tui",
        backend="textual",
        source_bundle=bundle,
        screens=build_textual_screens(bundle, width=width, height=height, session_transport=transport),
        default_screen="dashboard",
        availability=availability,
        session_transport=transport,
        owns_truth=False,
        metadata=dict(metadata or {}),
    )
    assert_textual_spec_is_reference_only(spec)
    return spec


def choose_textual_launch_plan(
    *,
    availability: TextualAvailability | None = None,
    prefer_textual: bool = True,
    require_textual: bool = False,
    fallback_backend: str = "legacy",
    session_transport: str = "local",
) -> TextualLaunchPlan:
    """Decide whether a TUI entrypoint can launch Textual or must fall back."""

    availability = availability or probe_textual_availability()
    transport = normalize_session_transport(session_transport)
    if prefer_textual and availability.available:
        return TextualLaunchPlan(
            selected_backend="textual",
            fallback_backend=None,
            availability=availability,
            command=("python", "-m", "devtool.tui_textual_runtime"),
            reason="textual available and preferred",
            session_transport=transport,
        )
    if require_textual:
        return TextualLaunchPlan(
            selected_backend="unavailable",
            fallback_backend=None,
            availability=availability,
            command=(),
            reason=availability.reason or "textual unavailable",
            session_transport=transport,
        )
    return TextualLaunchPlan(
        selected_backend=fallback_backend,
        fallback_backend=fallback_backend,
        availability=availability,
        command=("devtool", "tui", "--backend", fallback_backend),
        reason="textual unavailable; using configured fallback" if prefer_textual else "textual not preferred",
        session_transport=transport,
    )


def textual_dependency_contract() -> dict[str, Any]:
    """Document how this overlay expects the package dependency to behave."""

    return {
        "primary_backend": "textual",
        "dependency": _TEXTUAL_DISTRIBUTION_NAME,
        "declared_optional_extra": "textual",
        "must_import_lazily": True,
        "validation_requires_installed_textual": False,
        "truth_authority": "RUN/EXO/RER/validation/ENV via presentation_surfaces",
    }


def assert_textual_spec_is_reference_only(spec: TextualAppSpec) -> None:
    encoded = str(spec.as_dict()).lower()
    leaked = [field for field in _FORBIDDEN_TRUTH_FIELDS if field in encoded]
    if leaked:
        raise AssertionError(f"Textual TUI spec contains execution-owned truth: {leaked}")
    if spec.owns_truth is not False:
        raise AssertionError("Textual TUI attempted to own canonical truth")


def build_textual_spec_from_projection(
    records: Sequence[Mapping[str, Any]],
    *,
    consumers: Iterable[PresentationConsumer | str] = (
        PresentationConsumer.MACHINE,
        PresentationConsumer.TERMINAL,
        PresentationConsumer.TUI,
        PresentationConsumer.DESKTOP,
        PresentationConsumer.HUD,
        PresentationConsumer.IPC,
    ),
    width: int = 100,
    height: int = 28,
    session_transport: str = "local",
) -> TextualAppSpec:
    """Convenience helper used by tests and early adapter callers."""

    from devtool.presentation_surfaces import status_from_exo

    projection = status_from_exo(records)
    profile = classify_terminal_profile(width=width, height=height, session_transport=session_transport)
    bundle = build_presentation_bundle(projection, consumers=consumers, width=width, profile=profile)
    return build_textual_app_spec(bundle, width=width, height=height, session_transport=session_transport)
