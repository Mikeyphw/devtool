import QtQuick
import QtQuick.Controls

TabButton {
    id: root
    Theme { id: theme }
    implicitHeight: 40
    contentItem: Label {
        text: root.text
        color: root.enabled ? (root.checked ? theme.accent : theme.mutedText) : theme.disabledText
        font.pointSize: theme.bodyPointSize
        font.weight: root.checked ? Font.DemiBold : Font.Normal
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        color: root.hovered ? theme.hoverSurface : "transparent"
        radius: theme.controlRadius
        Rectangle {
            visible: root.checked
            height: 3
            radius: 2
            color: theme.accent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 10
            anchors.rightMargin: 10
        }
    }
}
