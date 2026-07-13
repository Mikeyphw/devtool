import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"

Item {
    id: root
    Theme { id: theme }
    Component.onCompleted: repositoryModel.refresh()
    FolderDialog { id: folderDialog; onAccepted: repositoryModel.addRepository(selectedFolder.toString().replace("file://", "")) }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.pageMargin
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            PageHeading { text: "Repositories" }
            Item { Layout.fillWidth: true }
            AppButton { text: "Refresh"; onClicked: repositoryModel.refresh() }
            AppButton { text: "Add repository…"; variant: "primary"; onClicked: folderDialog.open() }
        }
        Label {
            text: "Registered repositories are checked for branch, remote, working-tree state, and active devtool runs."
            color: theme.mutedText
            font.pointSize: theme.bodyPointSize
        }
        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 10
            model: repositoryModel
            delegate: Rectangle {
                width: ListView.view.width
                height: 126
                radius: theme.cardRadius
                color: theme.surface
                border.color: path === appController.repository ? theme.accent : theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 14
                    Rectangle {
                        Layout.preferredWidth: 7
                        Layout.fillHeight: true
                        radius: 4
                        color: status === "clean" ? theme.success : status === "modified" ? theme.warning : theme.error
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Label { text: name || path; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold }
                        Label { text: path; color: theme.mutedText; font.family: "monospace"; font.pointSize: theme.smallPointSize; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Label { text: (branch ? "Branch " + branch : "No branch") + (remote ? " · " + remote : ""); color: theme.subtleText; font.pointSize: theme.smallPointSize; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        RowLayout {
                            spacing: 8
                            Label { text: status.toUpperCase(); color: status === "clean" ? theme.success : status === "modified" ? theme.warning : theme.error; font.weight: Font.DemiBold; font.pointSize: theme.captionPointSize }
                            Label { visible: activeRuns > 0; text: activeRuns + " ACTIVE RUN" + (activeRuns === 1 ? "" : "S"); color: theme.info; font.weight: Font.DemiBold; font.pointSize: theme.captionPointSize }
                        }
                    }
                    ColumnLayout {
                        spacing: 6
                        AppButton { text: path === appController.repository ? "Selected" : "Use"; variant: path === appController.repository ? "default" : "primary"; compact: true; enabled: path !== appController.repository; onClicked: { appController.repository = path; repositoryModel.markUsed(path) } }
                        AppButton { text: "Open"; variant: "ghost"; compact: true; onClicked: appController.openPath(path) }
                        AppButton { text: "Remove"; variant: "ghost"; compact: true; onClicked: repositoryModel.removeRepository(index) }
                    }
                }
            }
        }
        Label { visible: repositoryModel.count === 0; text: "Add repositories you use with devtool."; color: theme.subtleText; font.pointSize: theme.bodyPointSize }
    }
}
