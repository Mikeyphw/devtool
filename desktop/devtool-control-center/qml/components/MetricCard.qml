import QtQuick 2.15
import QtQuick.Controls 2.15

Rectangle {
    id: root
    property string label: ""
    property int value: 0
    property color accent: theme.info
    Theme { id: theme }
    radius: theme.cardRadius
    color: theme.elevatedSurface
    border.color: theme.border
    implicitHeight: 82
    implicitWidth: 132
    Accessible.role: Accessible.StaticText
    Accessible.name: root.label + ", " + root.value
    Column {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 6
        Label {
            text: root.value
            color: root.accent
            font.pointSize: theme.metricValuePointSize
            font.weight: Font.DemiBold
            font.letterSpacing: 0
            lineHeight: theme.compactLineHeight
        }
        Label {
            text: root.label
            color: theme.mutedText
            font.pointSize: theme.labelPointSize
            font.weight: Font.DemiBold
            font.letterSpacing: 0
            lineHeight: theme.compactLineHeight
        }
    }
}
