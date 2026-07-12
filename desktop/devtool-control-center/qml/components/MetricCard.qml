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
    implicitHeight: 82
    implicitWidth: 132
    Column {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 6
        Label {
            text: root.value
            color: root.accent
            font.pointSize: theme.metricValuePointSize
            font.weight: Font.DemiBold
            font.letterSpacing: -0.15
            lineHeight: theme.compactLineHeight
            lineHeightMode: Text.ProportionalHeight
        }
        Label {
            text: root.label
            color: theme.mutedText
            font.pointSize: theme.captionPointSize
            font.weight: Font.DemiBold
            font.capitalization: Font.AllUppercase
            font.letterSpacing: 0.95
            lineHeight: theme.compactLineHeight
            lineHeightMode: Text.ProportionalHeight
        }
    }
}
