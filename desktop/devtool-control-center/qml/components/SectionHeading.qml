import QtQuick
import QtQuick.Controls

Label {
    Theme { id: theme }
    color: theme.mutedText
    font.pointSize: theme.smallPointSize
    font.weight: Font.DemiBold
    font.letterSpacing: 0.2
}
