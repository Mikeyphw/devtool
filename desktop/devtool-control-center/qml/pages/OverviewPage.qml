import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

AppScrollView {
    id: root
    signal openRun(var run)
    Theme { id: theme }
    clip: true

    ColumnLayout {
        width: root.availableWidth
        spacing: theme.sectionSpacing

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            Layout.topMargin: 20
            PageHeading { text: "Overview" }
            Item { Layout.fillWidth: true }
            Rectangle {
                width: 9
                height: 9
                radius: 5
                color: hudStore.ipcConnected ? theme.success : hudStore.connected ? theme.warning : theme.subtleText
            }
            Label {
                text: hudStore.ipcConnected ? "Live IPC connected" : hudStore.connected ? "File fallback active" : "Waiting for devtool"
                color: theme.mutedText
                font.pointSize: theme.smallPointSize
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            spacing: 10
            MetricCard { label: "ACTIVE RUNS"; value: hudStore.activeRuns.filter(function(r) { return r.status === "running" }).length; accent: theme.info }
            MetricCard { label: "ERRORS"; value: hudStore.activeRuns.reduce(function(a, r) { return a + (r.errors || 0) }, 0); accent: value ? theme.error : theme.success }
            MetricCard { label: "WARNINGS"; value: hudStore.activeRuns.reduce(function(a, r) { return a + (r.warnings || 0) }, 0); accent: value ? theme.warning : theme.success }
            MetricCard { label: "REPOSITORIES"; value: repositoryModel.count; accent: theme.violet }
            Item { Layout.fillWidth: true }
        }

        SectionHeading {
            Layout.leftMargin: theme.pageMargin
            text: hudStore.activeRuns.length ? "Active and recent runs" : "No active runs"
        }

        Repeater {
            model: hudStore.activeRuns
            delegate: RunCard {
                required property var modelData
                Layout.fillWidth: true
                Layout.leftMargin: theme.pageMargin
                Layout.rightMargin: theme.pageMargin
                run: modelData
                onExpandRequested: root.openRun(modelData)
            }
        }

        SurfaceCard {
            visible: !hudStore.activeRuns.length
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            height: 150
            Column {
                anchors.centerIn: parent
                spacing: 7
                Label {
                    text: "No devtool operation is publishing live state."
                    color: theme.text
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.Medium
                    anchors.horizontalCenter: parent.horizontalCenter
                }
                Label {
                    text: "Start a workflow from Run or from a terminal."
                    color: theme.mutedText
                    font.pointSize: theme.bodyPointSize
                    anchors.horizontalCenter: parent.horizontalCenter
                }
            }
        }

        SectionHeading { Layout.leftMargin: theme.pageMargin; text: "Quick actions" }
        RowLayout {
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            spacing: 10
            AppButton { text: "Validate repository"; enabled: appController.repository !== ""; onClicked: appController.runWorkflow("Validate", "", true, false, "", true) }
            AppButton { text: "Inspect artifact"; onClicked: appController.inspectArtifactRequested() }
            AppButton { text: "Open compact HUD"; onClicked: appController.compactHudVisible = true }
            AppButton { text: "Refresh repositories"; onClicked: repositoryModel.refresh() }
            Item { Layout.fillWidth: true }
        }
        Item { height: 16 }
    }
}
