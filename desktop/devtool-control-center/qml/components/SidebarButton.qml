import QtQuick
import QtQuick.Controls

Button {
    id: root
    property url iconSource
    property bool selected: false
    property bool compact: false
    property string accessibleDescription: "Open the " + text + " page"
    Theme { id: theme }
    flat: true
    hoverEnabled: true
    activeFocusOnTab: true
    implicitHeight: Math.max(48, theme.controlHeight + 10)
    leftPadding: compact ? 8 : 12
    rightPadding: compact ? 8 : 12
    Accessible.role: Accessible.Button
    Accessible.name: text + (selected ? ", current page" : "")
    Accessible.description: accessibleDescription
    ToolTip.visible: compact && hovered
    ToolTip.text: text
    ToolTip.delay: 350
    contentItem: Row {
        spacing: root.compact ? 0 : 12
        Image {
            source: root.iconSource
            width: root.compact ? root.availableWidth : theme.navigationIconSize
            height: parent.height
            sourceSize.width: theme.navigationIconSize
            sourceSize.height: theme.navigationIconSize
            fillMode: Image.PreserveAspectFit
            opacity: root.selected ? 1.0 : 0.78
            Accessible.ignored: true
        }
        Label {
            visible: !root.compact
            text: root.text
            color: root.selected ? theme.windowText : theme.mutedText
            font.pointSize: theme.bodyPointSize
            font.weight: root.selected ? Font.DemiBold : Font.Normal
            height: parent.height
            verticalAlignment: Text.AlignVCenter
            lineHeight: theme.compactLineHeight
        }
    }
    background: Rectangle {
        radius: theme.controlRadius
        color: root.selected ? theme.selectedSurface : root.hovered ? theme.hoverSurface : "transparent"
        border.width: root.activeFocus ? 2 : 0
        border.color: theme.accent
    }
}
