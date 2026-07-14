import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "pages"

ApplicationWindow {
    id: root
    width: 1260
    height: 800
    minimumWidth: 640
    minimumHeight: 520
    visible: true
    color: palette.window
    title: "Devtool Control Center"
    property int pageIndex: 0
    readonly property bool narrowMode: width < 900
    readonly property bool veryNarrowMode: width < 720
    readonly property var pageComponents: [
        overviewPageComponent, runPageComponent, artifactsPageComponent,
        diagnosticsPageComponent, historyPageComponent, repositoriesPageComponent,
        settingsPageComponent
    ]
    Theme { id: theme }

    header: ToolBar {
        implicitHeight: root.veryNarrowMode ? 56 : 64
        background: Rectangle {
            color: theme.elevatedSurface
            border.color: theme.border
            border.width: 1
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: root.veryNarrowMode ? 10 : 16
            anchors.rightMargin: root.veryNarrowMode ? 10 : 16
            spacing: root.veryNarrowMode ? 8 : 12
            Label {
                text: root.veryNarrowMode ? "Devtool" : "Devtool Control Center"
                color: theme.windowText
                font.pointSize: root.veryNarrowMode ? theme.cardTitlePointSize : theme.pageTitlePointSize
                font.weight: Font.DemiBold
                font.letterSpacing: -0.2
                lineHeight: theme.titleLineHeight
                Accessible.role: Accessible.Heading
                Accessible.name: text
            }
            Rectangle {
                visible: !root.narrowMode
                radius: 6
                color: theme.infoSurface
                implicitWidth: phaseLabel.implicitWidth + 12
                implicitHeight: 24
                Accessible.ignored: true
                Label {
                    id: phaseLabel
                    anchors.centerIn: parent
                    text: "1.0 · Multi-runner"
                    color: theme.info
                    font.pointSize: theme.smallPointSize
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.25
                    lineHeight: theme.compactLineHeight
                }
            }
            Item { Layout.fillWidth: true }
            Label {
                visible: !root.veryNarrowMode
                text: appController.repository ? appController.repository.split("/").pop() : "No repository"
                color: theme.mutedText
                font.pointSize: theme.smallPointSize
                elide: Text.ElideMiddle
                Layout.maximumWidth: root.narrowMode ? 140 : 280
                Accessible.name: "Current repository, " + text
            }
            Rectangle {
                width: 9
                height: 9
                radius: 5
                color: hudStore.ipcConnected ? theme.success : hudStore.connected ? theme.warning : theme.subtleText
                Accessible.ignored: true
            }
            Label {
                text: hudStore.ipcConnected ? (root.veryNarrowMode ? String(hudStore.activeRuns.length) : "IPC · " + hudStore.activeRuns.length + " run" + (hudStore.activeRuns.length === 1 ? "" : "s")) : hudStore.connected ? "Fallback" : "Idle"
                color: theme.mutedText
                font.pointSize: theme.bodyPointSize
                lineHeight: theme.compactLineHeight
                Accessible.name: hudStore.ipcConnected ? hudStore.activeRuns.length + " active runs over IPC" : hudStore.connected ? "File fallback connection" : "No active connection"
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0
        Accessible.role: Accessible.Pane
        Accessible.name: "Devtool Control Center"
        Accessible.description: "Desktop control center for devtool repositories, workflows, diagnostics, and history"
        Rectangle {
            Layout.preferredWidth: root.narrowMode ? 72 : 218
            Layout.minimumWidth: root.narrowMode ? 72 : 180
            Layout.fillHeight: true
            color: theme.sidebar
            border.color: theme.border
            border.width: 1
            Accessible.role: Accessible.Pane
            Accessible.name: "Primary navigation"
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: root.narrowMode ? 8 : 12
                spacing: 8
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    background: Rectangle { color: "transparent" }
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    Column {
                        width: parent.width
                        spacing: 5
                        SidebarButton { width: parent.width; compact: root.narrowMode; iconSource: "icons/overview.svg"; text: "Overview"; selected: root.pageIndex === 0; onClicked: root.pageIndex = 0 }
                        SidebarButton { width: parent.width; compact: root.narrowMode; iconSource: "icons/run.svg"; text: "Run"; selected: root.pageIndex === 1; onClicked: root.pageIndex = 1 }
                        SidebarButton { width: parent.width; compact: root.narrowMode; iconSource: "icons/artifacts.svg"; text: "Artifacts"; selected: root.pageIndex === 2; onClicked: root.pageIndex = 2 }
                        SidebarButton { width: parent.width; compact: root.narrowMode; iconSource: "icons/diagnostics.svg"; text: "Diagnostics"; selected: root.pageIndex === 3; onClicked: root.pageIndex = 3 }
                        SidebarButton { width: parent.width; compact: root.narrowMode; iconSource: "icons/history.svg"; text: "History"; selected: root.pageIndex === 4; onClicked: root.pageIndex = 4 }
                        SidebarButton { width: parent.width; compact: root.narrowMode; iconSource: "icons/repositories.svg"; text: "Repositories"; selected: root.pageIndex === 5; onClicked: root.pageIndex = 5 }
                        SidebarButton { width: parent.width; compact: root.narrowMode; iconSource: "icons/settings.svg"; text: "Settings"; selected: root.pageIndex === 6; onClicked: root.pageIndex = 6 }
                    }
                }
                AppButton {
                    Layout.fillWidth: true
                    text: root.narrowMode ? "HUD" : "Open HUD"
                    accessibleName: "Open live HUD"
                    accessibleDescription: "Open the compact live progress window. Keyboard shortcut F9."
                    variant: "default"
                    onClicked: appController.launchHud()
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: theme.window
            Loader {
                id: pageLoader
                anchors.fill: parent
                asynchronous: true
                active: root.visible
                sourceComponent: active ? root.pageComponents[root.pageIndex] : null
                Accessible.role: Accessible.Pane
                Accessible.name: "Current page"
            }

            Component { id: overviewPageComponent; OverviewPage { onOpenRun: function(run) { hudStore.selectedRunId = run.run_id; root.pageIndex = 3 } } }
            Component { id: runPageComponent; RunPage { } }
            Component { id: artifactsPageComponent; ArtifactsPage { } }
            Component { id: diagnosticsPageComponent; DiagnosticsPage { } }
            Component { id: historyPageComponent; HistoryPage { } }
            Component { id: repositoriesPageComponent; RepositoriesPage { } }
            Component { id: settingsPageComponent; SettingsPage { } }
        }
    }

    onPageIndexChanged: {
        if (pageIndex === 6) appController.refreshClipboardMonitorStatus()
    }
    onVisibleChanged: appController.setStandby(!visible)

    Connections {
        target: appController
        function onRequestArtifactInspector() { root.pageIndex = 2 }
        function onRequestRunPage() { root.pageIndex = 1 }
    }
    Shortcut { sequence: "Ctrl+1"; onActivated: root.pageIndex = 0 }
    Shortcut { sequence: "Ctrl+2"; onActivated: root.pageIndex = 1 }
    Shortcut { sequence: "Ctrl+3"; onActivated: root.pageIndex = 2 }
    Shortcut { sequence: "Ctrl+4"; onActivated: root.pageIndex = 3 }
    Shortcut { sequence: "Ctrl+5"; onActivated: root.pageIndex = 4 }
    Shortcut { sequence: "Ctrl+6"; onActivated: root.pageIndex = 5 }
    Shortcut { sequence: "Ctrl+,"; onActivated: root.pageIndex = 6 }
    Shortcut { sequence: "Alt+1"; onActivated: root.pageIndex = 0 }
    Shortcut { sequence: "Alt+2"; onActivated: root.pageIndex = 1 }
    Shortcut { sequence: "Alt+3"; onActivated: root.pageIndex = 2 }
    Shortcut { sequence: "Alt+4"; onActivated: root.pageIndex = 3 }
    Shortcut { sequence: "Alt+5"; onActivated: root.pageIndex = 4 }
    Shortcut { sequence: "Alt+6"; onActivated: root.pageIndex = 5 }
    Shortcut { sequence: "Alt+7"; onActivated: root.pageIndex = 6 }
    Shortcut { sequence: "Ctrl+O"; onActivated: root.pageIndex = 2 }
    Shortcut { sequence: "F9"; onActivated: appController.launchHud() }
    onClosing: function(close) {
        if (appController.trayAvailable) {
            close.accepted = false
            root.hide()
        }
    }
}
