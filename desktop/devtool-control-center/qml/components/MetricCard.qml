import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property string label: ""
    property int value: 0
    property color accent: theme.info
    Theme { id: theme }
    radius: theme.cardRadius
    color: theme.elevatedSurface
    border.color: theme.border
    implicitHeight: 74
    implicitWidth: 132
    Column {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 4
        Label {
            text: root.value
            color: root.accent
            font.pointSize: 19
            font.weight: Font.DemiBold
        }
        Label {
            text: root.label
            color: theme.mutedText
            font.pointSize: theme.captionPointSize
            font.weight: Font.DemiBold
            font.letterSpacing: 0.8
        }
    }
}
