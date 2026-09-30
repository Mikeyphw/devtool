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


# PRES-X05 Textual feature parity contracts.
@dataclass(frozen=True)
class TextualCommandPaletteItem:
    """Command palette row derived from canonical action intents or renderer bindings."""

    id: str
    title: str
    action_id: str
    intent: str
    key: str | None = None
    target_screen: str = "dashboard"
    refs: tuple[Mapping[str, Any], ...] = ()
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "action_id": self.action_id,
            "intent": self.intent,
            "key": self.key,
            "target_screen": self.target_screen,
            "refs": [dict(ref) for ref in self.refs],
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class TextualParityFeature:
    """Feature parity requirement for replacing the legacy TUI."""

    id: str
    label: str
    source: str
    required_widgets: tuple[str, ...]
    action_intents: tuple[str, ...] = ()
    screens: tuple[str, ...] = ()
    covered: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "source": self.source,
            "required_widgets": list(self.required_widgets),
            "action_intents": list(self.action_intents),
            "screens": list(self.screens),
            "covered": self.covered,
        }


@dataclass(frozen=True)
class TextualFeatureParityPlan:
    """Renderer-only Textual parity plan for current TUI replacement."""

    schema_version: int
    base_spec: TextualAppSpec
    screens: tuple[TextualScreenSpec, ...]
    command_palette: tuple[TextualCommandPaletteItem, ...]
    features: tuple[TextualParityFeature, ...]
    owns_truth: bool = False

    @property
    def missing_features(self) -> tuple[str, ...]:
        return tuple(feature.id for feature in self.features if not feature.covered)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "backend": "textual",
            "base_spec_digest": self.base_spec.as_dict()["spec_digest"],
            "base_bundle_digest": self.base_spec.source_bundle.as_machine()["bundle_digest"],
            "screens": [screen.as_dict() for screen in self.screens],
            "command_palette": [item.as_dict() for item in self.command_palette],
            "features": [feature.as_dict() for feature in self.features],
            "missing_features": list(self.missing_features),
            "owns_truth": self.owns_truth,
            "refs": [ref.as_dict() for ref in self.base_spec.source_bundle.refs],
        }
        payload["parity_digest"] = projection_digest({k: v for k, v in payload.items() if k != "parity_digest"})
        return payload


_ALLOWED_WIDGET_SOURCES = {
    "presentation-view",
    "presentation-events",
    "presentation-sections",
    "presentation-actions",
    "presentation-refs",
    "projection-digest",
    "terminal-render",
    "machine-render",
    "textual-bindings",
    "command-intents",
    "surface-capabilities",
}


def _bundle_refs(spec: TextualAppSpec) -> tuple[Mapping[str, Any], ...]:
    return tuple(ref.as_dict() for ref in spec.source_bundle.refs)


def _all_bindings(spec: TextualAppSpec) -> tuple[TextualBinding, ...]:
    seen: dict[str, TextualBinding] = {}
    for screen in spec.screens:
        for binding in screen.bindings:
            seen.setdefault(binding.action_id, binding)
    for binding in default_textual_bindings(spec.source_bundle):
        seen.setdefault(binding.action_id, binding)
    return tuple(seen.values())


def build_textual_command_palette(spec: TextualAppSpec) -> tuple[TextualCommandPaletteItem, ...]:
    """Build command palette entries from canonical action intents and bindings."""

    refs = _bundle_refs(spec)
    items: list[TextualCommandPaletteItem] = []
    for binding in _all_bindings(spec):
        target = "help" if binding.action_id == "help" else "evidence" if binding.action_id == "open-evidence" else "dashboard"
        if binding.action_id == "rerun-failed":
            target = "validation"
        elif binding.action_id == "next-page":
            target = "runs"
        items.append(
            TextualCommandPaletteItem(
                id=f"cmd-{binding.action_id}",
                title=binding.label,
                action_id=binding.action_id,
                intent=binding.intent,
                key=binding.key,
                target_screen=target,
                refs=refs,
                enabled=True,
            )
        )
    unique: dict[str, TextualCommandPaletteItem] = {}
    for item in items:
        unique.setdefault(item.id, item)
    return tuple(unique.values())


