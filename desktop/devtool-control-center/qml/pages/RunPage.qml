import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"

ScrollView {
    id: root
    property string operation: "Apply overlay"
    Theme { id: theme }
    clip: true

    FolderDialog { id: repoDialog; onAccepted: appController.repository = selectedFolder.toString().replace("file://", "") }
    FileDialog {
        id: artifactDialog
        nameFilters: ["Archives (*.zip *.tar *.gz *.zst)", "All files (*)"]
        onAccepted: appController.artifact = selectedFile.toString().replace("file://", "")
    }

    ColumnLayout {
        width: root.availableWidth
        spacing: theme.sectionSpacing
        PageHeading { text: "Run"; Layout.leftMargin: theme.pageMargin; Layout.topMargin: 20 }

        SurfaceCard {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            visible: appController.clipboardCandidateKind === "command" && appController.clipboardCandidateText !== ""
            implicitHeight: clipboardCommandColumn.implicitHeight + 28
            ColumnLayout {
                id: clipboardCommandColumn
                anchors.fill: parent
                anchors.margins: 14
                spacing: 8
                Label {
                    text: "Detected clipboard command"
                    color: theme.windowText
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.DemiBold
                }
                Label {
                    Layout.fillWidth: true
                    text: "Review the command below. Clipboard monitoring never executes it automatically."
                    color: theme.mutedText
                    wrapMode: Text.Wrap
                    font.pointSize: theme.smallPointSize
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: commandText.implicitHeight + 20
                    radius: 7
                    color: theme.recessedSurface
                    border.color: theme.border
                    border.width: 1
                    Label {
                        id: commandText
                        anchors.fill: parent
                        anchors.margins: 10
                        text: appController.clipboardCandidateText
                        color: theme.windowText
                        font.family: "monospace"
                        font.pointSize: theme.smallPointSize
                        wrapMode: Text.WrapAnywhere
                    }
                }
                RowLayout {
                    AppButton { text: "Copy command"; variant: "primary"; onClicked: appController.copyText(appController.clipboardCandidateText) }
                    AppButton { text: "Dismiss"; variant: "ghost"; onClicked: appController.clearClipboardCandidate() }
                    Item { Layout.fillWidth: true }
                }
            }
        }

        SurfaceCard {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            implicitHeight: form.implicitHeight + 32
            GridLayout {
                id: form
                anchors.fill: parent
                anchors.margins: 16
                columns: 3
                columnSpacing: 12
                rowSpacing: 12

                Label { text: "Repository"; color: theme.mutedText; font.pointSize: theme.smallPointSize; verticalAlignment: Text.AlignVCenter }
                AppTextField { Layout.fillWidth: true; text: appController.repository; onTextEdited: appController.repository = text; placeholderText: "~/Code/project" }
                AppButton { text: "Browse…"; onClicked: repoDialog.open() }

                Label { text: "Operation"; color: theme.mutedText; font.pointSize: theme.smallPointSize; verticalAlignment: Text.AlignVCenter }
                AppComboBox { Layout.fillWidth: true; model: ["Apply overlay", "Validate", "Restore", "Build", "Test", "Status", "Doctor"]; onCurrentTextChanged: root.operation = currentText }
                Item { width: 1; height: 1 }

                Label { text: "Artifact"; color: theme.mutedText; visible: root.operation === "Apply overlay" }
                AppTextField { Layout.fillWidth: true; visible: root.operation === "Apply overlay"; text: appController.artifact; onTextEdited: appController.artifact = text; placeholderText: "Choose an overlay or bundle" }
                RowLayout {
                    visible: root.operation === "Apply overlay"
                    spacing: 6
                    AppButton { text: "Browse…"; onClicked: artifactDialog.open() }
                    AppButton {
                        text: "Inspect"
                        variant: "ghost"
                        enabled: appController.artifact !== ""
                        onClicked: {
                            artifactInspector.artifactPath = appController.artifact
                            artifactInspector.repositoryPath = appController.repository
                            artifactInspector.inspect(true)
                            appController.inspectArtifactRequested()
                        }
                    }
                }

                Label { text: "Target"; color: theme.mutedText }
                AppTextField { id: target; Layout.fillWidth: true; placeholderText: "Optional target/profile" }
                Item { width: 1; height: 1 }

                Label { text: "Commit message"; color: theme.mutedText; visible: root.operation === "Apply overlay" }
                AppTextField { id: message; Layout.fillWidth: true; visible: root.operation === "Apply overlay"; placeholderText: "Artifact manifest may provide this" }
                Item { width: 1; height: 1 }
            }
        }

        RowLayout {
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            spacing: 14
            AppCheckBox { id: validate; text: "Validate"; checked: true; visible: root.operation === "Apply overlay" }
            AppCheckBox { id: commit; text: "Commit"; checked: true; visible: root.operation === "Apply overlay" }
            AppCheckBox { id: keep; text: "Keep artifact"; visible: root.operation === "Apply overlay" }
            AppCheckBox { text: "Show compact HUD"; checked: appController.compactHudVisible; onToggled: appController.compactHudVisible = checked }
            Item { Layout.fillWidth: true }
        }

        RowLayout {
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            spacing: 10
            AppButton {
                text: processRunner.running ? "Running…" : "Run"
                variant: "primary"
                enabled: !processRunner.running && appController.repository !== "" && (root.operation !== "Apply overlay" || appController.artifact !== "")
                onClicked: appController.runWorkflow(root.operation, target.text, validate.checked, commit.checked, message.text, keep.checked)
            }
            AppButton { text: "Cancel"; variant: "danger"; enabled: processRunner.running; onClicked: processRunner.cancel() }
            AppButton { text: "Clear output"; variant: "ghost"; onClicked: processRunner.clear() }
            Item { Layout.fillWidth: true }
        }

        Label {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            text: processRunner.commandLine
            color: theme.info
            font.family: "monospace"
            font.pointSize: theme.smallPointSize
            wrapMode: Text.Wrap
        }

        SurfaceCard {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            Layout.preferredHeight: 330
            color: theme.recessedSurface
            ScrollView {
                anchors.fill: parent
                anchors.margins: 8
                AppTextArea {
                    text: processRunner.output
                    readOnly: true
                    font.family: "monospace"
                    font.pointSize: theme.smallPointSize
                    wrapMode: TextEdit.NoWrap
                    background: null
                }
            }
        }
        Item { height: 16 }
    }
}
