import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: root
    property var steps: []
    implicitHeight: Math.max(76, pipelineFlow.implicitHeight)
    clip: false
    Accessible.role: Accessible.Grouping
    Accessible.name: "Run pipeline"
    Accessible.description: (root.steps || []).length + " workflow steps"
    function progressText(step, includeUnit) {
        const completed = Number((step || {}).completed)
        const total = Number((step || {}).total)
        const percent = Math.round(Math.max(0, Math.min(100, Number((step || {}).progress || 0))))
        if (completed >= 0 && total > 0) {
            const unit = String((step || {}).unitLabel || "tasks")
            return Math.round(completed) + "/" + Math.round(total) + (includeUnit ? " " + unit : "") + " · " + percent + "%"
        }
        return percent + "%"
    }
    function columnCount() {
        const count = Math.max(1, (root.steps || []).length)
        if (root.width < 440) return 1
        if (root.width < 660) return Math.min(2, count)
        if (root.width < 900) return Math.min(3, count)
        return Math.min(5, count)
    }
    function stepWidth() {
        const columns = root.columnCount()
        return Math.max(132, Math.floor((root.width - ((columns - 1) * pipelineFlow.spacing)) / columns))
    }
    Theme { id: theme }

    Flow {
        id: pipelineFlow
        width: root.width
        spacing: 8
        Repeater {
            model: root.steps || []
            delegate: Rectangle {
                id: stepRect
                required property var modelData
                width: root.stepWidth()
                height: 72
                radius: 10
                color: modelData.status === "running" ? theme.infoSurface : theme.elevatedSurface
                border.width: 1
                border.color: modelData.status === "failed" ? theme.alpha(theme.error, 0.72)
                            : modelData.status === "ok" ? theme.alpha(theme.success, 0.52)
                            : modelData.status === "running" ? theme.alpha(theme.info, 0.72)
                            : theme.border
                Accessible.role: Accessible.ProgressBar
                Accessible.name: String(modelData.label || "Workflow step")
                Accessible.description: {
                    const detail = String(modelData.detail || modelData.status || "pending")
                    return modelData.status === "running"
                            ? detail + ", " + root.progressText(modelData, true)
                            : detail
                }
                property color stepColor: modelData.status === "failed" ? theme.error
                                          : modelData.status === "ok" ? theme.success
                                          : modelData.status === "running" ? theme.info
                                          : modelData.status === "skipped" ? theme.subtleText
                                          : theme.strongBorder

                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: 3
                    radius: 1.5
                    color: stepRect.stepColor
                    opacity: modelData.status === "pending" ? 0.25 : 1
                }

                Column {
                    anchors.fill: parent
                    anchors.leftMargin: 11
                    anchors.rightMargin: 9
                    anchors.topMargin: 8
                    anchors.bottomMargin: 8
                    spacing: 3

                    Row {
                        width: parent.width
                        spacing: 6
                        Label {
                            width: parent.width - statusLabel.implicitWidth - 8
                            text: modelData.label
                            color: theme.text
                            font.pointSize: theme.smallPointSize
                            font.weight: modelData.status === "running" ? Font.DemiBold : Font.Medium
                            elide: Text.ElideRight
                        }
                        Row {
                            id: statusLabel
                            spacing: 4
                            StatusMark {
                                kind: modelData.status
                                markColor: stepRect.stepColor
                                markSize: 12
                            }
                            Label {
                                visible: modelData.status === "running"
                                text: root.progressText(modelData, false)
                                color: stepRect.stepColor
                                font.weight: Font.DemiBold
                                font.pointSize: theme.smallPointSize
                            }
                        }
                    }

                    Label {
                        width: parent.width
                        text: modelData.detail || (modelData.status === "ok" ? "Completed" : modelData.status === "pending" ? "Waiting" : "In progress")
                        color: theme.subtleText
                        font.pointSize: theme.captionPointSize
                        elide: Text.ElideRight
                    }

                    Rectangle {
                        width: parent.width
                        height: 8
                        radius: 4
                        color: theme.recessedSurface
                        Rectangle {
                            height: parent.height
                            radius: 4
                            color: stepRect.stepColor
                            width: parent.width * Math.max(0, Math.min(100, Number(modelData.progress || 0))) / 100
                            Behavior on width { NumberAnimation { duration: 90; easing.type: Easing.OutCubic } }
                        }
                    }
                }
            }
        }
    }
}
