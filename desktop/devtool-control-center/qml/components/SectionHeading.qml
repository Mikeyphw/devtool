import QtQuick 2.15
import QtQuick.Controls 2.15

Label {
    Theme { id: theme }
    color: theme.mutedText
    font.pointSize: theme.labelPointSize
    font.weight: Font.DemiBold
    font.letterSpacing: 0
    lineHeight: theme.compactLineHeight
    Accessible.role: Accessible.Heading
    Accessible.name: text
}
