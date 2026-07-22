from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.hud import choose_hud_mode
from devtool.cli import build_parser
from devtool.tasker import start_auto_hud


class QtControlCenterTests(unittest.TestCase):
    def test_control_center_aliases(self) -> None:
        self.assertEqual(choose_hud_mode("control-center"), "control-center")
        self.assertEqual(choose_hud_mode("qt"), "control-center")
        self.assertEqual(choose_hud_mode("qt5"), "control-center")
        self.assertEqual(choose_hud_mode("qt6"), "control-center")

    def test_control_center_mode_publishes_without_spawning_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"DEVTOOL_TASKER_DIR": tmp, "DEVTOOL_HUD_SINGLETON": "reuse"},
            clear=False,
        ), patch("devtool.tasker.hud_registry_dir", return_value=Path(tmp)), patch("devtool.tasker.subprocess.run") as run:
            self.assertTrue(start_auto_hud(Path(tmp), mode="control-center"))
            run.assert_not_called()
            self.assertTrue((Path(tmp) / "hud-active.json").is_file())

    def test_cli_exposes_control_center_launcher(self) -> None:
        args = build_parser().parse_args(["control-center"])
        self.assertEqual(args.command, "control-center")

    def test_phase_one_sources_are_packaged(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        required = [
            root / "CMakeLists.txt",
            root / "src" / "main.cpp",
            root / "src" / "HudMain.cpp",
            root / "src" / "HudStateStore.cpp",
            root / "src" / "ProcessRunner.cpp",
            root / "qml" / "Main.qml",
            root / "qml" / "CompactHud.qml",
            root / "qml" / "pages" / "OverviewPage.qml",
            root / "qml" / "pages" / "RunPage.qml",
            root / "qml" / "pages" / "HistoryPage.qml",
            root / "qml" / "pages" / "SettingsPage.qml",
        ]
        self.assertTrue(all(path.is_file() for path in required))
        cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("Qt5::Quick", cmake)
        self.assertIn("Qt5::Widgets", cmake)
        self.assertIn("qt5_add_resources", cmake)
        self.assertIn("resources/devtool-control-center.qrc", cmake)
        self.assertIn("devtool-control-center-hud", cmake)
        qrc = (root / "resources" / "devtool-control-center.qrc").read_text(encoding="utf-8")
        self.assertIn('alias="devtool-control-center.svg"', qrc)
        self.assertIn('alias="devtool-control-center-tray.svg"', qrc)
        common_target = cmake.split("add_library(devtool-control-center-common STATIC", 1)[1].split(")", 1)[0]
        hud_target = cmake.split("add_executable(devtool-control-center-hud", 1)[1].split(")", 1)[0]
        for source in ("HudStateStore.cpp", "HistoryModel.cpp", "IpcClient.cpp"):
            self.assertIn(source, common_target)
            self.assertNotIn(source, hud_target)
        self.assertIn("devtool-control-center-common", cmake)
        controller = (root / "src" / "AppController.cpp").read_text(encoding="utf-8")
        self.assertIn('"--hud-mode","control-center"', controller)

class QtControlCenterPhaseTwoSourceTests(unittest.TestCase):
    def test_phase_two_sources_are_packaged(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        required = [
            root / "src" / "ArtifactInspector.cpp",
            root / "src" / "RepositoryModel.cpp",
            root / "qml" / "pages" / "ArtifactsPage.qml",
            root / "qml" / "pages" / "DiagnosticsPage.qml",
            root / "qml" / "pages" / "RepositoriesPage.qml",
        ]
        self.assertTrue(all(path.is_file() for path in required))
        cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("ArtifactInspector.cpp", cmake)
        qrc = (root / "resources" / "devtool-control-center.qrc").read_text(encoding="utf-8")
        self.assertIn("RepositoriesPage.qml", qrc)
        main_qml = (root / "qml" / "Main.qml").read_text(encoding="utf-8")
        self.assertIn('text: "Artifacts"', main_qml)
        self.assertIn('text: "Diagnostics"', main_qml)

class QtControlCenterPhaseThreeSourceTests(unittest.TestCase):
    def test_phase_three_ipc_sources_are_packaged(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        self.assertTrue((root / "src" / "IpcClient.cpp").is_file())
        cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("Qt5::Network", cmake)
        self.assertIn("IpcClient.cpp", cmake)
        main_cpp = (root / "src" / "main.cpp").read_text(encoding="utf-8")
        self.assertIn('"ipc-server"', main_cpp)
        run_card = (root / "qml" / "components" / "RunCard.qml").read_text(encoding="utf-8")
        self.assertIn("cancelRun", run_card)

    def test_qt_64_compatible_source_api(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        process_header = (root / "src" / "ProcessRunner.h").read_text(encoding="utf-8")
        hud_source = (root / "src" / "HudStateStore.cpp").read_text(encoding="utf-8")
        app_source = (root / "src" / "AppController.cpp").read_text(encoding="utf-8")
        main_source = (root / "src" / "main.cpp").read_text(encoding="utf-8")
        cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")

        self.assertIn("#include <QVariantMap>", process_header)
        self.assertNotIn("toString(item.value", hud_source)
        self.assertNotIn("toString(status)", app_source)
        self.assertNotIn("loadFromModule", main_source)
        self.assertIn("qrc:/qt/qml/Devtool/ControlCenter/qml/Main.qml", main_source)
        self.assertIn("resources/devtool-control-center.qrc", cmake)
        self.assertIn("qCeil(percent)", hud_source)
        self.assertIn('object.value("percent").toDouble(-1.0)', hud_source)
        self.assertIn('targetLabels.join(" • ")', hud_source)
        self.assertNotIn('QString::number(targets.size())+" targets"', hud_source)

    def test_system_palette_theme_and_styled_controls_are_packaged(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        components = root / "qml" / "components"
        required = [
            components / "Theme.qml",
            components / "AppButton.qml",
            components / "AppTextField.qml",
            components / "AppComboBox.qml",
            components / "AppCheckBox.qml",
            components / "AppTabButton.qml",
            components / "AppTextArea.qml",
            components / "PageHeading.qml",
            components / "SectionHeading.qml",
            components / "SurfaceCard.qml",
        ]
        self.assertTrue(all(path.is_file() for path in required))

        theme = (components / "Theme.qml").read_text(encoding="utf-8")
        self.assertIn("SystemPalette", theme)
        self.assertIn("activePalette.highlight", theme)
        main_cpp = (root / "src" / "main.cpp").read_text(encoding="utf-8")
        self.assertNotIn("app.setPalette", main_cpp)
        self.assertIn("QQuickStyle::setStyle", main_cpp)
        self.assertIn("QQuickStyle::setFallbackStyle", main_cpp)

        qrc = (root / "resources" / "devtool-control-center.qrc").read_text(encoding="utf-8")
        for path in required:
            self.assertIn(path.name, qrc)

        for page in (root / "qml" / "pages").glob("*.qml"):
            source = page.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"(?m)^\s*(Button|TextField|ComboBox|CheckBox|TabButton|TextArea)\s*\{")
            self.assertNotIn('color: "#', source)


    def test_scrollable_system_palette_pages_and_runnable_clipboard_command(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        components = root / "qml" / "components"
        self.assertTrue((components / "AppScrollView.qml").is_file())
        app_scroll = (components / "AppScrollView.qml").read_text(encoding="utf-8")
        self.assertIn("background: Rectangle { color: theme.window }", app_scroll)
        self.assertIn("ScrollBar.vertical.policy", app_scroll)

        for name in ("OverviewPage.qml", "RunPage.qml", "HistoryPage.qml", "SettingsPage.qml"):
            source = (root / "qml" / "pages" / name).read_text(encoding="utf-8")
            self.assertIn("AppScrollView {", source)

        main_qml = (root / "qml" / "Main.qml").read_text(encoding="utf-8")
        self.assertIn("color: theme.window", main_qml)
        self.assertIn("ScrollBar.horizontal.policy", main_qml)
        self.assertIn("launchHud()", main_qml)
        self.assertNotIn("CompactHud { }", main_qml)

        controller = (root / "src" / "AppController.cpp").read_text(encoding="utf-8")
        run_page = (root / "qml" / "pages" / "RunPage.qml").read_text(encoding="utf-8")
        settings_page = (root / "qml" / "pages" / "SettingsPage.qml").read_text(encoding="utf-8")
        main_cpp = (root / "src" / "main.cpp").read_text(encoding="utf-8")
        hud_cpp = (root / "src" / "HudMain.cpp").read_text(encoding="utf-8")
        hud_store = (root / "src" / "HudStateStore.cpp").read_text(encoding="utf-8")
        compact_hud = (root / "qml" / "CompactHud.qml").read_text(encoding="utf-8")
        pipeline = (root / "qml" / "components" / "PipelineView.qml").read_text(encoding="utf-8")
        clipboard = (root / "src" / "ClipboardMonitor.cpp").read_text(encoding="utf-8")
        self.assertIn("runClipboardCommand", controller)
        self.assertIn("clipboardCandidateCopyText", controller)
        self.assertIn("captureClipboardCandidate", controller)
        self.assertIn("devtool-control-center-tray.svg", controller)
        self.assertIn('text: "Run command…"', run_page)
        self.assertIn('text: "Run command"', run_page)
        self.assertIn('text: "Cleaned command"', run_page)
        self.assertIn("font.letterSpacing: 0.25", run_page)
        self.assertNotIn("font.letterSpacing: 0.7", run_page)
        self.assertIn("clipboardCandidateSnapshotText", run_page)
        self.assertIn("captureClipboardCandidate()", run_page)
        self.assertIn("commandConfirmation", run_page)
        self.assertIn("launchHud()", settings_page)
        self.assertIn("launchHud()", run_page)
        self.assertIn("setWindowIcon(controlCenterIcon())", main_cpp)
        self.assertIn("show-hud", main_cpp)
        self.assertIn("setPersistentSceneGraph(false)", main_cpp)
        self.assertIn("setPersistentGraphics(false)", main_cpp)
        self.assertIn("releaseResources()", main_cpp)
        self.assertIn("unloadUiTimer.setInterval(7000)", main_cpp)
        self.assertIn("engine.reset()", main_cpp)
        self.assertIn("std::unique_ptr<QQmlApplicationEngine>", main_cpp)
        self.assertIn("active: root.visible", main_qml)
        self.assertIn("Devtool Control Center HUD", hud_cpp)
        self.assertIn("mergeRunState", hud_store)
        self.assertIn("stateSequence", hud_store)
        self.assertIn("phaseProgressScore", hud_store)
        self.assertIn("copyPhaseProgressFields", hud_store)
        self.assertIn("m_fileRuns", hud_store)
        self.assertIn("scopeTypeFor", hud_store)
        self.assertIn("scopeNameFor", hud_store)
        self.assertIn('map["scopeType"] = step.scopeType', hud_store)
        self.assertIn('map["key"] = step.base+":"+step.scopeType+":"+step.scope', hud_store)
        self.assertIn("hudStore.ipcConnected", compact_hud)
        self.assertIn("humanCommand(command)", compact_hud)
        self.assertIn('"apply-overlays": "Applying overlay bundle"', compact_hud)
        self.assertIn("scopedLanes()", compact_hud)
        self.assertIn("workspaceColumn", compact_hud)
        self.assertIn("laneProgress(lane)", compact_hud)
        self.assertIn("aggregateParserMetrics()", compact_hud)
        self.assertIn('text: "Details"', compact_hud)
        self.assertIn("runnerLabel()", compact_hud)
        self.assertIn("projectUnitLabel()", compact_hud)
        self.assertNotIn("LIVE .NET PARSER", compact_hud)
        self.assertIn("visible: parserExpanded", compact_hud)
        self.assertIn("hudStore.copySummary(run)", compact_hud)
        self.assertIn("hudStore.openLog(run)", compact_hud)
        self.assertIn('return "rollback-failed"', compact_hud)
        self.assertIn('return "RECOVERED"', compact_hud)
        self.assertIn('return "ROLLBACK FAILED"', compact_hud)
        self.assertIn("sharedVisibleSteps()", compact_hud)
        self.assertIn("Layout.preferredHeight: Math.min(342, workspaceColumn.implicitHeight)", compact_hud)
        self.assertIn("ListView.Horizontal", pipeline)
        self.assertIn("reuseItems: true", pipeline)
        self.assertIn("WheelHandler", pipeline)
        self.assertIn("border.color: theme.alpha(theme.windowText, 0.24)", compact_hud)
        self.assertIn('map["testsPassed"]', hud_store)
        self.assertIn('map["testAssemblies"]', hud_store)
        self.assertIn('state["rolled_back"]', hud_store)
        self.assertIn('state["rollback_failed"]', hud_store)
        self.assertIn("copySummary", hud_store)
        self.assertIn("openLog", hud_store)
        self.assertIn("Devtool command:", clipboard)
        self.assertIn("looksLikeDevtoolCommand", clipboard)

    def test_desktop_window_uses_qt_compact_hud_and_keeps_gtk_legacy_alias(self) -> None:
        import devtool.hud as hud_module
        source = Path(hud_module.__file__).read_text(encoding="utf-8")
        self.assertIn("class QtCompactHud", source)
        self.assertIn("_control_center_hud_binary()", source)
        self.assertIn("argv = [binary]", source)
        self.assertIn('if mode == "desktop-window":\n        return QtCompactHud()', source)
        self.assertIn('if mode == "gtk-window":', source)

        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        main_cpp = (root / "src" / "main.cpp").read_text(encoding="utf-8")
        main_qml = (root / "qml" / "Main.qml").read_text(encoding="utf-8")
        self.assertIn('"--compact-hud"', main_cpp)
        self.assertIn("launchHud()", main_qml)
        self.assertIn('if (command == QStringLiteral("show-hud"))', main_cpp)

    def test_compact_hud_keeps_visibility_binding_and_widens_layout_for_readability(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        compact_hud = (root / "qml" / "CompactHud.qml").read_text(encoding="utf-8")
        pipeline = (root / "qml" / "components" / "PipelineView.qml").read_text(encoding="utf-8")
        self.assertIn("visible: true", compact_hud)
        self.assertIn("Qt.quit()", compact_hud)
        self.assertIn("startSystemMove()", compact_hud)
        self.assertIn("startSystemResize(Qt.RightEdge)", compact_hud)
        self.assertNotIn("appController.compactHudVisible", compact_hud)
        self.assertIn("width: 720", compact_hud)
        self.assertIn("minimumWidth: 620", compact_hud)
        self.assertIn("minimumHeight: 224", compact_hud)
        self.assertIn("maximumHeight: 680", compact_hud)
        self.assertNotIn("height: Math.min(maximumHeight", compact_hud)
        self.assertIn("root.height = root.initialHudHeight", compact_hud)
        self.assertIn("id: bodyScroll", compact_hud)
        self.assertIn("ScrollBar.vertical.policy: lowerContent.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff", compact_hud)
        self.assertIn("contentLayout.implicitHeight + 32", compact_hud)
        self.assertIn("ColumnLayout", compact_hud)
        self.assertIn("id: workspaceColumn", compact_hud)
        self.assertIn("normalizedStepProgress(step)", compact_hud)
        self.assertIn("return Math.round(total / values.length)", compact_hud)
        self.assertIn("property bool parserExpanded: false", compact_hud)
        self.assertIn("property string expandedLaneKey", compact_hud)
        self.assertIn('text: copiedFeedback ? "Copied" : "Copy"', compact_hud)
        self.assertIn('enabled: !!run.log_path', compact_hud)
        self.assertIn("implicitHeight: 76", pipeline)
        self.assertIn("height: 72", pipeline)

    def test_compact_hud_remembers_position_and_uses_translucent_background(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        hud_cpp = (root / "src" / "HudMain.cpp").read_text(encoding="utf-8")
        compact_hud = (root / "qml" / "CompactHud.qml").read_text(encoding="utf-8")

        self.assertIn('kHudPositionKey = "hud/windowPosition"', hud_cpp)
        self.assertIn("restoreHudPosition(window)", hud_cpp)
        self.assertIn("saveHudPosition(window)", hud_cpp)
        self.assertIn("hudPositionIsVisible", hud_cpp)
        self.assertIn("QGuiApplication::screenRemoved", hud_cpp)
        self.assertIn("format.setAlphaBufferSize(8)", hud_cpp)
        self.assertIn("property real backgroundOpacity", compact_hud)
        self.assertIn("theme.alpha(theme.window, root.backgroundOpacity)", compact_hud)

class QtControlCenterPackagingTests(unittest.TestCase):
    def test_packaging_uses_clean_out_of_source_build(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        build_script = (root / "build.sh").read_text(encoding="utf-8")
        package_script = (root / "package-deb.sh").read_text(encoding="utf-8")

        self.assertIn('XDG_CACHE_HOME', build_script)
        self.assertIn('normalize_future_mtimes', build_script)
        self.assertIn('CMAKE_HOME_DIRECTORY:INTERNAL=', build_script)
        self.assertIn('CMAKE_CXX_COMPILER_LAUNCHER=ccache', build_script)
        self.assertIn('needs_configure=0', build_script)
        self.assertIn('cmake --build "$build"', build_script)
        self.assertNotIn('build=${BUILD_DIR:-"$root/build"}', build_script)

        self.assertIn('package-build-${version}-${arch}', package_script)
        self.assertIn('"$rm_bin" -rf "$build"', package_script)
        self.assertIn('BUILD_DIR="$build" "$root/build.sh"', package_script)
        self.assertNotIn('if [[ ! -x "$build/devtool-control-center" ]]', package_script)

    def test_debian_packaging_assets_are_present_and_preflighted(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        desktop_file = root / "packaging" / "io.github.mikeceead.DevtoolControlCenter.desktop"
        icon_file = root / "packaging" / "io.github.mikeceead.DevtoolControlCenter.svg"
        control_file = root / "packaging" / "debian" / "control"
        self.assertTrue(desktop_file.is_file())
        self.assertTrue(icon_file.is_file())
        self.assertTrue(control_file.is_file())

        desktop = desktop_file.read_text(encoding="utf-8")
        self.assertIn("[Desktop Entry]", desktop)
        self.assertIn("TryExec=/usr/bin/devtool-control-center", desktop)
        self.assertIn("Exec=/usr/bin/devtool-control-center", desktop)
        self.assertIn("Icon=io.github.mikeceead.DevtoolControlCenter", desktop)
        self.assertIn("StartupWMClass=devtool-control-center", desktop)

        cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn(desktop_file.name, cmake)
        self.assertIn(icon_file.name, cmake)

        package_script = (root / "package-deb.sh").read_text(encoding="utf-8")
        self.assertIn("required_packaging_files", package_script)
        self.assertIn(desktop_file.name, package_script)
        self.assertIn(icon_file.name, package_script)
        self.assertIn("packaging/debian/postinst", package_script)
        self.assertIn("gtk-update-icon-cache", (root / "packaging/debian/postinst").read_text(encoding="utf-8"))
        self.assertTrue((root / "packaging/icons/128x128/io.github.mikeceead.DevtoolControlCenter.png").is_file())

    def test_debian_package_does_not_require_apt_devtool_package(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        control = (root / "packaging" / "debian" / "control").read_text(encoding="utf-8")
        depends = next(line for line in control.splitlines() if line.startswith("Depends:"))
        self.assertNotIn("devtool", depends.split(":", 1)[1].split(","))
        app_source = (root / "src" / "AppController.cpp").read_text(encoding="utf-8")
        self.assertIn("QStandardPaths::findExecutable", app_source)
        self.assertIn(".local/bin/devtool", app_source)

    def test_debian_package_declares_implicit_qml_runtime_modules(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        control = (root / "packaging" / "debian" / "control").read_text(encoding="utf-8")
        depends = next(line for line in control.splitlines() if line.startswith("Depends:"))
        required = {
            "qml-module-qtqml",
            "qml-module-qtqml-models2",
            "qml-module-qtqml-workerscript2",
            "qml-module-qtquick2",
            "qml-module-qtquick-window2",
            "qml-module-qtquick-templates2",
            "qml-module-qtquick-controls2",
            "qml-module-qtquick-layouts",
            "qml-module-qtquick-dialogs",
        }
        declared = {item.strip() for item in depends.split(":", 1)[1].split(",")}
        self.assertTrue(required.issubset(declared))

        package_script = (root / "package-deb.sh").read_text(encoding="utf-8")
        self.assertIn("required_qml_packages", package_script)
        self.assertIn("qml-module-qtqml-workerscript2", package_script)

class QtControlCenterClipboardMonitorTests(unittest.TestCase):
    def test_clipboard_monitor_service_sources_are_packaged(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        self.assertTrue((root / "src" / "ClipboardMonitor.cpp").is_file())
        self.assertTrue((root / "src" / "ClipboardMonitor.h").is_file())
        cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("ClipboardMonitor.cpp", cmake)
        main_source = (root / "src" / "main.cpp").read_text(encoding="utf-8")
        self.assertIn('"--clipboard-monitor"', main_source)
        self.assertIn("ClipboardMonitor clipboardMonitor(true, true)", main_source)

    def test_clipboard_monitor_is_installable_and_never_auto_executes(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        controller = (root / "src" / "AppController.cpp").read_text(encoding="utf-8")
        monitor = (root / "src" / "ClipboardMonitor.cpp").read_text(encoding="utf-8")
        settings = (root / "qml" / "pages" / "SettingsPage.qml").read_text(encoding="utf-8")
        run_page = (root / "qml" / "pages" / "RunPage.qml").read_text(encoding="utf-8")

        self.assertIn("devtool-control-center-clipboard.service", controller)
        self.assertIn("systemctl", controller)
        self.assertIn("enable", controller)
        self.assertIn("disable", controller)
        self.assertIn("--clipboard-monitor", controller)
        self.assertIn("Install & enable", settings)
        self.assertIn("never executes", settings)
        self.assertIn("Detected clipboard command", run_page)
        self.assertIn("looksLikeArtifact", monitor)
        self.assertIn("looksLikeDevtoolCommand", monitor)
        self.assertIn("reportHeader", monitor)
        self.assertIn("QClipboard::dataChanged", monitor)
        self.assertNotIn("startDetached(normalized", monitor)
        self.assertNotIn("m_runner->start", monitor)

    def test_clipboard_monitor_package_uses_optional_notifications(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        control = (root / "packaging" / "debian" / "control").read_text(encoding="utf-8")
        self.assertIn("Suggests: python3-pip, pipx, libnotify-bin", control)
        monitor = (root / "src" / "ClipboardMonitor.cpp").read_text(encoding="utf-8")
        self.assertIn('findExecutable(QStringLiteral("notify-send"))', monitor)

class QtControlCenterRepositoryUxSourceTests(unittest.TestCase):
    def test_command_import_preserves_cd_and_fills_run_form(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        clipboard = (root / "src" / "ClipboardMonitor.cpp").read_text(encoding="utf-8")
        controller = (root / "src" / "AppController.cpp").read_text(encoding="utf-8")
        run_page = (root / "qml" / "pages" / "RunPage.qml").read_text(encoding="utf-8")

        self.assertIn("Preserve a shell prelude", clipboard)
        self.assertIn("block << line", clipboard)
        self.assertIn("pasteCommandFromClipboard", controller)
        self.assertIn("parseDevtoolCommand", controller)
        self.assertIn('QStringLiteral("zsh")', controller)
        self.assertIn('text: "Paste from clipboard"', run_page)
        self.assertIn("applyImportedCommand", run_page)
        self.assertIn("artifactExists", run_page)
        self.assertIn("manifest_present", run_page)
        self.assertIn("manifest_json_valid", run_page)
        self.assertIn("devtool-artifact.json", run_page)
        self.assertIn("launchExternalTerminal", controller)
        self.assertIn('QStringLiteral("kitty")', controller)
        self.assertIn('.local/share/mise/shims', controller)
        self.assertIn('QStringLiteral("-lc")', controller)
        self.assertIn("targetExplicit", controller)
        self.assertIn("commitExplicit", controller)
        self.assertIn("applyArtifactManifestDefaults", run_page)
        self.assertIn("summary.commit", run_page)
        self.assertNotIn("processRunner.output", run_page)

    def test_repository_registry_is_opt_in_and_git_workspace_is_packaged(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        main_source = (root / "src" / "main.cpp").read_text(encoding="utf-8")
        repo_source = (root / "src" / "RepositoryModel.cpp").read_text(encoding="utf-8")
        git_header = root / "src" / "GitRepositoryController.h"
        git_source = root / "src" / "GitRepositoryController.cpp"
        repos_page = (root / "qml" / "pages" / "RepositoriesPage.qml").read_text(encoding="utf-8")
        cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")

        self.assertTrue(git_header.is_file())
        self.assertTrue(git_source.is_file())
        self.assertNotIn("repositories.addRepository(controller.repository())", main_source)
        self.assertIn("Repositories are opt-in", repo_source)
        self.assertIn('item[QStringLiteral("saved")] = false', repo_source)
        self.assertIn("Live-only repositories", repo_source)
        self.assertIn("SavedRole", (root / "src" / "RepositoryModel.h").read_text(encoding="utf-8"))
        self.assertIn('"Save repository"', repos_page)
        self.assertIn("LIVE · NOT SAVED", repos_page)
        self.assertIn("GitRepositoryController.cpp", cmake)
        self.assertIn('AppTabButton { text: "Activity" }', repos_page)
        self.assertIn('AppTabButton { text: "Git" }', repos_page)
        self.assertIn("PathPickerDialog", repos_page)
        self.assertIn("repositoryDialog.open()", repos_page)
        self.assertIn("repositoryModel.addRepository(path)", repos_page)
        self.assertIn("historyModel.forRepository", repos_page)
        self.assertIn("gitRepository.commits", repos_page)
        self.assertIn("modelData.artifactName", repos_page)
        history_header = (root / "src" / "HistoryModel.h").read_text(encoding="utf-8")
        history_source = (root / "src" / "HistoryModel.cpp").read_text(encoding="utf-8")
        self.assertIn("artifactForCommit", history_header)
        self.assertIn("HistoryModel::artifactForCommit", history_source)

    def test_external_terminal_and_manifest_defaults_are_wired(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        header = (root / "src" / "AppController.h").read_text(encoding="utf-8")
        source = (root / "src" / "AppController.cpp").read_text(encoding="utf-8")
        run_page = (root / "qml" / "pages" / "RunPage.qml").read_text(encoding="utf-8")
        settings = (root / "qml" / "pages" / "SettingsPage.qml").read_text(encoding="utf-8")

        self.assertIn("preferredTerminal", header)
        self.assertIn("dotnetExecutable", header)
        self.assertIn("autoCloseTerminal", header)
        self.assertIn("launchExternalTerminal", header)
        self.assertIn('QStringLiteral("kitty")', source)
        self.assertIn('.local/share/mise/shims/dotnet', source)
        self.assertIn('export PATH=', source)
        self.assertIn("QProcess::startDetached", source)
        self.assertIn("QFileDialog::getExistingDirectory", source)
        self.assertIn("applyArtifactManifestDefaults", run_page)
        self.assertIn("inferredArtifactTarget", run_page)
        self.assertIn("External terminal execution", run_page)
        self.assertNotIn("processRunner.output", run_page)
        self.assertIn("Close terminal automatically", settings)

    def test_system_fonts_and_high_transparency_are_applied(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        main_source = (root / "src" / "main.cpp").read_text(encoding="utf-8")
        hud_source = (root / "src" / "HudMain.cpp").read_text(encoding="utf-8")
        theme = (root / "qml" / "components" / "Theme.qml").read_text(encoding="utf-8")
        compact = (root / "qml" / "CompactHud.qml").read_text(encoding="utf-8")

        self.assertIn("QFontDatabase::GeneralFont", main_source)
        self.assertIn("QFontDatabase::GeneralFont", hud_source)
        self.assertIn("uiMonospaceFont", main_source)
        self.assertIn("uiMonospaceFont", hud_source)
        self.assertNotIn("app.setFont(QFont(preferredNerdFont()))", main_source)
        self.assertNotIn("app.setFont(QFont(preferredNerdFont()))", hud_source)
        self.assertIn("monospaceFamily", theme)
        self.assertIn("theme.dark ? 0.14 : 0.18", compact)
        self.assertIn("panelOpacity", compact)


class QtControlCenterMultiProjectUiTests(unittest.TestCase):
    def test_run_page_exposes_multi_project_and_test_selection(self) -> None:
        root = Path(__file__).resolve().parents[1] / "desktop" / "devtool-control-center"
        qml = (root / "qml" / "pages" / "RunPage.qml").read_text(encoding="utf-8")
        controller = (root / "src" / "AppController.cpp").read_text(encoding="utf-8")
        header = (root / "src" / "AppController.h").read_text(encoding="utf-8")
        for value in ("Project type", "Execution", "Test profile", "Selectors", "Instrumentation", "Command preview"):
            self.assertIn(value, qml)
        for flag in ("--project-type", "--execution-environment", "--test-profile", "--select", "--instrumented"):
            self.assertIn(flag, controller)
        self.assertIn("buildWorkflowCommand", header)