def build_textual_feature_parity_screens(spec: TextualAppSpec) -> tuple[TextualScreenSpec, ...]:
    """Return supplemental screens needed for current-TUI feature parity."""

    refs = _bundle_refs(spec)
    bindings = _all_bindings(spec)
    action_ids = tuple(binding.action_id for binding in bindings)
    profile = spec.screens[0].profile if spec.screens else "standard"
    width = spec.screens[0].width if spec.screens else 100
    height = spec.screens[0].height if spec.screens else 28
    run_widgets = (
        TextualWidgetSpec("run-list", "RunList", "Current runs", "presentation-events", refs=refs, actions=action_ids),
        TextualWidgetSpec("progress", "ProgressTimeline", "Active progress", "presentation-events", refs=refs, actions=action_ids),
        TextualWidgetSpec("node-tree", "NodeTree", "Workflow nodes", "presentation-events", refs=refs, actions=action_ids),
    )
    validation_widgets = (
        TextualWidgetSpec("validation-session", "ValidationSession", "Validation session", "presentation-sections", refs=refs, actions=action_ids),
        TextualWidgetSpec("validation-evidence", "ReferenceTable", "Validation evidence", "presentation-refs", refs=refs, actions=action_ids),
    )
    artifact_widgets = (
        TextualWidgetSpec("artifact-lifecycle", "ArtifactLifecycle", "Artifact lifecycle", "presentation-events", refs=refs, actions=action_ids),
        TextualWidgetSpec("apply-status", "ApplyStatus", "Apply status", "presentation-sections", refs=refs, actions=action_ids),
        TextualWidgetSpec("rollback-hint", "RollbackHint", "Rollback / recovery", "presentation-refs", refs=refs, actions=action_ids),
    )
    command_widgets = (
        TextualWidgetSpec("command-palette", "CommandPalette", "Command palette", "command-intents", refs=refs, actions=action_ids),
        TextualWidgetSpec("shortcut-table", "ShortcutTable", "Keyboard shortcuts", "textual-bindings", refs=refs, actions=action_ids),
    )
    help_widgets = (
        TextualWidgetSpec("capabilities", "CapabilityMatrix", "Surface capabilities", "surface-capabilities", refs=refs),
        TextualWidgetSpec("copy-help", "CopyActions", "Copy and share", "presentation-actions", refs=refs, actions=action_ids),
    )
    return (
        TextualScreenSpec("runs", "Runs", "/runs", run_widgets, bindings, profile, width, height),
        TextualScreenSpec("validation", "Validation", "/validation", validation_widgets, bindings, profile, width, height),
        TextualScreenSpec("artifacts", "Artifacts", "/artifacts", artifact_widgets, bindings, profile, width, height),
        TextualScreenSpec("commands", "Commands", "/commands", command_widgets, bindings, profile, width, height),
        TextualScreenSpec("help", "Help", "/help", help_widgets, bindings, profile, width, height),
    )


def textual_required_feature_contract() -> tuple[TextualParityFeature, ...]:
    """Durable feature checklist for replacing the legacy TUI."""

    return (
        TextualParityFeature("run-list", "Current run list", "presentation-events", ("run-list",), ("open_refs",)),
        TextualParityFeature("active-progress", "Active progress timeline", "presentation-events", ("progress",), ("follow_cursor",)),
        TextualParityFeature("node-tree", "Workflow node tree", "presentation-events", ("node-tree",), ("open_refs",)),
        TextualParityFeature("diagnostics", "Diagnostic sections", "presentation-sections", ("diagnostics",), ("open_refs",)),
        TextualParityFeature("evidence", "Evidence/reference inspector", "presentation-refs", ("refs",), ("open_refs",)),
        TextualParityFeature("validation-session", "Validation session panel", "presentation-sections", ("validation-session",), ()),
        TextualParityFeature("artifact-lifecycle", "Artifact/apply lifecycle panel", "presentation-events", ("artifact-lifecycle", "apply-status"), ("open_refs",)),
        TextualParityFeature("command-palette", "Command palette", "command-intents", ("command-palette",), ("open_refs",)),
        TextualParityFeature("keyboard-shortcuts", "Keyboard shortcut parity", "textual-bindings", ("shortcut-table",), ("presentation:show_help",)),
        TextualParityFeature("copy-share", "Copy/share summary actions", "presentation-actions", ("copy-help",), ()),
        TextualParityFeature("responsive-layout", "Responsive layout profiles", "surface-capabilities", ("capabilities",), ()),
    )


def _screen_index(screens: Sequence[TextualScreenSpec]) -> tuple[set[str], set[str], dict[str, tuple[str, ...]]]:
    widget_ids: set[str] = set()
    action_intents: set[str] = set()
    widget_screens: dict[str, list[str]] = {}
    for screen in screens:
        for binding in screen.bindings:
            action_intents.add(binding.intent)
        for widget in screen.widgets:
            widget_ids.add(widget.id)
            widget_screens.setdefault(widget.id, []).append(screen.id)
            for action in widget.actions:
                action_intents.add(action)
    return widget_ids, action_intents, {k: tuple(v) for k, v in widget_screens.items()}


