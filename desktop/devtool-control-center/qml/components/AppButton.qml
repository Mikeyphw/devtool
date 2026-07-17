import QtQuick 2.15
import QtQuick.Controls 2.15

Button {
    id: root
    property string variant: "default"
    property bool compact: false
    property url iconSource

    function resolvedIconSource() {
        const value = String(root.iconSource || "")
        if (!value)
            return ""
        const slash = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"))
        const basename = slash >= 0 ? value.substring(slash + 1) : value
        return "qrc:/qt/qml/Devtool/ControlCenter/icons/" + basename
    }
    property bool destructive: variant === "danger"
    property string accessibleName: text
    property string accessibleDescription: ""
    property string toolTipText: accessibleDescription
    hoverEnabled: true
    activeFocusOnTab: true
    implicitHeight: Math.max(compact ? theme.compactControlHeight : theme.controlHeight, 40)
    implicitWidth: Math.max(compact ? 72 : 92, contentItem.implicitWidth + leftPadding + rightPadding)
    leftPadding: compact ? 10 : 14
    rightPadding: compact ? 10 : 14
    topPadding: 0
    bottomPadding: 0

    Accessible.role: Accessible.Button
    Accessible.name: accessibleName
    Accessible.description: accessibleDescription
    ToolTip.visible: hovered && toolTipText.length > 0
    ToolTip.text: toolTipText
    ToolTip.delay: 500

    Theme { id: theme }

    function foregroundColor() {
        if (!enabled) return theme.disabledText
        if (variant === "primary") return theme.accentText
        if (variant === "danger") return theme.dark ? "white" : theme.highlightedText
        if (variant === "ghost") return theme.windowText
        return theme.buttonText
    }

    function backgroundColor() {
        if (variant === "ghost") {
            if (down) return theme.pressedSurface
            if (hovered) return theme.hoverSurface
            return "transparent"
        }
        if (variant === "primary") {
            if (down) return theme.mix(theme.accent, theme.windowText, 0.20)
            if (hovered) return theme.mix(theme.accent, theme.windowText, 0.10)
            return theme.accent
        }
        if (variant === "danger") {
            if (down) return theme.mix(theme.error, theme.windowText, 0.20)
            if (hovered) return theme.mix(theme.error, theme.windowText, 0.10)
            return theme.error
        }
        if (down) return theme.pressedSurface
        if (hovered) return theme.hoverSurface
        return theme.elevatedSurface
    }

    contentItem: Row {
        spacing: root.iconSource && root.text ? 7 : 0
        anchors.horizontalCenter: parent.horizontalCenter
        Image {
            visible: !!root.iconSource
            source: root.resolvedIconSource()
            width: visible ? 18 : 0
            height: 18
            sourceSize.width: 18
            sourceSize.height: 18
            fillMode: Image.PreserveAspectFit
            opacity: root.enabled ? 1.0 : 0.55
            Accessible.ignored: true
        }
        Label {
            visible: root.text.length > 0
            text: root.text
            color: root.foregroundColor()
            font.pointSize: theme.bodyPointSize
            font.weight: root.variant === "primary" ? Font.DemiBold : Font.Medium
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }

    background: Rectangle {
        radius: theme.controlRadius
        color: root.backgroundColor()
        border.width: root.activeFocus ? 2 : (root.variant === "ghost" ? 0 : 1)
        border.color: root.activeFocus ? theme.accent : theme.border
        opacity: root.enabled ? 1 : 0.62
    }
}
