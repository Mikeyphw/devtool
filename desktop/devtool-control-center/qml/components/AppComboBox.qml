import QtQuick
import QtQuick.Controls

ComboBox {
    id: root
    property string accessibleName: "Selection"
    property string accessibleDescription: ""
    property string toolTipText: accessibleDescription
    Theme { id: theme }
    hoverEnabled: true
    activeFocusOnTab: true
    implicitHeight: Math.max(theme.controlHeight, 40)
    leftPadding: 12
    rightPadding: 36
    font.pointSize: theme.bodyPointSize

    Accessible.role: Accessible.ComboBox
    Accessible.name: accessibleName
    Accessible.description: accessibleDescription
    ToolTip.visible: hovered && toolTipText.length > 0
    ToolTip.text: toolTipText
    ToolTip.delay: 500

    contentItem: Label {
        leftPadding: 0
        rightPadding: 0
        text: root.displayText
        color: root.enabled ? theme.text : theme.disabledText
        font: root.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Canvas {
        width: 14
        height: 8
        x: root.width - width - 14
        y: (root.height - height) / 2
        Accessible.ignored: true
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = root.enabled ? theme.mutedText : theme.disabledText
            ctx.lineWidth = 1.8
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            ctx.beginPath()
            ctx.moveTo(1, 1)
            ctx.lineTo(width / 2, height - 1)
            ctx.lineTo(width - 1, 1)
            ctx.stroke()
        }
    }

    background: Rectangle {
        radius: theme.controlRadius
        color: root.down ? theme.pressedSurface : root.hovered ? theme.hoverSurface : theme.recessedSurface
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? theme.accent : theme.border
        opacity: root.enabled ? 1 : 0.66
    }

    delegate: ItemDelegate {
        id: delegateControl
        required property int index
        required property var modelData
        width: ListView.view ? ListView.view.width : root.width
        highlighted: root.highlightedIndex === index
        implicitHeight: 44
        activeFocusOnTab: true
        Accessible.role: Accessible.ListItem
        Accessible.name: optionLabel.text
        contentItem: Label {
            id: optionLabel
            text: root.textRole && delegateControl.modelData && delegateControl.modelData[root.textRole] !== undefined
                  ? delegateControl.modelData[root.textRole] : String(delegateControl.modelData)
            color: delegateControl.highlighted ? theme.accentText : theme.text
            font.pointSize: theme.bodyPointSize
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 5
            color: delegateControl.highlighted ? theme.accent : delegateControl.hovered ? theme.hoverSurface : "transparent"
        }
    }

    popup: Popup {
        y: root.height + 4
        width: root.width
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 320)
        padding: 4
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
            keyNavigationEnabled: true
            ScrollIndicator.vertical: ScrollIndicator { }
        }
        background: Rectangle {
            radius: theme.controlRadius
            color: theme.elevatedSurface
            border.color: theme.strongBorder
            border.width: 1
        }
    }
}
