import QtQuick 2.15
import QtQuick.Controls 2.15

TextField {
    id: root
    property string accessibleName: placeholderText.length ? placeholderText : "Text field"
    property string accessibleDescription: ""
    property string toolTipText: accessibleDescription
    Theme { id: theme }
    activeFocusOnTab: true
    hoverEnabled: true
    implicitHeight: Math.max(theme.controlHeight, 40)
    leftPadding: 12
    rightPadding: 12
    color: theme.text
    placeholderTextColor: theme.subtleText
    selectionColor: theme.accent
    selectedTextColor: theme.accentText
    font.pointSize: theme.bodyPointSize

    Accessible.role: Accessible.EditableText
    Accessible.name: accessibleName
    Accessible.description: accessibleDescription
    ToolTip.visible: hovered && toolTipText.length > 0
    ToolTip.text: toolTipText
    ToolTip.delay: 500

    background: Rectangle {
        radius: theme.controlRadius
        color: root.enabled ? theme.recessedSurface : theme.surface
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? theme.accent : (root.hovered ? theme.strongBorder : theme.border)
        opacity: root.enabled ? 1 : 0.66
    }
}