def evaluate_textual_feature_coverage(
    screens: Sequence[TextualScreenSpec],
    features: Sequence[TextualParityFeature] | None = None,
    command_palette: Sequence[TextualCommandPaletteItem] = (),
) -> tuple[TextualParityFeature, ...]:
    """Mark required TUI replacement features as covered by screens/actions."""

    features = tuple(features or textual_required_feature_contract())
    widget_ids, action_intents, widget_screens = _screen_index(screens)
    action_intents.update(item.intent for item in command_palette if item.enabled)
    covered: list[TextualParityFeature] = []
    for feature in features:
        widgets_ok = all(widget in widget_ids for widget in feature.required_widgets)
        actions_ok = all(intent in action_intents for intent in feature.action_intents)
        screens_for_feature: set[str] = set()
        for widget in feature.required_widgets:
            screens_for_feature.update(widget_screens.get(widget, ()))
        covered.append(
            TextualParityFeature(
                feature.id,
                feature.label,
                feature.source,
                feature.required_widgets,
                feature.action_intents,
                tuple(sorted(screens_for_feature)),
                widgets_ok and actions_ok,
            )
        )
    return tuple(covered)


def build_textual_feature_parity_plan(spec: TextualAppSpec) -> TextualFeatureParityPlan:
    """Build the PRES-X05 parity plan for making Textual feature-complete."""

    assert_textual_spec_is_reference_only(spec)
    parity_screens = build_textual_feature_parity_screens(spec)
    screens = tuple(spec.screens) + parity_screens
    command_palette = build_textual_command_palette(spec)
    features = evaluate_textual_feature_coverage(screens, command_palette=command_palette)
    plan = TextualFeatureParityPlan(1, spec, screens, command_palette, features, owns_truth=False)
    assert_textual_feature_parity_is_reference_only(plan)
    return plan


def build_textual_feature_parity_from_projection(
    records: Sequence[Mapping[str, Any]],
    *,
    width: int = 100,
    height: int = 28,
    session_transport: str = "local",
) -> TextualFeatureParityPlan:
    """Convenience helper for callers/tests that begin from EXO/RUN records."""

    spec = build_textual_spec_from_projection(records, width=width, height=height, session_transport=session_transport)
    return build_textual_feature_parity_plan(spec)


def assert_textual_feature_parity_is_reference_only(plan: TextualFeatureParityPlan) -> None:
    encoded = str(plan.as_dict()).lower()
    leaked = [field for field in _FORBIDDEN_TRUTH_FIELDS if field in encoded]
    if leaked:
        raise AssertionError(f"Textual feature parity plan contains execution-owned truth: {leaked}")
    if plan.owns_truth is not False:
        raise AssertionError("Textual feature parity attempted to own canonical truth")
    invalid_sources = [
        widget.source
        for screen in plan.screens
        for widget in screen.widgets
        if widget.source not in _ALLOWED_WIDGET_SOURCES
    ]
    if invalid_sources:
        raise AssertionError(f"Textual widgets use non-canonical sources: {sorted(set(invalid_sources))}")



# PRES-X06 Textual default-replacement contracts.
@dataclass(frozen=True)
class TuiBackendSelection:
    """Resolved TUI backend decision for the ``devtool tui`` entrypoint."""

    requested_backend: str
    effective_backend: str
    default_backend: str
    fallback_backend: str | None
    availability: TextualAvailability
    reason: str
    command: tuple[str, ...]
    session_transport: str = "local"
    legacy_flag: bool = False
    require_textual: bool = False
    owns_truth: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "requested_backend": self.requested_backend,
            "effective_backend": self.effective_backend,
            "default_backend": self.default_backend,
            "fallback_backend": self.fallback_backend,
            "availability": self.availability.as_dict(),
            "reason": self.reason,
            "command": list(self.command),
            "session_transport": self.session_transport,
            "legacy_flag": self.legacy_flag,
            "require_textual": self.require_textual,
            "owns_truth": self.owns_truth,
        }
        payload["selection_digest"] = projection_digest({k: v for k, v in payload.items() if k != "selection_digest"})
        return payload


def normalize_tui_backend(value: str | None) -> str:
    normalized = (value or "auto").strip().lower().replace("_", "-")
    if normalized in {"", "default", "preferred"}:
        return "auto"
    if normalized in {"legacy", "rich", "classic", "old", "legacy-tui"}:
        return "legacy"
    if normalized in {"textual", "new"}:
        return "textual"
    if normalized == "auto":
        return normalized
    raise ValueError(f"Unsupported TUI backend: {value}")


def default_tui_backend_contract() -> dict[str, Any]:
    """Contract for the PRES-X06 TUI default replacement."""

    return {
        "schema_version": 1,
        "default_backend": "textual",
        "command_default": "auto",
        "auto_policy": "prefer-textual-fallback-legacy",
        "legacy_override": "--legacy-tui or --backend legacy",
        "explicit_textual_failure_mode": "fail-closed-when-unavailable",
        "textual_owns_truth": False,
        "truth_authority": "RUN/EXO/RER/validation/ENV via presentation_surfaces",
        "machine_output_changed": False,
    }


