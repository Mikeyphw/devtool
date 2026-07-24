import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import Devtool.ControlCenter.Components 1.0

Item {
    id: root
    readonly property bool narrowMode: width < 900
    Theme { id: theme }
    property int selectedRow: -1
    property var selectedRepo: ({})
    property var repoHistory: []
    property var repoRuns: []

    function selectRepository(row) {
        selectedRow = row
        selectedRepo = row >= 0 ? repositoryModel.get(row) : ({})
        const path = selectedRepo.path || ""
        repoHistory = path ? historyModel.forRepository(path) : []
        repoRuns = path ? (hudStore.activeRuns || []).filter(function(run) {
            return String(run.repo_root || "") === String(path)
                    && String(run.status || "").toLowerCase() === "running"
        }) : []
        gitRepository.repositoryPath = path
    }

    function refreshActivity() {
        if (selectedRow >= 0) selectRepository(selectedRow)
    }

    PathPickerDialog {
        id: repositoryDialog
        selectFolder: true
        initialPath: appController.repository || ""
        onAccepted: function(path) {
            if (!path) return
            repositoryModel.addRepository(path)
            for (let row = 0; row < repositoryModel.count; ++row) {
                if (String(repositoryModel.get(row).path || "") === String(path)) {
                    selectRepository(row)
                    return
                }
            }
        }
    }

    Component.onCompleted: {
        repositoryModel.refresh()
        if (repositoryModel.count > 0) selectRepository(0)
    }
    Connections { target: hudStore; function onActiveRunsChanged() { root.refreshActivity() } }
    Connections { target: historyModel; function onCountChanged() { root.refreshActivity() } }


    Popup {
        id: discardConfirmation
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        width: Math.min(480, Overlay.overlay.width - 40)
        x: Math.round((Overlay.overlay.width - width) / 2)
        y: Math.round((Overlay.overlay.height - implicitHeight) / 2)
        padding: 16
        background: Rectangle { color: theme.elevatedSurface; radius: theme.cardRadius; border.color: theme.error; border.width: 1 }
        contentItem: ColumnLayout {
            spacing: 12
            Label { text: "Discard every uncommitted change?"; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold }
            Label { Layout.fillWidth: true; text: "This runs git reset --hard HEAD. Untracked files are not deleted, but tracked changes cannot be recovered by devtool."; color: theme.warning; wrapMode: Text.Wrap }
            RowLayout {
                Item { Layout.fillWidth: true }
                AppButton { text: "Cancel"; variant: "ghost"; onClicked: discardConfirmation.close() }
                AppButton { text: "Discard changes"; variant: "danger"; onClicked: { discardConfirmation.close(); gitRepository.discardWorkingTree() } }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.pageMargin
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            PageHeading { text: "Repositories" }
            Item { Layout.fillWidth: true }
            AppButton { text: "Refresh"; onClicked: { repositoryModel.refresh(); gitRepository.refresh(); root.refreshActivity() } }
            AppButton { text: "Add repository…"; variant: "primary"; onClicked: repositoryDialog.open() }
        }

        Label {
            Layout.fillWidth: true
            text: "Repositories are opt-in. Using a path in Run does not save it here."
            color: theme.mutedText
            font.pointSize: theme.bodyPointSize
            wrapMode: Text.Wrap
        }

        SurfaceCard {
            visible: repositoryModel.count === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 260
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 560)
                spacing: 10
                Label {
                    Layout.fillWidth: true
                    text: "No saved repositories"
                    color: theme.windowText
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                }
                Label {
                    Layout.fillWidth: true
                    text: "Add your first repository, or keep using the current Run path without saving it here."
                    color: theme.mutedText
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                }
                Flow {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 8
                    AppButton { text: "Add repository…"; variant: "primary"; onClicked: repositoryDialog.open() }
                    AppButton { text: "Use current repo"; enabled: appController.repository !== ""; onClicked: { repositoryModel.addRepository(appController.repository); repositoryModel.refresh(); root.selectRepository(0) } }
                }
            }
        }

        GridLayout {
            visible: repositoryModel.count > 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: root.narrowMode ? 1 : 2
            columnSpacing: 12
            rowSpacing: 12

            SurfaceCard {
                Layout.fillWidth: root.narrowMode
                Layout.preferredWidth: root.narrowMode ? 0 : 310
                Layout.preferredHeight: root.narrowMode ? 220 : -1
                Layout.fillHeight: !root.narrowMode

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0
                    ListView {
                        id: repositoryList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: repositoryModel
                        spacing: 4
                        topMargin: 8
                        bottomMargin: 8
                        leftMargin: 8
                        rightMargin: 8
                        delegate: Rectangle {
                            width: repositoryList.width - 16
                            height: 86
                            radius: theme.controlRadius
                            color: index === root.selectedRow ? theme.selectedSurface : "transparent"
                            border.color: index === root.selectedRow ? theme.accent : "transparent"
                            border.width: 1
                            activeFocusOnTab: true
                            Accessible.role: Accessible.Button
                            Accessible.name: (name || path) + ", " + (branch || "No branch") + ", " + status
                            Accessible.description: "Select this repository"
                            Keys.onReturnPressed: root.selectRepository(index)
                            Keys.onEnterPressed: root.selectRepository(index)
                            MouseArea { anchors.fill: parent; onClicked: root.selectRepository(index) }
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 9
                                Rectangle {
                                    width: 6; Layout.fillHeight: true; radius: 3
                                    color: status === "clean" ? theme.success : status === "modified" ? theme.warning : theme.error
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Label { Layout.fillWidth: true; text: name || path; color: theme.windowText; font.weight: Font.DemiBold; elide: Text.ElideRight }
                                    Label { Layout.fillWidth: true; text: path; color: theme.mutedText; font.family: theme.monospaceFamily; font.pointSize: theme.captionPointSize; elide: Text.ElideMiddle }
                                    RowLayout {
                                        Label { text: branch || "No branch"; color: theme.subtleText; font.pointSize: theme.captionPointSize }
                                        Item { Layout.fillWidth: true }
                                        Label { visible: saved === false; text: "LIVE · NOT SAVED"; color: theme.warning; font.weight: Font.DemiBold; font.pointSize: theme.captionPointSize }
                                        Label { visible: activeRuns > 0; text: activeRuns + " live"; color: theme.info; font.weight: Font.DemiBold; font.pointSize: theme.captionPointSize }
                                    }
                                }
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        Layout.margins: 16
                        visible: repositoryModel.count === 0
                        text: "No saved repositories. Add one explicitly to begin."
                        color: theme.subtleText
                        wrapMode: Text.Wrap
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.fillHeight: true

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0

                    GridLayout {
                        Layout.fillWidth: true
                        Layout.margins: 14
                        columns: root.narrowMode ? 1 : 4
                        columnSpacing: 10
                        rowSpacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.columnSpan: root.narrowMode ? 1 : 1
                            Label { text: root.selectedRepo.name || "Select a repository"; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold }
                            Label { Layout.fillWidth: true; text: root.selectedRepo.path || ""; color: theme.mutedText; font.family: theme.monospaceFamily; font.pointSize: theme.smallPointSize; elide: Text.ElideMiddle }
                        }
                        AppButton { text: "Use in Run"; enabled: !!root.selectedRepo.path; onClicked: appController.repository = root.selectedRepo.path }
                        AppButton { text: "Open"; variant: "ghost"; enabled: !!root.selectedRepo.path; onClicked: appController.openPath(root.selectedRepo.path) }
                        AppButton {
                            text: root.selectedRepo.saved === false ? "Save repository" : "Remove"
                            variant: root.selectedRepo.saved === false ? "primary" : "ghost"
                            enabled: root.selectedRow >= 0
                            onClicked: {
                                if (root.selectedRepo.saved === false) {
                                    repositoryModel.addRepository(root.selectedRepo.path)
                                    root.selectRepository(root.selectedRow)
                                } else {
                                    repositoryModel.removeRepository(root.selectedRow)
                                    root.selectRepository(Math.min(root.selectedRow, repositoryModel.count - 1))
                                }
                            }
                        }
                    }

                    TabBar {
                        id: detailTabs
                        visible: !!root.selectedRepo.path
                        Layout.fillWidth: true
                        background: Rectangle { color: theme.recessedSurface; border.color: theme.border; border.width: 1 }
                        AppTabButton { text: "Activity" }
                        AppTabButton { text: "Git" }
                    }

                    StackLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        currentIndex: detailTabs.currentIndex

                        Item {
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 10
                                SectionHeading { text: "Current runs" }
                                Repeater {
                                    model: root.repoRuns
                                    delegate: Rectangle {
                                        Layout.fillWidth: true
                                        implicitHeight: 64
                                        radius: theme.controlRadius
                                        color: theme.infoSurface
                                        border.color: theme.border
                                        RowLayout {
                                            anchors.fill: parent; anchors.margins: 10
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                Label { text: modelData.task_title || modelData.title || modelData.command || "Devtool run"; color: theme.windowText; font.weight: Font.DemiBold }
                                                Label { text: (modelData.target || "No target") + " · " + (modelData.message || modelData.status || "running"); color: theme.mutedText; font.pointSize: theme.smallPointSize }
                                            }
                                            Label { text: Math.round(Number(modelData.percent || 0)) + "%"; color: theme.info; font.weight: Font.Bold }
                                        }
                                    }
                                }
                                Label { visible: root.repoRuns.length === 0; text: "No active run for this repository."; color: theme.subtleText; font.pointSize: theme.smallPointSize }

                                SectionHeading { text: "Command and artifact history" }
                                ListView {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    clip: true
                                    spacing: 7
                                    model: root.repoHistory
                                    delegate: Rectangle {
                                        width: ListView.view.width
                                        height: 78
                                        radius: theme.controlRadius
                                        color: theme.recessedSurface
                                        border.color: theme.border
                                        RowLayout {
                                            anchors.fill: parent; anchors.margins: 10; spacing: 10
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                Label { Layout.fillWidth: true; text: modelData.title || modelData.command || "Devtool run"; color: theme.windowText; font.weight: Font.DemiBold; elide: Text.ElideRight }
                                                Label { Layout.fillWidth: true; text: modelData.command || ""; color: theme.mutedText; font.family: theme.monospaceFamily; font.pointSize: theme.captionPointSize; elide: Text.ElideMiddle }
                                                Label { text: (modelData.status || "unknown") + (modelData.git_commit ? " · " + String(modelData.git_commit).slice(0, 8) : ""); color: modelData.status === "ok" ? theme.success : theme.warning; font.pointSize: theme.captionPointSize }
                                            }
                                            AppButton { visible: !!modelData.artifact_path; text: "Artifact"; compact: true; onClicked: appController.openPath(modelData.artifact_path) }
                                        }
                                    }
                                }
                                Label { visible: root.repoHistory.length === 0; text: "No recorded devtool history for this repository."; color: theme.subtleText }
                            }
                        }

                        Item {
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 10

                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: gitRepository.branch || "No branch"; color: theme.windowText; font.weight: Font.DemiBold }
                                    Label { text: gitRepository.statusText; color: gitRepository.dirty ? theme.warning : theme.success }
                                    Label { Layout.fillWidth: true; text: gitRepository.remote; color: theme.mutedText; elide: Text.ElideMiddle }
                                    AppButton { text: "Refresh"; compact: true; enabled: !gitRepository.busy; onClicked: gitRepository.refresh() }
                                    AppButton { text: "Fetch"; compact: true; enabled: !gitRepository.busy; onClicked: gitRepository.fetch() }
                                    AppButton { text: "Pull"; compact: true; enabled: !gitRepository.busy; onClicked: gitRepository.pull() }
                                    AppButton { text: "Push"; compact: true; enabled: !gitRepository.busy; onClicked: gitRepository.push() }
                                }

                                Label { visible: gitRepository.error !== ""; Layout.fillWidth: true; text: gitRepository.error; color: theme.error; wrapMode: Text.Wrap }
                                Label { visible: gitRepository.output !== "" && gitRepository.error === ""; Layout.fillWidth: true; text: gitRepository.output; color: theme.subtleText; font.family: theme.monospaceFamily; font.pointSize: theme.captionPointSize; wrapMode: Text.WrapAnywhere; maximumLineCount: 3; elide: Text.ElideRight }

                                RowLayout {
                                    Layout.fillWidth: true
                                    AppComboBox { id: branchPicker; Layout.fillWidth: true; model: gitRepository.branches }
                                    AppButton { text: "Checkout"; enabled: !gitRepository.busy && branchPicker.currentText !== ""; onClicked: gitRepository.checkout(branchPicker.currentText) }
                                    AppTextField { id: newBranch; Layout.preferredWidth: 180; placeholderText: "New branch" }
                                    AppButton { text: "Create"; enabled: !gitRepository.busy && newBranch.text !== ""; onClicked: gitRepository.createBranch(newBranch.text) }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    AppButton { text: "Stage all"; enabled: !gitRepository.busy; onClicked: gitRepository.stageAll() }
                                    AppButton { text: "Unstage all"; enabled: !gitRepository.busy; onClicked: gitRepository.unstageAll() }
                                    AppButton { text: "Stash"; enabled: !gitRepository.busy && gitRepository.dirty; onClicked: gitRepository.stash() }
                                    AppButton { text: "Pop stash"; enabled: !gitRepository.busy; onClicked: gitRepository.stashPop() }
                                    AppTextField { id: commitMessage; Layout.fillWidth: true; placeholderText: "Commit message" }
                                    AppButton { text: "Commit"; variant: "primary"; enabled: !gitRepository.busy && commitMessage.text !== ""; onClicked: gitRepository.commit(commitMessage.text) }
                                    AppButton { text: "Discard changes"; variant: "danger"; enabled: !gitRepository.busy && gitRepository.dirty; onClicked: discardConfirmation.open() }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    spacing: 10
                                    SurfaceCard {
                                        Layout.preferredWidth: 290
                                        Layout.fillHeight: true
                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 8
                                            SectionHeading { text: "Working tree" }
                                            ListView {
                                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                                model: gitRepository.statusEntries
                                                delegate: Rectangle {
                                                    width: ListView.view.width
                                                    height: 36
                                                    radius: theme.controlRadius
                                                    color: "transparent"
                                                    RowLayout {
                                                        anchors.fill: parent
                                                        spacing: 5
                                                        Label {
                                                            Layout.fillWidth: true
                                                            text: (modelData.code || "") + "  " + (modelData.path || modelData.raw || "")
                                                            color: theme.mutedText
                                                            font.family: theme.monospaceFamily
                                                            font.pointSize: theme.captionPointSize
                                                            elide: Text.ElideMiddle
                                                        }
                                                        AppButton { text: "Stage"; compact: true; enabled: !gitRepository.busy; onClicked: gitRepository.stagePath(modelData.path || "") }
                                                        AppButton { text: "Unstage"; compact: true; enabled: !gitRepository.busy; onClicked: gitRepository.unstagePath(modelData.path || "") }
                                                        AppButton { text: "Discard"; compact: true; variant: "ghost"; enabled: !gitRepository.busy; onClicked: gitRepository.discardPath(modelData.path || "") }
                                                    }
                                                }
                                            }
                                            Label { visible: gitRepository.statusEntries.length === 0; text: "Working tree clean"; color: theme.success }
                                        }
                                    }
                                    SurfaceCard {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 8
                                            SectionHeading { text: "Commits" }
                                            ListView {
                                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 5
                                                model: gitRepository.commits
                                                delegate: Rectangle {
                                                    width: ListView.view.width; height: modelData.artifactName ? 74 : 58
                                                    radius: theme.controlRadius; color: theme.recessedSurface; border.color: theme.border
                                                    RowLayout {
                                                        anchors.fill: parent; anchors.margins: 8
                                                        ColumnLayout {
                                                            Layout.fillWidth: true
                                                            Label { Layout.fillWidth: true; text: modelData.subject; color: theme.windowText; font.weight: Font.DemiBold; elide: Text.ElideRight }
                                                            Label { text: modelData.shortHash + " · " + modelData.author + " · " + modelData.date; color: theme.mutedText; font.family: theme.monospaceFamily; font.pointSize: theme.captionPointSize }
                                                            Label { visible: !!modelData.artifactName; text: "Devtool artifact · " + modelData.artifactName; color: theme.info; font.pointSize: theme.captionPointSize }
                                                        }
                                                        AppButton { visible: !!modelData.artifactPath; text: "Artifact"; compact: true; onClicked: appController.openPath(modelData.artifactPath) }
                                                        AppButton { visible: !!modelData.url; text: "Remote"; compact: true; onClicked: appController.openUrl(modelData.url) }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
