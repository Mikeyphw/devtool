import QtQuick
import QtQuick.Controls

Item {
    id: root
    property var steps: []
    implicitHeight: 76
    clip: true
    Accessible.role: Accessible.Grouping
    Accessible.name: "Run pipeline"
    Accessible.description: (root.steps || []).length + " workflow steps"
    Theme { id: theme }

    ListView {
        id: timeline
        anchors.fill: parent
        orientation: ListView.Horizontal
        spacing: 8
        model: root.steps || []
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentWidth > width
        reuseItems: true
        ScrollBar.horizontal: ScrollBar {
            policy: timeline.contentWidth > timeline.width ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        }
        WheelHandler {
            target: timeline
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            orientation: Qt.Vertical
            onWheel: function(event) {
                if (timeline.contentWidth <= timeline.width) return
                const delta = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y
                if (!delta) return
                timeline.contentX = Math.max(0, Math.min(timeline.contentWidth - timeline.width, timeline.contentX - delta))
                event.accepted = true
            }
        }

        delegate: Rectangle {
            id: stepRect
            required property var modelData
            width: Math.max(132, Math.min(170, Math.floor((timeline.width - (Math.min(4, Math.max(0, timeline.count - 1)) * 8)) / Math.max(1, Math.min(5, timeline.count)))))
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
                const progress = Math.max(0, Math.min(100, Number(modelData.progress || 0)))
                return modelData.status === "running"
                        ? detail + ", " + Math.round(progress) + " percent"
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
                            text: Math.round(modelData.progress) + "%"
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
