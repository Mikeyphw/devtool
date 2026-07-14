from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "desktop" / "devtool-control-center" / "qml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_qml_theme_references_resolve_to_declared_tokens() -> None:
    theme = read(QML / "components" / "Theme.qml")
    declared = set(
        re.findall(
            r"property\s+(?:readonly\s+)?(?:color|real|int|string|bool)\s+(\w+)",
            theme,
        )
    )
    declared.update({"alpha", "mix", "luminance"})

    unresolved: dict[str, list[str]] = {}
    for path in QML.rglob("*.qml"):
        missing = sorted(set(re.findall(r"\btheme\.(\w+)", read(path))) - declared)
        if missing:
            unresolved[str(path.relative_to(ROOT))] = missing

    assert unresolved == {}


def test_accessibility_is_not_attached_directly_to_popup_objects() -> None:
    offenders: list[str] = []
    for path in QML.rglob("*.qml"):
        source = read(path)
        for match in re.finditer(r"\bPopup\s*\{", source):
            start = match.end()
            depth = 1
            index = start
            while index < len(source) and depth:
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                index += 1
            block = source[start:index]
            content_index = block.find("contentItem:")
            direct_prefix = block if content_index < 0 else block[:content_index]
            if "Accessible." in direct_prefix:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_run_page_uses_scroll_view_width_and_valid_stage_palette() -> None:
    source = read(QML / "pages" / "RunPage.qml")
    assert "id: workflowScroll" in source
    assert "width: Math.max(0, workflowScroll.availableWidth)" in source
    assert "parent.availableWidth" not in source
    assert "theme.accentSurface" not in source
    assert "theme.selectedSurface" in source


def test_artifacts_page_has_top_aligned_inputs_and_an_empty_state() -> None:
    source = read(QML / "pages" / "ArtifactsPage.qml")
    assert "readonly property bool hasSummary" in source
    assert 'text: "Artifact"' in source
    assert 'text: "Repository"' in source
    assert 'text: artifactInspector.loading ? "Inspecting artifact…" : "No artifact inspected"' in source
    assert "Layout.fillHeight: visible" in source


def test_repository_current_runs_only_include_running_states() -> None:
    source = read(QML / "pages" / "RepositoriesPage.qml")
    assert 'String(run.status || "").toLowerCase() === "running"' in source
