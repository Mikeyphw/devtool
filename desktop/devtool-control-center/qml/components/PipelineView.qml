import QtQuick
import QtQuick.Controls

Flow {
    id: root
    property var steps: []
    spacing: 8
    Theme { id: theme }
    Repeater {
        model: root.steps || []
        delegate: Rectangle {
            id: stepRect
            width: Math.max(118, (root.width - 24) / 4)
            height: 66
            radius: theme.controlRadius
            color: theme.elevatedSurface
            border.color: theme.border
            property color stepColor: modelData.status === "failed" ? theme.error
                                      : modelData.status === "ok" ? theme.success
                                      : modelData.status === "running" ? theme.info
                                      : modelData.status === "skipped" ? theme.subtleText
                                      : theme.strongBorder
            Column {
                anchors.fill: parent
                anchors.margins: 9
                spacing: 8
                Row {
                    width: parent.width
                    Label {
                        text: modelData.label
                        color: theme.text
                        font.pointSize: theme.smallPointSize
                        font.weight: modelData.status === "running" ? Font.DemiBold : Font.Normal
                        elide: Text.ElideRight
                        width: parent.width - 36
                    }
                    Label {
                        text: modelData.status === "ok" ? "✓" : modelData.status === "failed" ? "!" : modelData.status === "running" ? modelData.progress + "%" : "·"
                        color: stepRect.stepColor
                        font.weight: Font.DemiBold
                        font.pointSize: theme.smallPointSize
                    }
                }
                Rectangle {
                    width: parent.width
                    height: 6
                    radius: 3
                    color: theme.recessedSurface
                    Rectangle {
                        height: parent.height
                        radius: 3
                        color: stepRect.stepColor
                        width: parent.width * Math.max(0, Math.min(100, modelData.progress)) / 100
                        Behavior on width { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    }
                }
            }
        }
    }
}
