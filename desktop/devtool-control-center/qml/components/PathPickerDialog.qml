import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import Qt.labs.folderlistmodel 2.1
import Devtool.ControlCenter.Components 1.0

Popup {
    id: root
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent

    property bool selectFolder: false
    property string initialPath: ""
    property string selectedPath: ""
    property var nameFilters: []
    property string title: selectFolder ? "Choose folder" : "Choose file"

    signal accepted(string path)

    width: Math.min(860, Math.max(560, (Overlay.overlay ? Overlay.overlay.width : 920) - 48))
    height: Math.min(640, Math.max(480, (Overlay.overlay ? Overlay.overlay.height : 720) - 48))
    anchors.centerIn: Overlay.overlay

    Theme { id: theme }

    function stripFileUrl(path) {
        var value = String(path || "").trim()
        if (value.indexOf("file://") === 0) {
            value = value.replace(/^file:\/\/+/, "/")
        }
        return value
    }

    function toFileUrl(path) {
        var value = stripFileUrl(path)
        if (!value) return "file:///"
        return value.indexOf("/") === 0 ? "file://" + value : value
    }

    function parentPath(path) {
        var value = stripFileUrl(path).replace(/\/+$/, "")
        if (!value) return "/"
        var slash = value.lastIndexOf("/")
        if (slash <= 0) return "/"
        return value.substring(0, slash)
    }

    function startFolder() {
        var value = stripFileUrl(initialPath)
        if (!value) return "/"
        return selectFolder ? value : parentPath(value)
    }

    function refreshSelection() {
        if (selectFolder) {
            selectedPath = stripFileUrl(folderModel.folder)
        } else if (selectedPath === "") {
            selectedPath = stripFileUrl(initialPath)
        }
    }

    function openAt(path) {
        var next = stripFileUrl(path)
        if (!next) next = "/"
        folderModel.folder = toFileUrl(next)
        searchField.text = next
        if (selectFolder) selectedPath = next
    }

    function navigateTo(path) {
        var next = stripFileUrl(path)
        if (!next) return
        openAt(next)
    }

    function acceptCurrent() {
        if (selectFolder) {
            accepted(stripFileUrl(folderModel.folder))
        } else {
            accepted(stripFileUrl(selectedPath))
        }
        close()
    }

    FolderListModel {
        id: folderModel
        folder: root.toFileUrl(root.startFolder())
        showDirs: true
        showFiles: !root.selectFolder
        showDirsFirst: true
        showDotAndDotDot: false
        showHidden: false
        nameFilters: root.nameFilters
        sortField: FolderListModel.Name
    }

    onOpened: {
        openAt(startFolder())
        if (!selectFolder) selectedPath = stripFileUrl(initialPath)
        searchField.forceActiveFocus()
    }

    contentItem: ColumnLayout {
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            color: theme.elevatedSurface
            border.color: theme.border
            border.width: 1
            implicitHeight: 56

            RowLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 10
                Label {
                    text: root.title
                    color: theme.windowText
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.DemiBold
                }
                Item { Layout.fillWidth: true }
                AppButton {
                    text: "Up"
                    variant: "ghost"
                    compact: true
                    enabled: folderModel.parentFolder.toString() !== ""
                    onClicked: openAt(parentPath(stripFileUrl(folderModel.folder)))
                }
                AppButton {
                    text: "Home"
                    variant: "ghost"
                    compact: true
                    onClicked: openAt(initialPath ? startFolder() : "/")
                }
                AppButton {
                    text: ""
                    iconSource: "qrc:/qt/qml/Devtool/ControlCenter/icons/close.svg"
                    variant: "ghost"
                    compact: true
                    implicitWidth: 40
                    onClicked: root.close()
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 12
            spacing: 10

            AppTextField {
                id: searchField
                Layout.fillWidth: true
                text: stripFileUrl(folderModel.folder)
                placeholderText: "Path"
                accessibleName: "Current folder path"
                onAccepted: navigateTo(text)
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: theme.cardRadius
                color: theme.surface
                border.color: theme.border
                border.width: 1
                clip: true

                ListView {
                    id: listView
                    anchors.fill: parent
                    anchors.margins: 6
                    clip: true
                    model: folderModel
                    currentIndex: -1
                    spacing: 2
                    highlightFollowsCurrentItem: false
                    delegate: ItemDelegate {
                        width: ListView.view.width
                        text: (isFolder ? "[dir] " : "") + fileName
                        highlighted: ListView.isCurrentItem
                        onClicked: {
                            listView.currentIndex = index
                            if (isFolder) {
                                if (!root.selectFolder) navigateTo(filePath)
                                else selectedPath = filePath
                            } else {
                                selectedPath = filePath
                            }
                        }
                        onDoubleClicked: {
                            if (isFolder && !root.selectFolder) {
                                navigateTo(filePath)
                            } else if (!isFolder) {
                                selectedPath = filePath
                                acceptCurrent()
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    Layout.fillWidth: true
                    text: selectFolder ? "Choose the current folder or navigate deeper." : "Choose a file, or navigate into folders first."
                    color: theme.subtleText
                    font.pointSize: theme.captionPointSize
                }
                AppButton {
                    text: "Cancel"
                    variant: "ghost"
                    onClicked: root.close()
                }
                AppButton {
                    text: selectFolder ? "Choose folder" : "Open"
                    variant: "primary"
                    enabled: selectFolder || selectedPath !== ""
                    onClicked: acceptCurrent()
                }
            }
        }
    }

    onVisibleChanged: if (visible) refreshSelection()
}
