import QtQuick
import QtQuick.Controls

ScrollView {
    id: root
    Theme { id: theme }
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: contentHeight > availableHeight ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
    background: Rectangle { color: theme.window }
}
