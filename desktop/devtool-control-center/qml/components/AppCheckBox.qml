import QtQuick 2.15
import QtQuick.Controls 2.15

CheckBox {
    id: root
    property string accessibleName: text
    property string accessibleDescription: ""
    property string toolTipText: accessibleDescription
    Theme { id: theme }
    hoverEnabled: true
    activeFocusOnTab: true
    spacing: 9
    implicitHeight: Math.max(theme.controlHeight, 40)
    font.pointSize: theme.bodyPointSize

    Accessible.role: Accessible.CheckBox
    Accessible.name: accessibleName
    Accessible.description: accessibleDescription
    ToolTip.visible: hovered && toolTipText.length > 0
    ToolTip.text: toolTipText
    ToolTip.delay: 500

    indicator: Rectangle {
        implicitWidth: 19
        implicitHeight: 19
        x: root.leftPadding
        y: (root.height - height) / 2
        radius: 5
        color: root.checked ? theme.accent : (root.hovered ? theme.hoverSurface : theme.recessedSurface)
        border.width: root.activeFocus ? 2 : 1
        border.color: root.checked ? theme.accent : root.activeFocus ? theme.accent : theme.strongBorder
        Canvas {
            anchors.fill: parent
            anchors.margins: 4
            visible: root.checked
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.strokeStyle = theme.accentText
                ctx.lineWidth = 2
                ctx.lineCap = "round"
                ctx.lineJoin = "round"
                ctx.beginPath()
                ctx.moveTo(width * 0.12, height * 0.52)
                ctx.lineTo(width * 0.40, height * 0.80)
                ctx.lineTo(width * 0.88, height * 0.20)
                ctx.stroke()
            }
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
