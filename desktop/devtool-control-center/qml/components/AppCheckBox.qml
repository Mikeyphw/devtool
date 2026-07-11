import QtQuick
import QtQuick.Controls

CheckBox {
    id: root
    Theme { id: theme }
    hoverEnabled: true
    spacing: 9
    implicitHeight: theme.controlHeight
    font.pointSize: theme.bodyPointSize

    indicator: Rectangle {
        implicitWidth: 19
        implicitHeight: 19
        x: root.leftPadding
        y: (root.height - height) / 2
        radius: 5
        color: root.checked ? theme.accent : (root.hovered ? theme.hoverSurface : theme.recessedSurface)
        border.width: root.activeFocus ? 2 : 1
        border.color: root.checked ? theme.accent : root.activeFocus ? theme.accent : theme.strongBorder
        Label {
            anchors.centerIn: parent
            text: "✓"
            visible: root.checked
            color: theme.accentText
            font.bold: true
            font.pointSize: theme.smallPointSize
        }
    }

    contentItem: Label {
        leftPadding: root.indicator.width + root.spacing
        text: root.text
        color: root.enabled ? theme.text : theme.disabledText
        font: root.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
