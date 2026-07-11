import QtQuick
import QtQuick.Controls

Button {
    id: root
    property string variant: "default"
    property bool compact: false
    property bool destructive: variant === "danger"
    hoverEnabled: true
    implicitHeight: compact ? theme.compactControlHeight : theme.controlHeight
    implicitWidth: Math.max(compact ? 72 : 92, contentItem.implicitWidth + leftPadding + rightPadding)
    leftPadding: compact ? 10 : 14
    rightPadding: compact ? 10 : 14
    topPadding: 0
    bottomPadding: 0

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

    contentItem: Label {
        text: root.text
        color: root.foregroundColor()
        font.pointSize: theme.bodyPointSize
        font.weight: root.variant === "primary" ? Font.DemiBold : Font.Medium
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: theme.controlRadius
        color: root.backgroundColor()
        border.width: root.activeFocus ? 2 : (root.variant === "ghost" ? 0 : 1)
        border.color: root.activeFocus ? theme.accent : theme.border
        opacity: root.enabled ? 1 : 0.62
    }
}
