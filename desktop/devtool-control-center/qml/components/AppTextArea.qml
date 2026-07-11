import QtQuick
import QtQuick.Controls

TextArea {
    id: root
    Theme { id: theme }
    leftPadding: 12
    rightPadding: 12
    topPadding: 10
    bottomPadding: 10
    color: theme.text
    placeholderTextColor: theme.subtleText
    selectionColor: theme.accent
    selectedTextColor: theme.accentText
    font.pointSize: theme.bodyPointSize
    background: Rectangle {
        radius: theme.controlRadius
        color: theme.recessedSurface
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? theme.accent : theme.border
    }
}
