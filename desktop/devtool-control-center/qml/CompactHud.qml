import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Window {
    id: root
    width: 560
    height: 310
    visible: appController.compactHudVisible
    color: "transparent"
    flags: Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint
    property var run: hudStore.activeRun
    Theme { id: theme }

    onClosing: function(close) {
        close.accepted = false
        appController.compactHudVisible = false
    }

    Rectangle {
        anchors.fill: parent
        color: theme.surface
        border.color: theme.strongBorder
        border.width: 1
        radius: 12
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: (run.repo_name || "Devtool") + (run.target_summary ? " · " + run.target_summary : "")
                    color: theme.windowText
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                AppButton { text: "×"; variant: "ghost"; compact: true; implicitWidth: 34; onClicked: appController.compactHudVisible = false }
            }
            PipelineView { Layout.fillWidth: true; steps: run.steps || [] }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                MetricCard { label: "ERRORS"; value: run.errors || 0; accent: value ? theme.error : theme.success }
                MetricCard { label: "WARNINGS"; value: run.warnings || 0; accent: value ? theme.warning : theme.success }
                Label {
                    Layout.fillWidth: true
                    text: run.message || "Waiting for a run"
                    color: theme.text
                    wrapMode: Text.Wrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                    font.pointSize: theme.bodyPointSize
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                AppButton { text: "Copy"; variant: "ghost"; compact: true; onClicked: appController.copyText(hudStore.focusedSummary(run)) }
                AppButton { text: "Open log"; variant: "ghost"; compact: true; enabled: !!run.log_path; onClicked: appController.openPath(run.log_path) }
                Item { Layout.fillWidth: true }
                AppButton { text: "Control Center"; variant: "primary"; compact: true; onClicked: { root.visible = false; appController.showMainWindow() } }
            }
        }
    }
}
