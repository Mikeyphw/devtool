import QtQuick

Rectangle {
    Theme { id: theme }
    radius: theme.cardRadius
    color: theme.surface
    border.color: theme.border
    border.width: 1
}