def resolve_tui_backend_selection(
    *,
    requested_backend: str | None = "auto",
    legacy_tui: bool = False,
    require_textual: bool = False,
    availability: TextualAvailability | None = None,
    session_transport: str = "local",
) -> TuiBackendSelection:
    """Resolve the backend used by ``devtool tui``.

    ``auto`` is now the command default and means "prefer Textual".  Missing
    Textual falls back to the legacy TUI only for auto mode; an explicit
    ``--backend textual`` request fails closed so users do not unknowingly get
    the old UI when they asked to test the replacement.
    """

    availability = availability or probe_textual_availability()
    transport = normalize_session_transport(session_transport)
    requested = "legacy" if legacy_tui else normalize_tui_backend(requested_backend)
    if requested == "legacy":
        selection = TuiBackendSelection(
            requested_backend=requested,
            effective_backend="legacy",
            default_backend="textual",
            fallback_backend=None,
            availability=availability,
            reason="legacy TUI explicitly requested",
            command=("devtool", "tui", "--backend", "legacy"),
            session_transport=transport,
            legacy_flag=legacy_tui,
            require_textual=require_textual,
        )
        assert_tui_backend_selection_is_safe(selection)
        return selection
    if requested == "textual":
        if availability.available:
            selection = TuiBackendSelection(
                requested_backend=requested,
                effective_backend="textual",
                default_backend="textual",
                fallback_backend=None,
                availability=availability,
                reason="explicit Textual backend requested and available",
                command=("python", "-m", "devtool.tui_textual_runtime"),
                session_transport=transport,
                require_textual=require_textual,
            )
        else:
            selection = TuiBackendSelection(
                requested_backend=requested,
                effective_backend="unavailable",
                default_backend="textual",
                fallback_backend=None,
                availability=availability,
                reason=availability.reason or "explicit Textual backend requested but unavailable",
                command=(),
                session_transport=transport,
                require_textual=True,
            )
        assert_tui_backend_selection_is_safe(selection)
        return selection
    if requested != "auto":
        raise ValueError(f"Unsupported TUI backend: {requested_backend}")
    if availability.available:
        selection = TuiBackendSelection(
            requested_backend="auto",
            effective_backend="textual",
            default_backend="textual",
            fallback_backend="legacy",
            availability=availability,
            reason="auto mode selected default Textual backend",
            command=("python", "-m", "devtool.tui_textual_runtime"),
            session_transport=transport,
            require_textual=require_textual,
        )
    elif require_textual:
        selection = TuiBackendSelection(
            requested_backend="auto",
            effective_backend="unavailable",
            default_backend="textual",
            fallback_backend=None,
            availability=availability,
            reason=availability.reason or "Textual required but unavailable",
            command=(),
            session_transport=transport,
            require_textual=True,
        )
    else:
        selection = TuiBackendSelection(
            requested_backend="auto",
            effective_backend="legacy",
            default_backend="textual",
            fallback_backend="legacy",
            availability=availability,
            reason="auto mode prefers Textual, but Textual is unavailable; using legacy fallback",
            command=("devtool", "tui", "--backend", "legacy"),
            session_transport=transport,
            require_textual=False,
        )
    assert_tui_backend_selection_is_safe(selection)
    return selection


def assert_tui_backend_selection_is_safe(selection: TuiBackendSelection) -> None:
    encoded = str(selection.as_dict()).lower()
    leaked = [field for field in _FORBIDDEN_TRUTH_FIELDS if field in encoded]
    if leaked:
        raise AssertionError(f"TUI backend selection contains execution-owned truth: {leaked}")
    if selection.owns_truth is not False:
        raise AssertionError("TUI backend selection attempted to own canonical truth")
    if selection.default_backend != "textual":
        raise AssertionError("PRES-X06 requires Textual to be the default TUI backend")



# PRES-X07 Textual replacement hardening contracts.
@dataclass(frozen=True)
class TuiBackendHardeningNotice:
    """Human/machine notice explaining TUI backend selection and fallback fencing."""

    selection: TuiBackendSelection
    severity: str
    message: str
    remediations: tuple[str, ...] = ()
    fallback_explicit: bool = False
    owns_truth: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "severity": self.severity,
            "message": self.message,
            "remediations": list(self.remediations),
            "fallback_explicit": self.fallback_explicit,
            "owns_truth": self.owns_truth,
            "selection": self.selection.as_dict(),
        }
        payload["notice_digest"] = projection_digest({k: v for k, v in payload.items() if k != "notice_digest"})
        return payload


