import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Window {
    id: root
    width: 640
    height: 430
    minimumWidth: 580
    minimumHeight: 380
    visible: true
    color: "transparent"
    flags: Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint

    property var run: hudStore.activeRun
    property bool detailsExpanded: false

    Theme { id: theme }

    function runStatus() {
        const status = (run && run.status) ? String(run.status) : "idle"
        if (status === "ok") return "Complete"
        if (status === "failed") return "Failed"
        if (status === "running") return "Running"
        if (status === "skipped") return "Skipped"
        return "Waiting"
    }

    function runStatusColor() {
        const status = (run && run.status) ? String(run.status) : "idle"
        if (status === "ok") return theme.success
        if (status === "failed") return theme.error
        if (status === "running") return theme.warning
        if (status === "skipped") return theme.subtleText
        return theme.info
    }

    function runTitle() {
        if (!run) return "Waiting for progress"
        return run.title || run.command || "Waiting for progress"
    }

    function runContext() {
        if (!run) return "No active devtool run"
        const repo = run.repo_name || "Devtool"
        const target = run.target_summary || ""
        return target ? repo + " · " + target : repo
    }

    function runDetail() {
        if (!run) return "No devtool task has reported progress yet."
        return run.message || "No status message yet."
    }

    function tileTitle(value) {
        return String(value || "").toUpperCase()
    }

    onClosing: function(close) { close.accepted = true; Qt.quit() }

    Rectangle {
        id: shell
        anchors.fill: parent
        radius: 18
        clip: true
        border.color: theme.strongBorder
        border.width: 1
        gradient: Gradient {
            GradientStop { position: 0.0; color: theme.mix(theme.surface, theme.window, theme.dark ? 0.14 : 0.06) }
            GradientStop { position: 0.55; color: theme.surface }
            GradientStop { position: 1.0; color: theme.mix(theme.elevatedSurface, theme.window, theme.dark ? 0.06 : 0.03) }
        }

        Item {
            anchors.fill: parent
            opacity: 0.75
            Rectangle {
                x: -60; y: -80
                width: 170; height: 170
                radius: 85
                color: theme.alpha(theme.info, theme.dark ? 0.12 : 0.07)
            }
            Rectangle {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.rightMargin: -80
                anchors.bottomMargin: -80
                width: 220; height: 220
                radius: 110
                color: theme.alpha(theme.violet, theme.dark ? 0.10 : 0.06)
            }
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 12

            Rectangle {
                id: headerPanel
                Layout.fillWidth: true
                Layout.preferredHeight: 90
                radius: 16
                color: theme.elevatedSurface
                border.color: theme.border
                border.width: 1

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    cursorShape: Qt.SizeAllCursor
                    onPressed: root.startSystemMove()
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Label {
                            text: "DEVTOOL HUD"
                            color: theme.mutedText
                            font.pointSize: theme.captionPointSize
                            font.weight: Font.Bold
                            font.capitalization: Font.AllUppercase
                            font.letterSpacing: 1.25
                        }
                        Label {
                            Layout.fillWidth: true
                            text: runTitle()
                            color: theme.windowText
                            font.pointSize: theme.pageTitlePointSize - 1
                            font.weight: Font.DemiBold
                            lineHeight: theme.titleLineHeight
                            elide: Text.ElideRight
                        }
                        Label {
                            Layout.fillWidth: true
                            text: runContext()
                            color: theme.subtleText
                            font.pointSize: theme.smallPointSize
                            elide: Text.ElideRight
                        }
                    }

                    ColumnLayout {
                        spacing: 4
                        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                        Rectangle {
                            radius: 999
                            color: theme.alpha(runStatusColor(), theme.dark ? 0.20 : 0.14)
                            border.color: theme.alpha(runStatusColor(), theme.dark ? 0.50 : 0.35)
                            implicitHeight: 26
                            implicitWidth: statusText.implicitWidth + 20
                            Label {
                                id: statusText
                                anchors.centerIn: parent
                                text: runStatus().toUpperCase()
                                color: runStatusColor()
                                font.pointSize: theme.smallPointSize
                                font.weight: Font.Bold
                                font.capitalization: Font.AllUppercase
                                font.letterSpacing: 0.9
                            }
                        }
                        Label {
                            Layout.alignment: Qt.AlignRight
                            text: run.updated_at || "live"
                            color: theme.subtleText
                            font.pointSize: theme.captionPointSize
                        }
                    }

                    AppButton {
                        text: "×"
                        variant: "ghost"
                        compact: true
                        implicitWidth: 34
                        onClicked: Qt.quit()
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 150
                radius: 16
                color: theme.surface
                border.color: theme.border
                border.width: 1

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8

                    Label {
                        text: "OPERATIONS"
                        color: theme.mutedText
                        font.pointSize: theme.captionPointSize
                        font.weight: Font.Bold
                        font.capitalization: Font.AllUppercase
                        font.letterSpacing: 1.15
                    }
                    PipelineView {
                        Layout.fillWidth: true
                        steps: run.steps || []
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 12

                Rectangle {
                    Layout.preferredWidth: 170
                    Layout.fillHeight: true
                    radius: 16
                    color: theme.elevatedSurface
                    border.color: theme.border
                    border.width: 1

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10

                        Label {
                            text: "HEALTH"
                            color: theme.mutedText
                            font.pointSize: theme.captionPointSize
                            font.weight: Font.Bold
                            font.capitalization: Font.AllUppercase
                            font.letterSpacing: 1.05
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 70
                            radius: 12
                            color: theme.successSurface
                            border.color: theme.alpha(theme.success, theme.dark ? 0.42 : 0.28)
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 0
                                Label {
                                    text: String(run.errors || 0)
                                    color: theme.error
                                    font.pointSize: theme.metricValuePointSize + 2
                                    font.weight: Font.DemiBold
                                    lineHeight: theme.titleLineHeight
                                }
                                Label {
                                    text: "ERRORS"
                                    color: theme.mutedText
                                    font.pointSize: theme.captionPointSize
                                    font.weight: Font.DemiBold
                                    font.capitalization: Font.AllUppercase
                                    font.letterSpacing: 0.95
                                }
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 70
                            radius: 12
                            color: theme.warningSurface
                            border.color: theme.alpha(theme.warning, theme.dark ? 0.42 : 0.28)
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 0
                                Label {
                                    text: String(run.warnings || 0)
                                    color: theme.warning
                                    font.pointSize: theme.metricValuePointSize + 2
                                    font.weight: Font.DemiBold
                                    lineHeight: theme.titleLineHeight
                                }
                                Label {
                                    text: "WARNINGS"
                                    color: theme.mutedText
                                    font.pointSize: theme.captionPointSize
                                    font.weight: Font.DemiBold
                                    font.capitalization: Font.AllUppercase
                                    font.letterSpacing: 0.95
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 16
                    color: theme.elevatedSurface
                    border.color: theme.border
                    border.width: 1

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10

                        Label {
                            text: "LATEST NOTE"
                            color: theme.mutedText
                            font.pointSize: theme.captionPointSize
                            font.weight: Font.Bold
                            font.capitalization: Font.AllUppercase
                            font.letterSpacing: 1.05
                        }

                        Label {
                            Layout.fillWidth: true
                            text: runDetail()
                            color: theme.text
                            wrapMode: Text.Wrap
                            maximumLineCount: 4
                            elide: Text.ElideRight
                            font.pointSize: theme.bodyPointSize + 1
                            lineHeight: theme.bodyLineHeight
                        }

                        Label {
                            Layout.fillWidth: true
                            text: hudStore.focusedSummary(run)
                            color: theme.subtleText
                            wrapMode: Text.Wrap
                            maximumLineCount: 3
                            elide: Text.ElideRight
                            font.pointSize: theme.smallPointSize
                            lineHeight: theme.bodyLineHeight
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            AppButton { text: "Copy summary"; variant: "ghost"; compact: true; onClicked: appController.copyText(hudStore.focusedSummary(run)) }
                            AppButton { text: "Open log"; variant: "ghost"; compact: true; enabled: !!run.log_path; onClicked: appController.openPath(run.log_path) }
                            AppButton { text: detailsExpanded ? "Collapse" : "Expand"; variant: "ghost"; compact: true; onClicked: detailsExpanded = !detailsExpanded }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }
            }

            Rectangle {
                visible: detailsExpanded
                Layout.fillWidth: true
                Layout.preferredHeight: 118
                radius: 16
                color: theme.elevatedSurface
                border.color: theme.border
                border.width: 1

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8

                    Label {
                        text: "RECENT"
                        color: theme.mutedText
                        font.pointSize: theme.captionPointSize
                        font.weight: Font.Bold
                        font.capitalization: Font.AllUppercase
                        font.letterSpacing: 1.05
                    }

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        background: Rectangle { color: "transparent" }
                        clip: true

                        Label {
                            width: root.width - 72
                            text: hudStore.focusedSummary(run)
                            color: theme.windowText
                            wrapMode: Text.Wrap
                            font.family: "monospace"
                            font.pointSize: theme.smallPointSize
                            lineHeight: theme.bodyLineHeight
                        }
                    }
                }
            }
        }

        MouseArea {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            width: 20
            height: 20
            cursorShape: Qt.SizeFDiagCursor
            acceptedButtons: Qt.LeftButton
            onPressed: root.startSystemResize(Qt.RightEdge | Qt.BottomEdge)
        }
        MouseArea {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 10
            cursorShape: Qt.SizeHorCursor
            acceptedButtons: Qt.LeftButton
            onPressed: root.startSystemResize(Qt.LeftEdge)
        }
        MouseArea {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 10
            cursorShape: Qt.SizeHorCursor
            acceptedButtons: Qt.LeftButton
            onPressed: root.startSystemResize(Qt.RightEdge)
        }
        MouseArea {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 10
            cursorShape: Qt.SizeVerCursor
            acceptedButtons: Qt.LeftButton
            onPressed: root.startSystemResize(Qt.BottomEdge)
        }
    }
}
