import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import Devtool.ControlCenter.Components 1.0

AppScrollView {
    id: root
    signal openRun(var run)
    signal openRunPage()
    signal openLogsPage()
    Theme { id: theme }
    clip: true

    function runningCount() {
        return hudStore.activeRuns.filter(function(r) { return r.status === "running" }).length
    }
    function errorCount() {
        return hudStore.activeRuns.reduce(function(a, r) { return a + (r.errors || 0) }, 0)
    }
    function warningCount() {
        return hudStore.activeRuns.reduce(function(a, r) { return a + (r.warnings || 0) }, 0)
    }
    function primaryActionText() {
        if (appController.repository !== "")
            return runningCount() ? "Open live work" : "Validate current repository"
        return "Choose a repository"
    }
    function primaryActionDetail() {
        if (runningCount())
            return runningCount() + " operation" + (runningCount() === 1 ? " is" : "s are") + " running. Open the detailed HUD or inspect the latest run."
        if (appController.repository !== "")
            return "The selected repository is ready for a validation pass, overlay apply, test, package, or status check."
        return "Pick a repository, then build a workflow without writing shell commands."
    }

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
                lineHeight: theme.bodyLineHeight
            }
        }

        SurfaceCard {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            implicitHeight: nextActionColumn.implicitHeight + 32
            color: runningCount() ? theme.infoSurface : theme.surface
            border.color: runningCount() ? theme.info : theme.border

            ColumnLayout {
                id: nextActionColumn
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                Label {
                    Layout.fillWidth: true
                    text: "Next action"
                    color: theme.mutedText
                    font.pointSize: theme.labelPointSize
                    lineHeight: theme.compactLineHeight
                }
                Label {
                    Layout.fillWidth: true
                    text: primaryActionText()
                    color: theme.windowText
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.DemiBold
                    lineHeight: theme.titleLineHeight
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    text: primaryActionDetail()
                    color: theme.mutedText
                    font.pointSize: theme.bodyPointSize
                    lineHeight: theme.bodyLineHeight
                    wrapMode: Text.Wrap
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 10
                    AppButton {
                        text: appController.repository !== "" ? "Validate repository" : "Open Run"
                        variant: "primary"
                        width: Math.max(170, implicitWidth)
                        onClicked: appController.repository !== "" ? appController.runWorkflow("Validate", "", true, false, "", true) : root.openRunPage()
                    }
                    AppButton {
                        text: "Build workflow"
                        width: Math.max(150, implicitWidth)
                        onClicked: root.openRunPage()
                    }
                    AppButton {
                        text: "Open HUD"
                        variant: "ghost"
                        width: Math.max(120, implicitWidth)
                        onClicked: appController.launchHud()
                    }
                }
            }
        }

        SectionHeading {
            Layout.leftMargin: theme.pageMargin
            text: hudStore.activeRuns.length ? "Latest run" : "Latest run"
        }

        RunCard {
            visible: hudStore.activeRuns.length > 0
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            run: hudStore.activeRuns.length ? hudStore.activeRuns[0] : ({})
            onExpandRequested: root.openRun(run)
        }

        SurfaceCard {
            visible: !hudStore.activeRuns.length
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            implicitHeight: noRunColumn.implicitHeight + 32
            ColumnLayout {
                id: noRunColumn
                anchors.fill: parent
                anchors.margins: 16
                spacing: 7
                Label {
                    Layout.fillWidth: true
                    text: "No devtool operation is publishing live state."
                    color: theme.text
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.Medium
                    lineHeight: theme.bodyLineHeight
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    text: "Start from Run or paste an existing devtool command."
                    color: theme.mutedText
                    font.pointSize: theme.bodyPointSize
                    lineHeight: theme.bodyLineHeight
                    wrapMode: Text.Wrap
                }
            }
        }

        SectionHeading { Layout.leftMargin: theme.pageMargin; text: "Snapshot" }
        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            columns: root.availableWidth < 640 ? 1 : root.availableWidth < 980 ? 2 : 4
            columnSpacing: 10
            rowSpacing: 10
            MetricCard { label: "Current runs"; value: runningCount(); accent: theme.info; Layout.fillWidth: true }
            MetricCard { label: "Open errors"; value: errorCount(); accent: value ? theme.error : theme.success; Layout.fillWidth: true }
            MetricCard { label: "Warnings"; value: warningCount(); accent: value ? theme.warning : theme.success; Layout.fillWidth: true }
            MetricCard { label: "Known repos"; value: repositoryModel.count; accent: theme.violet; Layout.fillWidth: true }
        }

        SectionHeading { Layout.leftMargin: theme.pageMargin; text: "Quick actions" }
        Flow {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            spacing: 10
            AppButton { text: "Inspect artifact"; width: Math.max(150, implicitWidth); onClicked: appController.inspectArtifactRequested() }
            AppButton { text: "Refresh repos"; width: Math.max(140, implicitWidth); onClicked: repositoryModel.refresh() }
            AppButton { text: "Open logs"; width: Math.max(120, implicitWidth); onClicked: root.openRun(null) }
            AppButton { text: "Open HUD"; width: Math.max(120, implicitWidth); onClicked: appController.launchHud() }
        }

        Repeater {
            model: hudStore.activeRuns.length > 1 ? hudStore.activeRuns.slice(1) : []
            delegate: RunCard {
                required property var modelData
                Layout.fillWidth: true
                Layout.leftMargin: theme.pageMargin
                Layout.rightMargin: theme.pageMargin
                run: modelData
                onExpandRequested: root.openRun(modelData)
            }
        }
        Item { height: 16 }
    }
}