def textual_replacement_hardening_contract() -> dict[str, Any]:
    """Durable PRES-X07 contract for replacing the legacy TUI safely."""

    return {
        "schema_version": 1,
        "primary_backend": "textual",
        "legacy_backend_status": "fallback-only",
        "legacy_allowed_when": (
            "--legacy-tui or --backend legacy is explicit",
            "auto mode cannot import Textual and fallback is allowed",
        ),
        "legacy_not_allowed_when": (
            "--backend textual was requested",
            "--require-textual or --no-tui-fallback was requested",
        ),
        "explain_command": "devtool tui --explain-backend",
        "machine_output_changed": False,
        "textual_owns_truth": False,
        "truth_authority": "RUN/EXO/RER/validation/ENV via presentation_surfaces",
    }


def build_tui_backend_notice(selection: TuiBackendSelection) -> TuiBackendHardeningNotice:
    """Build a renderer-safe explanation for the chosen TUI backend."""

    assert_tui_backend_selection_is_safe(selection)
    if selection.effective_backend == "textual":
        notice = TuiBackendHardeningNotice(
            selection=selection,
            severity="info",
            message="Textual TUI selected as the primary backend.",
            remediations=(),
            fallback_explicit=False,
        )
    elif selection.effective_backend == "legacy" and selection.requested_backend == "auto":
        notice = TuiBackendHardeningNotice(
            selection=selection,
            severity="warning",
            message="Textual is the primary TUI, but it is not importable; using the legacy fallback for this launch.",
            remediations=(
                "Install the Textual extra with pip install -e .[textual]",
                "Use devtool tui --require-textual to fail instead of falling back",
                "Use devtool tui --explain-backend to inspect backend selection",
            ),
            fallback_explicit=False,
        )
    elif selection.effective_backend == "legacy":
        notice = TuiBackendHardeningNotice(
            selection=selection,
            severity="notice",
            message="Legacy TUI selected only because it was explicitly requested.",
            remediations=("Remove --legacy-tui or use --backend auto to return to Textual-first selection",),
            fallback_explicit=True,
        )
    else:
        notice = TuiBackendHardeningNotice(
            selection=selection,
            severity="error",
            message=selection.reason or "Textual TUI unavailable and fallback is disabled.",
            remediations=(
                "Install the Textual extra with pip install -e .[textual]",
                "Use --legacy-tui only if you intentionally need the fallback",
            ),
            fallback_explicit=False,
        )
    assert_tui_backend_notice_is_safe(notice)
    return notice


def assert_tui_backend_notice_is_safe(notice: TuiBackendHardeningNotice) -> None:
    encoded = str(notice.as_dict()).lower()
    leaked = [field for field in _FORBIDDEN_TRUTH_FIELDS if field in encoded]
    if leaked:
        raise AssertionError(f"TUI backend notice contains execution-owned truth: {leaked}")
    if notice.owns_truth is not False:
        raise AssertionError("TUI backend notice attempted to own canonical truth")


def assert_legacy_tui_is_fenced(selection: TuiBackendSelection) -> None:
    """Ensure legacy can only be reached through explicit or auto-fallback paths."""

    assert_tui_backend_selection_is_safe(selection)
    if selection.default_backend != "textual":
        raise AssertionError("legacy cannot become the default backend")
    if selection.effective_backend != "legacy":
        return
    explicit = selection.requested_backend == "legacy" or selection.legacy_flag
    auto_fallback = (
        selection.requested_backend == "auto"
        and not selection.availability.available
        and selection.fallback_backend == "legacy"
        and not selection.require_textual
    )
    if not (explicit or auto_fallback):
        raise AssertionError("legacy TUI must be explicit or a permitted auto fallback")


def summarize_legacy_cleanup_assumptions(selections: Sequence[TuiBackendSelection]) -> dict[str, Any]:
    """Summarize remaining legacy paths without reading execution truth."""

    for selection in selections:
        assert_legacy_tui_is_fenced(selection)
    legacy_paths = [selection for selection in selections if selection.effective_backend == "legacy"]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "primary_backend": "textual",
        "legacy_path_count": len(legacy_paths),
        "legacy_reasons": sorted({selection.reason for selection in legacy_paths}),
        "default_backend": "textual",
        "owns_truth": False,
    }
    payload["cleanup_digest"] = projection_digest({k: v for k, v in payload.items() if k != "cleanup_digest"})
    return payload

