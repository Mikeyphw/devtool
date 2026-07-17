import QtQuick 2.15
import QtQuick.Controls 2.15

TabButton {
    id: root
    property string accessibleName: text
    property string accessibleDescription: ""
    Theme { id: theme }
    activeFocusOnTab: true
    implicitHeight: 44
    Accessible.role: Accessible.PageTab
    Accessible.name: accessibleName
    Accessible.description: accessibleDescription
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
        border.width: root.activeFocus ? 2 : 0
        border.color: theme.accent
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
