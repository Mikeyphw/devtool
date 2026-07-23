import QtQuick 2.15
import QtQuick.Controls 2.15

Button {
    id: root
    property url iconSource

    function resolvedIconSource() {
        const value = String(root.iconSource || "")
        if (!value)
            return ""
        const slash = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"))
        const basename = slash >= 0 ? value.substring(slash + 1) : value
        return Qt.resolvedUrl("../icons/" + basename)
    }
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
    readonly property int selectedRailWidth: 3
    Accessible.role: Accessible.Button
    Accessible.name: text + (selected ? ", current page" : "")
    Accessible.description: accessibleDescription
    ToolTip.visible: compact && hovered
    ToolTip.text: text
    ToolTip.delay: 350
    contentItem: Row {
        spacing: root.compact ? 0 : 12
        Image {
            source: root.resolvedIconSource()
            width: theme.navigationIconSize
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
        Rectangle {
            id: selectedRail
            visible: root.selected
            width: root.selectedRailWidth
            radius: width
            color: theme.accent
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 7
            anchors.bottomMargin: 7
            Accessible.ignored: true
        }
    }
}