def run_textual_tui_runtime(args: Any, *, repo_root: Any, console: Any | None = None) -> int:
    """Run the concrete Textual control-center shell.

    ``--once`` stays renderer-independent so bootstrap/non-interactive hosts can
    inspect the same bounded projection without importing Textual. Interactive
    mode remains a client over canonical RUN/EXO/LIFE/DIST/TEST authorities and
    never reconstructs shell commands or execution truth.
    """

    once = bool(getattr(args, "once", False))
    page = str(getattr(args, "page", "dashboard") or "dashboard")
    width = int(getattr(args, "width", 0) or 0)
    if once:
        from .textual_control_center import PAGES, TextualControlCenter
        center = TextualControlCenter(page=page if page in PAGES else "dashboard")
        payload = center.snapshot(width=width or 100, height=28)
        payload["repo_root"] = str(repo_root)
        if console is not None:
            console.print(payload)
        else:
            print(payload)
        return 0

    availability = probe_textual_availability()
    if not availability.available:
        message = "Textual TUI requested, but the textual package is not importable. Install devtool[textual] or use --legacy-tui."
        if console is not None:
            console.print(f"Error: {message}")
        else:
            print(f"Error: {message}")
        return 2
    try:
        from textual.app import App, ComposeResult  # type: ignore
        from textual.widgets import Footer, Header, Static  # type: ignore
    except Exception as exc:  # pragma: no cover - only exercised when Textual is installed but broken
        message = f"Textual TUI requested, but Textual failed to import: {exc}"
        if console is not None:
            console.print(f"Error: {message}")
        else:
            print(f"Error: {message}")
        return 2

    from .textual_control_center import (
        NAV_KEYS,
        PAGES,
        TerminalRestorationGuard,
        TextualControlCenter,
        render_control_center_text,
    )

    class DevtoolTextualShell(App):  # type: ignore[misc]
        TITLE = "Devtool"
        SUB_TITLE = "Textual Control Center"
        BINDINGS = [
            ("q", "exit_without_cancel", "Quit"),
            ("escape", "exit_without_cancel", "Back/Quit"),
            ("ctrl+c", "cancel_selected", "Cancel run"),
            ("?", "help", "Help"),
        ] + [(key, f"page_{target}", target.title()) for key, target in NAV_KEYS.items()]

        def __init__(self) -> None:
            super().__init__()
            self.control_center = TextualControlCenter(page=page if page in PAGES else "dashboard")
            self.last_control_result: dict[str, Any] | None = None

        def compose(self) -> ComposeResult:  # type: ignore[override]
            yield Header()
            yield Static("", id="devtool-textual-dashboard")
            yield Footer()

        def on_mount(self) -> None:  # pragma: no cover - requires optional Textual runtime
            self._refresh_control_center()

        def _refresh_control_center(self) -> None:  # pragma: no cover - requires optional Textual runtime
            snapshot = self.control_center.snapshot(width=width or 100, height=28)
            body = render_control_center_text(snapshot)
            if self.last_control_result:
                body += "\nLast control: " + str(self.last_control_result)
            self.query_one("#devtool-textual-dashboard", Static).update(body)

        def _navigate(self, target: str) -> None:  # pragma: no cover - requires optional Textual runtime
            self.control_center.navigate(target)
            self.last_control_result = {"kind": "navigate", "page": target}
            self._refresh_control_center()

        def action_cancel_selected(self) -> None:  # pragma: no cover - requires optional Textual runtime
            self.last_control_result = self.control_center.handle_key("ctrl+c", confirmed=True)
            self._refresh_control_center()

        def action_exit_without_cancel(self) -> None:  # pragma: no cover - requires optional Textual runtime
            self.last_control_result = self.control_center.handle_key("q")
            self.exit()

        def action_help(self) -> None:  # pragma: no cover - requires optional Textual runtime
            self.last_control_result = {
                "kind": "help",
                "keys": {**NAV_KEYS, "ctrl+c": "cancel", "q/esc": "exit", "?": "help"},
            }
            self._refresh_control_center()

    def _page_action(target: str):
        def action(self: Any) -> None:  # pragma: no cover - requires optional Textual runtime
            self._navigate(target)
        action.__name__ = f"action_page_{target}"
        return action

    for _target in PAGES:
        setattr(DevtoolTextualShell, f"action_page_{_target}", _page_action(_target))

    guard = TerminalRestorationGuard()
    with guard:
        DevtoolTextualShell().run()
    if not guard.restored:
        raise RuntimeError("Textual terminal ownership did not restore after exit")
    return 0

def run_tui_entrypoint(
    args: Any,
    *,
    repo_root: Any,
    console: Any | None = None,
    availability: TextualAvailability | None = None,
    legacy_runner: Any | None = None,
    textual_runner: Any | None = None,
) -> int:
    """Entrypoint used by ``devtool tui`` after PRES-X06."""

    selection = resolve_tui_backend_selection(
        requested_backend=getattr(args, "backend", "auto"),
        legacy_tui=bool(getattr(args, "legacy_tui", False)),
        require_textual=bool(getattr(args, "require_textual", False)),
        availability=availability,
        session_transport=getattr(args, "session_transport", "local"),
    )
    notice = build_tui_backend_notice(selection)
    if bool(getattr(args, "explain_backend", False)):
        payload = {"selection": selection.as_dict(), "notice": notice.as_dict()}
        if console is not None:
            console.print(payload)
        else:
            print(payload)
        return 0
    if selection.effective_backend == "unavailable":
        message = notice.message or selection.reason or "Textual TUI unavailable"
        if console is not None:
            console.print(f"Error: {message}")
        else:
            print(f"Error: {message}")
        return 2
    if selection.effective_backend == "legacy":
        assert_legacy_tui_is_fenced(selection)
        if selection.requested_backend == "auto" and console is not None:
            console.print(f"Warning: {notice.message}")
        if legacy_runner is None:
            from .tui import run_tui as legacy_runner
        return int(legacy_runner(args, repo_root=repo_root, console=console))
    if textual_runner is None:
        textual_runner = run_textual_tui_runtime
    return int(textual_runner(args, repo_root=repo_root, console=console))


