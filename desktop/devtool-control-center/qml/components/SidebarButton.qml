import QtQuick
import QtQuick.Controls

Button {
    id: root
    property string iconText: ""
    property bool selected: false
    Theme { id: theme }
    flat: true
    hoverEnabled: true
    implicitHeight: 48
    leftPadding: 12
    rightPadding: 12
    contentItem: Row {
        spacing: 12
        Label {
            text: root.iconText
            color: root.selected ? theme.accent : theme.mutedText
            font.pointSize: 14
            width: 22
            height: parent.height
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        Label {
            text: root.text
            color: root.selected ? theme.windowText : theme.mutedText
            font.pointSize: theme.bodyPointSize
            font.weight: root.selected ? Font.DemiBold : Font.Normal
            height: parent.height
            verticalAlignment: Text.AlignVCenter
        }
    }
    background: Rectangle {
        radius: theme.controlRadius
        color: root.selected ? theme.selectedSurface : root.hovered ? theme.hoverSurface : "transparent"
        border.width: root.activeFocus ? 2 : 0
        border.color: theme.accent
    }
}
