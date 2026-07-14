import QtQuick
import QtQuick.Controls

Label {
    Theme { id: theme }
    color: theme.windowText
    font.pointSize: theme.pageTitlePointSize
    font.weight: Font.DemiBold
    font.letterSpacing: -0.25
    lineHeight: theme.titleLineHeight
    Accessible.role: Accessible.Heading
    Accessible.name: text
}
