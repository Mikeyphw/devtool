import QtQuick
import QtQuick.Controls

Label {
    Theme { id: theme }
    color: theme.mutedText
    font.pointSize: theme.smallPointSize
    font.weight: Font.DemiBold
    font.capitalization: Font.AllUppercase
    font.letterSpacing: 0.85
    lineHeight: theme.compactLineHeight
    lineHeightMode: Text.ProportionalHeight
}
