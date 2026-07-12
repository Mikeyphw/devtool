import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "pages"

ApplicationWindow {
    id: root
    width: 1260
    height: 800
    minimumWidth: 940
    minimumHeight: 620
    visible: !appController.startCompactOnly
    color: palette.window
    title: "Devtool Control Center"
    property int pageIndex: 0
    Theme { id: theme }

    header: ToolBar {
        implicitHeight: 64
        background: Rectangle {
            color: theme.elevatedSurface
            border.color: theme.border
            border.width: 1
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            spacing: 12
            Label {
                text: "Devtool Control Center"
                color: theme.windowText
                font.pointSize: theme.pageTitlePointSize
                font.weight: Font.DemiBold
                font.letterSpacing: -0.2
                lineHeight: theme.titleLineHeight
                lineHeightMode: Text.ProportionalHeight
            }
            Rectangle {
                radius: 6
                color: theme.infoSurface
                implicitWidth: phaseLabel.implicitWidth + 12
                implicitHeight: 24
                Label {
                    id: phaseLabel
                    anchors.centerIn: parent
                    text: "Phase 3"
                    color: theme.info
                    font.pointSize: theme.smallPointSize
                    font.weight: Font.DemiBold
                    font.capitalization: Font.AllUppercase
                    font.letterSpacing: 0.7
                    lineHeight: theme.compactLineHeight
                    lineHeightMode: Text.ProportionalHeight
                }
            }
            Item { Layout.fillWidth: true }
            Label {
                text: appController.repository ? appController.repository.split("/").pop() : "No repository"
                color: theme.mutedText
                font.pointSize: theme.smallPointSize
                elide: Text.ElideMiddle
                Layout.maximumWidth: 280
            }
            Rectangle {
                width: 9
                height: 9
                radius: 5
                color: hudStore.ipcConnected ? theme.success : hudStore.connected ? theme.warning : theme.subtleText
            }
            Label {
                text: hudStore.ipcConnected ? ("IPC · " + hudStore.activeRuns.length + " run" + (hudStore.activeRuns.length === 1 ? "" : "s")) : hudStore.connected ? "File fallback" : "Idle"
                color: theme.mutedText
                font.pointSize: theme.bodyPointSize
                lineHeight: theme.compactLineHeight
                lineHeightMode: Text.ProportionalHeight
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.preferredWidth: 218
            Layout.fillHeight: true
            color: theme.sidebar
            border.color: theme.border
            border.width: 1
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
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
                        SidebarButton { width: parent.width; iconText: "⌂"; text: "Overview"; selected: root.pageIndex === 0; onClicked: root.pageIndex = 0 }
                        SidebarButton { width: parent.width; iconText: "▶"; text: "Run"; selected: root.pageIndex === 1; onClicked: root.pageIndex = 1 }
                        SidebarButton { width: parent.width; iconText: "◇"; text: "Artifacts"; selected: root.pageIndex === 2; onClicked: root.pageIndex = 2 }
                        SidebarButton { width: parent.width; iconText: "!"; text: "Diagnostics"; selected: root.pageIndex === 3; onClicked: root.pageIndex = 3 }
                        SidebarButton { width: parent.width; iconText: "◷"; text: "History"; selected: root.pageIndex === 4; onClicked: root.pageIndex = 4 }
                        SidebarButton { width: parent.width; iconText: "⌘"; text: "Repositories"; selected: root.pageIndex === 5; onClicked: root.pageIndex = 5 }
                        SidebarButton { width: parent.width; iconText: "⚙"; text: "Settings"; selected: root.pageIndex === 6; onClicked: root.pageIndex = 6 }
                    }
                }
                AppButton { Layout.fillWidth: true; text: "Compact HUD"; variant: "default"; onClicked: appController.compactHudVisible = true }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: theme.window
            StackLayout {
                anchors.fill: parent
            currentIndex: root.pageIndex
            OverviewPage { onOpenRun: function(run) { hudStore.selectedRunId = run.run_id; root.pageIndex = 3 } }
            RunPage { }
            ArtifactsPage { }
            DiagnosticsPage { }
            HistoryPage { }
            RepositoriesPage { }
            SettingsPage { }
        }
        }
    }

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
    Shortcut { sequence: "Ctrl+O"; onActivated: root.pageIndex = 2 }
    Shortcut { sequence: "F9"; onActivated: appController.compactHudVisible = !appController.compactHudVisible }
    CompactHud { }
    onClosing: function(close) {
        if (appController.trayAvailable) {
            close.accepted = false
            root.hide()
        }
    }
}