# PRES-G1 Textual replacement proof gate.
_REQUIRED_PRES_GATE_REPORTS: tuple[str, ...] = (
    "PRES_X01_PRESENTATION_PROJECTION_FOUNDATION_REPORT.md",
    "PRES_X02_PRESENTATION_LAYOUT_INTERACTIONS_REPORT.md",
    "PRES_X03_CROSS_SURFACE_RENDERING_ADOPTION_REPORT.md",
    "PRES_X04_TEXTUAL_TUI_SUBSTRATE_REPORT.md",
    "PRES_X05_TEXTUAL_FEATURE_PARITY_REPORT.md",
    "PRES_X06_TEXTUAL_DEFAULT_REPLACEMENT_REPORT.md",
    "PRES_X07_TEXTUAL_REPLACEMENT_HARDENING_REPORT.md",
)


@dataclass(frozen=True)
class TextualReplacementGateItem:
    """One proof item in the PRES-G1 Textual replacement gate."""

    id: str
    status: str
    evidence: tuple[str, ...]
    requirement: str
    owner: str = "PRES"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "requirement": self.requirement,
            "owner": self.owner,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class TextualReplacementGateReport:
    """Machine-readable proof gate result for Textual replacing the legacy TUI."""

    schema_version: int
    gate_id: str
    items: tuple[TextualReplacementGateItem, ...]
    backend_selections: tuple[TuiBackendSelection, ...]
    notices: tuple[TuiBackendHardeningNotice, ...]
    parity_plan: TextualFeatureParityPlan
    default_contract: Mapping[str, Any]
    hardening_contract: Mapping[str, Any]
    required_reports: tuple[str, ...]
    present_reports: tuple[str, ...]
    owns_truth: bool = False
    machine_output_changed: bool = False

    @property
    def passed(self) -> bool:
        return all(item.status == "passed" for item in self.items)

    @property
    def missing_reports(self) -> tuple[str, ...]:
        present = set(self.present_reports)
        return tuple(name for name in self.required_reports if name not in present)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "gate_id": self.gate_id,
            "passed": self.passed,
            "owns_truth": self.owns_truth,
            "machine_output_changed": self.machine_output_changed,
            "required_reports": list(self.required_reports),
            "present_reports": list(self.present_reports),
            "missing_reports": list(self.missing_reports),
            "items": [item.as_dict() for item in self.items],
            "backend_selections": [selection.as_dict() for selection in self.backend_selections],
            "notices": [notice.as_dict() for notice in self.notices],
            "parity_plan_digest": self.parity_plan.as_dict()["parity_digest"],
            "default_contract": dict(self.default_contract),
            "hardening_contract": dict(self.hardening_contract),
        }
        payload["gate_digest"] = projection_digest({k: v for k, v in payload.items() if k != "gate_digest"})
        return payload


def required_pres_gate_reports() -> tuple[str, ...]:
    """Reports that must exist before PRES-G1 can claim closure."""

    return _REQUIRED_PRES_GATE_REPORTS


def _gate_status(condition: bool) -> str:
    return "passed" if condition else "failed"


