import QtQuick
import QtQuick.Controls

Label {
    Theme { id: theme }
    color: theme.windowText
    font.pointSize: theme.pageTitlePointSize
    font.weight: Font.DemiBold
}
