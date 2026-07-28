import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root
    property var run: ({})
    signal expandRequested()
    Theme { id: theme }
    radius: theme.cardRadius
    color: theme.surface
    border.color: run.run_id === hudStore.selectedRunId ? theme.accent : theme.border
    border.width: 1
    implicitHeight: body.implicitHeight + 32
    activeFocusOnTab: true
    Accessible.role: Accessible.Grouping
    Accessible.name: (run.title || run.command || "Devtool run") + ", " + String(run.status || "idle")
    Accessible.description: (run.repo_name || "Repository") + (run.target_summary ? ", " + run.target_summary : "") + ". " + (run.message || "Waiting for progress")
    Keys.onReturnPressed: { hudStore.selectedRunId = run.run_id; root.expandRequested() }
    Keys.onEnterPressed: { hudStore.selectedRunId = run.run_id; root.expandRequested() }

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Label {
                    Layout.fillWidth: true
                    text: run.title || run.command || "Devtool run"
                    color: theme.windowText
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Label {
                    Layout.fillWidth: true
                    text: (run.repo_name || "Repository") + (run.target_summary ? " · " + run.target_summary : "")
                    color: theme.mutedText
                    font.pointSize: theme.smallPointSize
                    elide: Text.ElideRight
                }
            }
            Label {
                text: (run.status || "idle").toUpperCase()
                color: run.status === "failed" ? theme.error : run.status === "ok" ? theme.success : theme.info
                font.weight: Font.DemiBold
                font.pointSize: theme.smallPointSize
            }
            AppButton { text: "Details"; accessibleDescription: "Open this run in Diagnostics"; variant: "ghost"; compact: true; onClicked: { hudStore.selectedRunId = run.run_id; root.expandRequested() } }
        }
        PipelineView { Layout.fillWidth: true; steps: run.steps || [] }
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            MetricCard { label: "ERRORS"; value: run.errors || 0; accent: value ? theme.error : theme.success }
            MetricCard { label: "WARNINGS"; value: run.warnings || 0; accent: value ? theme.warning : theme.success }
            ColumnLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: run.message || "Waiting for progress"; color: theme.text; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.pointSize: theme.bodyPointSize }
                Label { text: run.updated_at || run.started_at || ""; color: theme.subtleText; font.pointSize: theme.captionPointSize }
            }
            AppButton { visible: !run.stale && (run.status === "running" || run.status === "waiting"); text: "Cancel"; variant: "danger"; compact: true; onClicked: appController.cancelRun(run.run_id) }
            AppButton { text: "Copy"; variant: "ghost"; compact: true; onClicked: appController.copyText(hudStore.focusedSummary(run)) }
            AppButton { text: "Log"; variant: "ghost"; compact: true; enabled: !!run.log_path || !!((run.files || {}).state); onClicked: appController.openPath(run.log_path || ((run.files || {}).state || "")) }
        }
    }
}
