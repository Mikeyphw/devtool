import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

AppScrollView {
    id: root
    property var selectedRun: ({})
    readonly property bool narrowMode: availableWidth < 720
    Theme { id: theme }
    Accessible.role: Accessible.Pane
    Accessible.name: "Run history"
    Accessible.description: "Search completed runs, inspect details, copy commands, and rerun workflows"

    function filteredRuns() {
        return historyModel.search(searchField.text, statusFilter.currentText.toLowerCase(), repositoryFilter.currentValue || "")
    }
    function duration(value) {
        const seconds = Number(value || 0)
        return seconds < 60 ? Math.round(seconds) + "s" : Math.floor(seconds / 60) + "m " + Math.round(seconds % 60) + "s"
    }

    Popup {
        id: detailsPopup
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        width: Math.min(820, Overlay.overlay.width - 32)
        height: Math.min(600, Overlay.overlay.height - 32)
        x: (Overlay.overlay.width - width) / 2
        y: (Overlay.overlay.height - height) / 2
        Accessible.role: Accessible.Dialog
        Accessible.name: "Run details"
        background: Rectangle { color: theme.elevatedSurface; radius: theme.cardRadius; border.color: theme.strongBorder; border.width: 1 }
        contentItem: ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: root.selectedRun.title || "Run details"; color: theme.windowText; font.pointSize: theme.pageTitlePointSize; font.weight: Font.DemiBold; Accessible.role: Accessible.Heading; Accessible.name: text }
                AppButton { text: "Close"; accessibleDescription: "Close run details. Keyboard shortcut Escape."; variant: "ghost"; onClicked: detailsPopup.close() }
            }
            Label { Layout.fillWidth: true; text: (root.selectedRun.repo_name || "") + (root.selectedRun.target ? " · " + root.selectedRun.target : "") + " · " + String(root.selectedRun.status || "").toUpperCase(); color: theme.mutedText; wrapMode: Text.Wrap }
            Label { Layout.fillWidth: true; text: root.selectedRun.summary || "No summary captured"; color: theme.text; wrapMode: Text.Wrap }
            SectionHeading { text: "Command" }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 160
                color: theme.recessedSurface
                radius: theme.controlRadius
                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 8
                    AppTextArea { accessibleName: "Run command"; text: root.selectedRun.command || ""; readOnly: true; wrapMode: TextEdit.WrapAnywhere; background: null }
                }
            }
            GridLayout {
                Layout.fillWidth: true
                columns: root.narrowMode ? 1 : 2
                Label { text: "Started"; color: theme.mutedText }
                Label { text: root.selectedRun.started_at || "Unknown"; color: theme.text }
                Label { text: "Duration"; color: theme.mutedText }
                Label { text: root.duration(root.selectedRun.duration); color: theme.text }
                Label { text: "Commit"; color: theme.mutedText }
                Label { text: root.selectedRun.git_commit || "None"; color: theme.text; font.family: theme.monospaceFamily }
                Label { text: "Artifact"; color: theme.mutedText }
                Label { text: root.selectedRun.artifact_name || "None"; color: theme.text }
            }
            Item { Layout.fillHeight: true }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                AppButton { text: "Copy command"; accessibleDescription: "Copy the command from this run"; onClicked: appController.copyText(root.selectedRun.command || "") }
                AppButton { text: "Rerun"; variant: "primary"; enabled: !!root.selectedRun.command; onClicked: appController.runHistoricalCommand(root.selectedRun.command, root.selectedRun.repo_root || "") }
                AppButton { text: "Open log"; enabled: !!root.selectedRun.log_path; onClicked: appController.openPath(root.selectedRun.log_path || "") }
                AppButton { text: "Open artifact"; enabled: !!root.selectedRun.artifact_path; onClicked: appController.openPath(root.selectedRun.artifact_path || "") }
            }
        }
    }

    Popup {
        id: clearConfirmation
        parent: Overlay.overlay
        modal: true
        focus: true
        width: Math.min(420, Overlay.overlay.width - 32)
        height: Math.min(210, Overlay.overlay.height - 32)
        x: (Overlay.overlay.width - width) / 2
        y: (Overlay.overlay.height - height) / 2
        Accessible.role: Accessible.Dialog
        Accessible.name: "Clear all run history confirmation"
        background: Rectangle { color: theme.elevatedSurface; radius: theme.cardRadius; border.color: theme.strongBorder; border.width: 1 }
        contentItem: ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 12
            Label { text: "Clear all run history?"; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold; Accessible.role: Accessible.Heading; Accessible.name: text }
            Label { Layout.fillWidth: true; text: "This removes saved run metadata. Repositories, artifacts, and log files are not deleted."; color: theme.mutedText; wrapMode: Text.Wrap }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton { text: "Cancel"; variant: "ghost"; onClicked: clearConfirmation.close() }
                AppButton { text: "Clear history"; accessibleDescription: "Permanently remove all saved run metadata"; variant: "danger"; onClicked: { historyModel.clear(); clearConfirmation.close() } }
            }
        }
    }

    ColumnLayout {
        width: root.availableWidth
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            Layout.topMargin: 20
            PageHeading { text: "History" }
            Item { Layout.fillWidth: true }
            AppButton { text: "Clear"; accessibleDescription: "Clear all saved run history"; variant: "ghost"; enabled: historyModel.count > 0; onClicked: clearConfirmation.open() }
        }

        SurfaceCard {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            implicitHeight: filters.implicitHeight + 28
            GridLayout {
                id: filters
                anchors.fill: parent
                anchors.margins: 14
                columns: root.narrowMode ? 1 : 3
                columnSpacing: 10
                rowSpacing: 10
                AppTextField { id: searchField; accessibleName: "Search run history"; accessibleDescription: "Search commands, repositories, targets, artifacts, and commits. Keyboard shortcut Control+F."; Layout.fillWidth: true; placeholderText: "Search command, repository, target, artifact, commit…" }
                AppComboBox { id: statusFilter; accessibleName: "Run status filter"; Layout.fillWidth: root.narrowMode; Layout.preferredWidth: root.narrowMode ? -1 : 150; model: ["All", "Ok", "Failed", "Cancelled", "Interrupted", "Skipped"] }
                AppComboBox { id: repositoryFilter; accessibleName: "Repository filter"; Layout.fillWidth: root.narrowMode; Layout.preferredWidth: root.narrowMode ? -1 : 230; textRole: "label"; valueRole: "path"; model: [{"label":"All repositories","path":""}].concat(historyModel.repositories()) }
            }
        }

        Label { Layout.leftMargin: theme.pageMargin; text: root.filteredRuns().length + " of " + historyModel.count + " runs"; color: theme.subtleText; Accessible.name: text }

        Repeater {
            model: root.filteredRuns()
            delegate: SurfaceCard {
                id: runCard
                required property var modelData
                Layout.fillWidth: true
                Layout.leftMargin: theme.pageMargin
                Layout.rightMargin: theme.pageMargin
                implicitHeight: cardContent.implicitHeight + 28
                activeFocusOnTab: true
                Accessible.role: Accessible.Button
                Accessible.name: (modelData.title || "devtool") + ", " + String(modelData.status || "") + ", " + (modelData.summary || "No summary")
                Accessible.description: "Press Enter to open details"
                Keys.onReturnPressed: { root.selectedRun = modelData; detailsPopup.open() }
                Keys.onEnterPressed: { root.selectedRun = modelData; detailsPopup.open() }
                border.width: activeFocus ? 2 : 1
                border.color: activeFocus ? theme.accent : theme.border

                ColumnLayout {
                    id: cardContent
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 5; radius: 3; color: modelData.status === "ok" ? theme.success : modelData.status === "failed" ? theme.error : theme.info; Accessible.ignored: true }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: root.narrowMode ? 1 : 2
                        columnSpacing: 14
                        rowSpacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { Layout.fillWidth: true; text: modelData.title || "devtool"; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: (modelData.repo_name || "") + (modelData.target ? " · " + modelData.target : ""); color: theme.mutedText; elide: Text.ElideMiddle }
                            Label { Layout.fillWidth: true; text: modelData.summary || modelData.status; color: theme.text; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: (modelData.started_at || "Unknown time") + " · " + root.duration(modelData.duration) + (modelData.git_commit ? " · " + modelData.git_commit : ""); color: theme.subtleText; elide: Text.ElideMiddle }
                        }
                        ColumnLayout {
                            Layout.alignment: root.narrowMode ? Qt.AlignLeft : Qt.AlignRight
                            Label { text: String(modelData.status || "").toUpperCase(); color: modelData.status === "ok" ? theme.success : modelData.status === "failed" ? theme.error : theme.info; font.weight: Font.DemiBold }
                            Label { text: (modelData.errors || 0) + " errors · " + (modelData.warnings || 0) + " warnings"; color: theme.mutedText }
                            Flow {
                                spacing: 8
                                AppButton { text: "Details"; compact: true; accessibleDescription: "Open details for this run"; onClicked: { root.selectedRun = modelData; detailsPopup.open() } }
                                AppButton { text: "Rerun"; compact: true; variant: "primary"; enabled: !!modelData.command; onClicked: appController.runHistoricalCommand(modelData.command, modelData.repo_root || "") }
                            }
                        }
                    }
                }
            }
        }

        Label { visible: root.filteredRuns().length === 0; Layout.leftMargin: theme.pageMargin; text: historyModel.count === 0 ? "Completed runs will appear here." : "No runs match the current search and filters."; color: theme.subtleText }
        Item { height: 16 }
    }

    Shortcut { sequence: "Ctrl+F"; onActivated: searchField.forceActiveFocus() }
}
