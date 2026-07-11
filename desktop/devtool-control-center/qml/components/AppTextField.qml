import QtQuick
import QtQuick.Controls

TextField {
    id: root
    Theme { id: theme }
    implicitHeight: theme.controlHeight
    leftPadding: 12
    rightPadding: 12
    color: theme.text
    placeholderTextColor: theme.subtleText
    selectionColor: theme.accent
    selectedTextColor: theme.accentText
    font.pointSize: theme.bodyPointSize
    background: Rectangle {
        radius: theme.controlRadius
        color: root.enabled ? theme.recessedSurface : theme.surface
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? theme.accent : (root.hovered ? theme.strongBorder : theme.border)
        opacity: root.enabled ? 1 : 0.66
    }
}
