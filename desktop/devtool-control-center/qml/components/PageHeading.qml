import QtQuick 2.15
import QtQuick.Controls 2.15

Label {
    Theme { id: theme }
    color: theme.windowText
    font.pointSize: theme.pageTitlePointSize
    font.weight: Font.DemiBold
    font.letterSpacing: 0
    lineHeight: theme.titleLineHeight
    Accessible.role: Accessible.Heading
    Accessible.name: text
}
