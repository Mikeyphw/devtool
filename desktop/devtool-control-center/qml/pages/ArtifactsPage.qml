import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"

Item {
    id: root
    Theme { id: theme }

    FileDialog {
        id: artifactDialog
        nameFilters: ["Devtool artifacts (*.zip *.tar *.gz *.zst)", "All files (*)"]
        onAccepted: {
            artifactInspector.artifactPath = selectedFile.toString().replace("file://", "")
            appController.artifact = artifactInspector.artifactPath
            artifactInspector.inspect(true)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.pageMargin
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            PageHeading { text: "Artifacts" }
            Item { Layout.fillWidth: true }
            AppButton { text: "Open artifact…"; variant: "primary"; onClicked: artifactDialog.open() }
            AppButton { text: artifactInspector.loading ? "Inspecting…" : "Refresh"; enabled: !artifactInspector.loading && artifactInspector.artifactPath !== ""; onClicked: artifactInspector.inspect(true) }
        }

        RowLayout {
            Layout.fillWidth: true
            AppTextField {
                Layout.fillWidth: true
                text: artifactInspector.artifactPath
                placeholderText: "Choose an overlay or bundle"
                onTextEdited: { artifactInspector.artifactPath = text; appController.artifact = text }
                onAccepted: artifactInspector.inspect(true)
            }
            AppTextField {
                Layout.preferredWidth: 360
                text: artifactInspector.repositoryPath
                placeholderText: "Repository for preview/diff (optional)"
                onTextEdited: artifactInspector.repositoryPath = text
                onAccepted: artifactInspector.inspect(true)
            }
        }

        Rectangle {
            visible: artifactInspector.error !== ""
            Layout.fillWidth: true
            implicitHeight: errorText.implicitHeight + 24
            radius: theme.cardRadius
            color: theme.errorSurface
            border.color: theme.alpha(theme.error, 0.45)
            Label {
                id: errorText
                anchors.fill: parent
                anchors.margins: 12
                text: artifactInspector.error
                color: theme.error
                wrapMode: Text.Wrap
                font.pointSize: theme.bodyPointSize
            }
        }

        RowLayout {
            visible: Object.keys(artifactInspector.summary).length > 0
            Layout.fillWidth: true
            spacing: 10
            MetricCard { label: "FILES"; value: artifactInspector.summary.payload_files || 0; accent: theme.info }
            MetricCard { label: "ADDED"; value: (artifactInspector.summary.counts || {}).add || 0; accent: theme.success }
            MetricCard { label: "MODIFIED"; value: (artifactInspector.summary.counts || {}).modify || 0; accent: theme.warning }
            MetricCard { label: "DELETED"; value: (artifactInspector.summary.counts || {}).delete || 0; accent: theme.error }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 74
                radius: theme.cardRadius
                color: ((artifactInspector.summary.validation || {}).ok) ? theme.successSurface : theme.errorSurface
                border.color: ((artifactInspector.summary.validation || {}).ok) ? theme.alpha(theme.success, 0.45) : theme.alpha(theme.error, 0.45)
                Column {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 4
                    Label {
                        text: ((artifactInspector.summary.validation || {}).ok) ? "VALIDATION PASSED" : "VALIDATION FAILED"
                        color: ((artifactInspector.summary.validation || {}).ok) ? theme.success : theme.error
                        font.weight: Font.DemiBold
                        font.pointSize: theme.smallPointSize
                    }
                    Label {
                        width: parent.width
                        elide: Text.ElideRight
                        color: theme.mutedText
                        text: (((artifactInspector.summary.validation || {}).messages || [])[0] || "No validation result")
                        font.pointSize: theme.smallPointSize
                    }
                }
            }
        }

        SurfaceCard {
            visible: Object.keys(artifactInspector.summary).length > 0
            Layout.fillWidth: true
            implicitHeight: summaryColumn.implicitHeight + 24
            ColumnLayout {
                id: summaryColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 5
                Label { text: artifactInspector.summary.name || artifactInspector.summary.filename || "Artifact"; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold }
                Label { Layout.fillWidth: true; text: artifactInspector.summary.description || "No description"; color: theme.mutedText; wrapMode: Text.Wrap; font.pointSize: theme.bodyPointSize }
                Label { text: (artifactInspector.summary.kind || "overlay") + " · schema " + (artifactInspector.summary.schema_version || 1) + (artifactInspector.summary.target ? " · " + artifactInspector.summary.target : ""); color: theme.info; font.pointSize: theme.smallPointSize }
                Label { text: "SHA-256  " + (artifactInspector.summary.sha256 || ""); color: theme.subtleText; font.family: "monospace"; font.pointSize: theme.captionPointSize }
            }
        }

        TabBar {
            id: tabs
            visible: Object.keys(artifactInspector.summary).length > 0
            Layout.fillWidth: true
            background: Rectangle { color: "transparent" }
            AppTabButton { text: "Inventory" }
            AppTabButton { text: "Bundle"; enabled: artifactInspector.overlays.length > 0 }
            AppTabButton { text: "Diff" }
            AppTabButton { text: "Manifest" }
        }

        StackLayout {
            visible: tabs.visible
            currentIndex: tabs.currentIndex
            Layout.fillWidth: true
            Layout.fillHeight: true

            SurfaceCard {
                color: theme.recessedSurface
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    RowLayout {
                        Layout.fillWidth: true
                        AppTextField { id: inventoryFilter; Layout.fillWidth: true; placeholderText: "Filter files" }
                        AppComboBox { id: actionFilter; Layout.preferredWidth: 150; model: ["All", "add", "modify", "delete"] }
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: artifactInspector.inventory
                        spacing: 4
                        delegate: Rectangle {
                            required property var modelData
                            visible: (actionFilter.currentIndex === 0 || (modelData.action || modelData.kind) === actionFilter.currentText)
                                     && (!inventoryFilter.text || (modelData.path || "").toLowerCase().includes(inventoryFilter.text.toLowerCase()))
                            width: ListView.view.width
                            height: visible ? 46 : 0
                            radius: theme.controlRadius
                            color: index % 2 ? theme.surface : theme.elevatedSurface
                            border.color: theme.border
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 10
                                Rectangle {
                                    Layout.preferredWidth: 76
                                    height: 25
                                    radius: 5
                                    color: (modelData.action || modelData.kind) === "add" ? theme.successSurface
                                           : (modelData.action || modelData.kind) === "delete" ? theme.errorSurface
                                           : theme.warningSurface
                                    Label {
                                        anchors.centerIn: parent
                                        text: (modelData.action || modelData.kind || "file").toUpperCase()
                                        color: (modelData.action || modelData.kind) === "add" ? theme.success
                                               : (modelData.action || modelData.kind) === "delete" ? theme.error
                                               : theme.warning
                                        font.weight: Font.DemiBold
                                        font.pointSize: theme.captionPointSize
                                    }
                                }
                                Label { Layout.fillWidth: true; text: modelData.path || ""; color: theme.text; elide: Text.ElideMiddle; font.family: "monospace"; font.pointSize: theme.smallPointSize }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                color: theme.recessedSurface
                ScrollView {
                    anchors.fill: parent
                    background: Rectangle { color: "transparent" }
                    anchors.margins: 12
                    Column {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            model: artifactInspector.overlays
                            delegate: Column {
                                required property var modelData
                                width: parent.width
                                spacing: 5
                                Rectangle {
                                    width: parent.width
                                    height: 92
                                    radius: theme.controlRadius
                                    color: theme.surface
                                    border.color: modelData.required === false ? theme.strongBorder : theme.alpha(theme.info, 0.55)
                                    Column {
                                        anchors.fill: parent
                                        anchors.margins: 12
                                        spacing: 4
                                        Label { text: (modelData.index || index + 1) + ". " + (modelData.label || modelData.id); color: theme.windowText; font.weight: Font.DemiBold; font.pointSize: theme.bodyPointSize }
                                        Label { text: (modelData.target ? "Target: " + modelData.target + "   " : "") + (modelData.required === false ? "Optional" : "Required"); color: theme.mutedText; font.pointSize: theme.smallPointSize }
                                        Label { visible: (modelData.after || []).length || (modelData.requires || []).length; text: "After: " + (modelData.after || []).join(", ") + "   Requires: " + (modelData.requires || []).join(", "); color: theme.info; elide: Text.ElideRight; width: parent.width; font.pointSize: theme.smallPointSize }
                                    }
                                }
                                Label { visible: index < artifactInspector.overlays.length - 1; text: "↓"; color: theme.info; font.pointSize: theme.cardTitlePointSize; horizontalAlignment: Text.AlignHCenter; width: parent.width }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                color: theme.recessedSurface
                AppTextArea { anchors.fill: parent; anchors.margins: 8; readOnly: true; text: artifactInspector.diffText || "Select a repository and refresh to calculate a diff."; font.family: "monospace"; font.pointSize: theme.smallPointSize; wrapMode: TextEdit.NoWrap; background: null }
            }
            SurfaceCard {
                color: theme.recessedSurface
                AppTextArea { anchors.fill: parent; anchors.margins: 8; readOnly: true; text: artifactInspector.manifestText; font.family: "monospace"; font.pointSize: theme.smallPointSize; wrapMode: TextEdit.NoWrap; background: null }
            }
        }

        RowLayout {
            visible: Object.keys(artifactInspector.summary).length > 0
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton { text: "Use in Run page"; onClicked: { appController.artifact = artifactInspector.artifactPath; appController.repository = artifactInspector.repositoryPath } }
            AppButton {
                text: "Apply"
                variant: "primary"
                enabled: artifactInspector.repositoryPath !== ""
                onClicked: {
                    appController.repository = artifactInspector.repositoryPath
                    appController.artifact = artifactInspector.artifactPath
                    appController.runWorkflow("Apply overlay", artifactInspector.summary.target || "", true, true, ((artifactInspector.summary.commit || {}).message || ""), false)
                }
            }
        }
    }
}