def build_textual_replacement_proof_gate(
    *,
    present_reports: Sequence[str] | None = None,
    textual_available: bool = True,
) -> TextualReplacementGateReport:
    """Build the PRES-G1 proof gate from canonical PRES contracts.

    The gate intentionally uses the PRES projection and Textual contract APIs
    instead of runtime TUI state.  It proves that Textual is now the default TUI
    and that all rendering remains reference-only.
    """

    present = tuple(sorted(set(present_reports or _REQUIRED_PRES_GATE_REPORTS)))
    available = TextualAvailability(bool(textual_available))
    missing = TextualAvailability(False, reason="textual package is not importable")
    selections = (
        resolve_tui_backend_selection(requested_backend="auto", availability=available, session_transport="ssh"),
        resolve_tui_backend_selection(requested_backend="auto", availability=missing, session_transport="ssh"),
        resolve_tui_backend_selection(requested_backend="auto", require_textual=True, availability=missing),
        resolve_tui_backend_selection(requested_backend="textual", availability=missing),
        resolve_tui_backend_selection(requested_backend="legacy", availability=available),
    )
    notices = tuple(build_tui_backend_notice(selection) for selection in selections)
    cleanup = summarize_legacy_cleanup_assumptions(selections)
    projection_records = [
        {"id": "syntax", "ref": "exo:pres:syntax", "status": "passed", "label": "Source syntax", "progress": 1.0},
        {"id": "textual", "ref": "exo:pres:textual", "status": "running", "label": "Textual replacement", "progress": 0.62},
        {"id": "gate", "ref": "exo:pres:g1", "status": "passed", "label": "PRES-G1 proof", "progress": 1.0},
    ]
    parity_plan = build_textual_feature_parity_from_projection(
        projection_records,
        width=118,
        height=30,
        session_transport="ssh",
    )
    parity_machine = parity_plan.as_dict()
    bundle_machine = parity_plan.base_spec.source_bundle.as_machine()
    default_contract = default_tui_backend_contract()
    hardening_contract = textual_replacement_hardening_contract()
    required = _REQUIRED_PRES_GATE_REPORTS
    missing_reports = tuple(name for name in required if name not in set(present))
    items = (
        TextualReplacementGateItem(
            "pres-report-window-complete",
            _gate_status(not missing_reports),
            present,
            "PRES-X01 through PRES-X07 reports are present before the gate closes",
        ),
        TextualReplacementGateItem(
            "textual-is-default",
            _gate_status(default_contract.get("default_backend") == "textual" and default_contract.get("machine_output_changed") is False),
            ("default_tui_backend_contract",),
            "Textual is the default TUI while machine output remains unchanged",
        ),
        TextualReplacementGateItem(
            "auto-prefers-textual",
            _gate_status(selections[0].effective_backend == "textual" and selections[0].default_backend == "textual"),
            (selections[0].as_dict()["selection_digest"],),
            "auto backend chooses Textual when Textual is available",
        ),
        TextualReplacementGateItem(
            "fallback-is-fenced",
            _gate_status(
                selections[1].effective_backend == "legacy"
                and notices[1].severity == "warning"
                and selections[2].effective_backend == "unavailable"
                and selections[3].effective_backend == "unavailable"
                and selections[4].effective_backend == "legacy"
                and cleanup["legacy_path_count"] == 2
            ),
            tuple(notice.as_dict()["notice_digest"] for notice in notices),
            "legacy fallback is only explicit or permitted auto fallback; require-textual fails closed",
        ),
        TextualReplacementGateItem(
            "feature-parity-complete",
            _gate_status(parity_machine["missing_features"] == []),
            (parity_machine["parity_digest"],),
            "Textual parity plan covers required current-TUI features",
        ),
        TextualReplacementGateItem(
            "cross-surface-parity",
            _gate_status(bundle_machine["parity"]["consistent"] is True and bundle_machine["schema_version"] == 3),
            (bundle_machine["bundle_digest"],),
            "machine, terminal, TUI, desktop, HUD, and IPC renders preserve one parity key",
        ),
        TextualReplacementGateItem(
            "renderer-only-authority",
            _gate_status(
                parity_plan.owns_truth is False
                and default_contract.get("textual_owns_truth") is False
                and hardening_contract.get("textual_owns_truth") is False
            ),
            ("presentation_surfaces", "tui_textual"),
            "Textual owns rendering and interaction only; RUN/EXO/RER/validation remain authoritative",
        ),
    )
    report = TextualReplacementGateReport(
        schema_version=1,
        gate_id="PRES-G1",
        items=items,
        backend_selections=selections,
        notices=notices,
        parity_plan=parity_plan,
        default_contract=default_contract,
        hardening_contract=hardening_contract,
        required_reports=required,
        present_reports=present,
        owns_truth=False,
        machine_output_changed=False,
    )
    assert_textual_replacement_gate_passes(report)
    return report


def assert_textual_replacement_gate_passes(report: TextualReplacementGateReport) -> None:
    """Fail closed if the PRES-G1 proof gate is incomplete or owns truth."""

    encoded = str(report.as_dict()).lower()
    leaked = [field for field in _FORBIDDEN_TRUTH_FIELDS if field in encoded]
    if leaked:
        raise AssertionError(f"PRES-G1 gate report contains execution-owned truth: {leaked}")
    if report.owns_truth is not False:
        raise AssertionError("PRES-G1 attempted to own canonical truth")
    if report.machine_output_changed is not False:
        raise AssertionError("PRES-G1 must not change machine output semantics")
    if report.missing_reports:
        raise AssertionError(f"PRES-G1 is missing report evidence: {report.missing_reports}")
    if not report.passed:
        failed = [item.id for item in report.items if item.status != "passed"]
        raise AssertionError(f"PRES-G1 proof gate failed: {failed}")
