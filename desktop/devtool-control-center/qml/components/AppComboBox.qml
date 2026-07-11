import QtQuick
import QtQuick.Controls

ComboBox {
    id: root
    Theme { id: theme }
    hoverEnabled: true
    implicitHeight: theme.controlHeight
    leftPadding: 12
    rightPadding: 36
    font.pointSize: theme.bodyPointSize

    contentItem: Label {
        leftPadding: 0
        rightPadding: 0
        text: root.displayText
        color: root.enabled ? theme.text : theme.disabledText
        font: root.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Label {
        x: root.width - width - 12
        y: (root.height - height) / 2 - 1
        text: "⌄"
        color: root.enabled ? theme.mutedText : theme.disabledText
        font.pointSize: theme.cardTitlePointSize
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
        implicitHeight: 36
        contentItem: Label {
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
