import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import Devtool.ControlCenter.Components 1.0

AppScrollView {
    id: root
    readonly property bool narrowMode: availableWidth < 780
    contentHeight: artifactsColumn.implicitHeight + 20
    readonly property bool hasSummary: Object.keys(artifactInspector.summary).length > 0
    Theme { id: theme }

    PathPickerDialog {
        id: artifactDialog
        selectFolder: false
        initialPath: artifactInspector.artifactPath
        nameFilters: ["Devtool artifacts (*.zip *.tar *.gz *.zst)", "All files (*)"]
        onAccepted: function(path) {
            artifactInspector.artifactPath = path
            appController.artifact = artifactInspector.artifactPath
            artifactInspector.inspect(true)
        }
    }

    ColumnLayout {
        id: artifactsColumn
        width: Math.max(0, root.availableWidth - 2 * theme.pageMargin)
        x: theme.pageMargin
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 20
            PageHeading { text: "Artifacts" }
            Item { Layout.fillWidth: true }
            AppButton {
                text: artifactInspector.loading ? "Inspecting…" : "Refresh"
                enabled: !artifactInspector.loading && artifactInspector.artifactPath !== ""
                onClicked: artifactInspector.inspect(true)
            }
        }

        SurfaceCard {
            Layout.fillWidth: true
            implicitHeight: artifactInputs.implicitHeight + 28
            GridLayout {
                id: artifactInputs
                anchors.fill: parent
                anchors.margins: 14
                columns: root.narrowMode ? 1 : 3
                columnSpacing: 10
                rowSpacing: 10
                Label { text: "Artifact"; color: theme.mutedText }
                AppTextField {
                    Layout.fillWidth: true
                    accessibleName: "Artifact archive"
                    text: artifactInspector.artifactPath
                    placeholderText: "Choose an overlay or bundle"
                    onTextEdited: { artifactInspector.artifactPath = text; appController.artifact = text }
                    onAccepted: artifactInspector.inspect(true)
                }
                AppButton { text: "Browse…"; variant: "primary"; onClicked: artifactDialog.open() }
                Label { text: "Repository"; color: theme.mutedText }
                AppTextField {
                    Layout.fillWidth: true
                    accessibleName: "Repository for artifact preview"
                    text: artifactInspector.repositoryPath
                    placeholderText: "Repository for preview and diff (optional)"
                    onTextEdited: artifactInspector.repositoryPath = text
                    onAccepted: artifactInspector.inspect(true)
                }
                Item { visible: !root.narrowMode }
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

        SurfaceCard {
            visible: !root.hasSummary && artifactInspector.error === ""
            Layout.fillWidth: true
            Layout.fillHeight: false
            Layout.minimumHeight: 190
            implicitHeight: artifactEmptyColumn.implicitHeight + 42
            ColumnLayout {
                id: artifactEmptyColumn
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 640)
                spacing: 10
                Image {
                    Layout.alignment: Qt.AlignHCenter
                    source: "qrc:/qt/qml/Devtool/ControlCenter/icons/artifacts.svg"
                    sourceSize.width: 42
                    sourceSize.height: 42
                    width: 42
                    height: 42
                    opacity: 0.8
                    Accessible.ignored: true
                }
                Label {
                    Layout.fillWidth: true
                    text: artifactInspector.loading ? "Inspecting artifact…" : "No artifact inspected"
                    color: theme.windowText
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                }
                Label {
                    Layout.fillWidth: true
                    text: "Choose an overlay or bundle, inspect the latest downloaded artifact, or validate the current path before applying it."
                    color: theme.mutedText
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                }
                Flow {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 8
                    AppButton {
                        text: "Open artifact…"
                        variant: "primary"
                        onClicked: artifactDialog.open()
                    }
                    AppButton {
                        text: "Inspect current path"
                        enabled: artifactInspector.artifactPath !== ""
                        onClicked: artifactInspector.inspect(true)
                    }
                }
            }
        }

        GridLayout {
            visible: root.hasSummary
            Layout.fillWidth: true
            columns: root.narrowMode ? 2 : 5
            columnSpacing: 10
            rowSpacing: 10
            MetricCard { Layout.fillWidth: true; label: "FILES"; value: artifactInspector.summary.payload_files || 0; accent: theme.info }
            MetricCard { Layout.fillWidth: true; label: "ADDED"; value: (artifactInspector.summary.counts || {}).add || 0; accent: theme.success }
            MetricCard { Layout.fillWidth: true; label: "MODIFIED"; value: (artifactInspector.summary.counts || {}).modify || 0; accent: theme.warning }
            MetricCard { Layout.fillWidth: true; label: "DELETED"; value: (artifactInspector.summary.counts || {}).delete || 0; accent: theme.error }
            Rectangle {
                Layout.fillWidth: true
                Layout.columnSpan: root.narrowMode ? 2 : 1
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
            visible: root.hasSummary
            Layout.fillWidth: true
            implicitHeight: summaryColumn.implicitHeight + 24
            ColumnLayout {
                id: summaryColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Rectangle {
                        Layout.preferredWidth: 34
                        Layout.preferredHeight: 34
                        radius: 10
                        color: theme.infoSurface
                        Image { anchors.centerIn: parent; width: 20; height: 20; source: "qrc:/qt/qml/Devtool/ControlCenter/icons/artifact.svg"; sourceSize.width: 20; sourceSize.height: 20; Accessible.ignored: true }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Label { Layout.fillWidth: true; text: artifactInspector.summary.name || artifactInspector.summary.filename || "Artifact"; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold; elide: Text.ElideRight }
                        Label { text: (artifactInspector.summary.kind || "overlay").replace(/_/g, " ").toUpperCase() + "  ·  SCHEMA " + (artifactInspector.summary.schema_version || 1); color: theme.info; font.pointSize: theme.captionPointSize; font.weight: Font.DemiBold }
                    }
                    Rectangle {
                        implicitWidth: summaryState.implicitWidth + 20
                        implicitHeight: 28
                        radius: 8
                        color: ((artifactInspector.summary.validation || {}).ok) ? theme.successSurface : theme.errorSurface
                        Label {
                            id: summaryState
                            anchors.centerIn: parent
                            text: ((artifactInspector.summary.validation || {}).ok) ? "READY" : "CHECK FAILED"
                            color: ((artifactInspector.summary.validation || {}).ok) ? theme.success : theme.error
                            font.pointSize: theme.captionPointSize
                            font.weight: Font.DemiBold
                        }
                    }
                }
                Label { Layout.fillWidth: true; text: artifactInspector.summary.description || "No description"; color: theme.mutedText; wrapMode: Text.Wrap; font.pointSize: theme.bodyPointSize }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    Label { visible: artifactInspector.summary.target !== ""; text: "Target  " + artifactInspector.summary.target; color: theme.info; font.pointSize: theme.smallPointSize; font.weight: Font.DemiBold }
                    Label { visible: artifactInspector.summary.solution !== ""; text: "Solution  " + artifactInspector.summary.solution; color: theme.mutedText; font.pointSize: theme.smallPointSize; elide: Text.ElideMiddle }
                    Label { visible: artifactInspector.overlays.length > 0; text: artifactInspector.overlays.length + " overlays"; color: theme.mutedText; font.pointSize: theme.smallPointSize }
                }
            }
        }

        TabBar {
            id: tabs
            visible: root.hasSummary
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
            Layout.fillHeight: visible
            Layout.preferredHeight: visible ? 360 : 0

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
                                Label { Layout.fillWidth: true; text: modelData.path || ""; color: theme.text; elide: Text.ElideMiddle; font.family: theme.monospaceFamily; font.pointSize: theme.smallPointSize }
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
                                Image { visible: index < artifactInspector.overlays.length - 1; source: "qrc:/qt/qml/Devtool/ControlCenter/icons/arrow-down.svg"; sourceSize.width: 18; sourceSize.height: 18; width: parent.width; height: 18; fillMode: Image.PreserveAspectFit; Accessible.ignored: true }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                color: theme.recessedSurface
                AppTextArea { anchors.fill: parent; anchors.margins: 8; readOnly: true; text: artifactInspector.diffText || "Select a repository and refresh to calculate a diff."; font.family: theme.monospaceFamily; font.pointSize: theme.smallPointSize; wrapMode: TextEdit.NoWrap; background: null }
            }
            SurfaceCard {
                color: theme.recessedSurface
                AppTextArea { anchors.fill: parent; anchors.margins: 8; readOnly: true; text: artifactInspector.manifestText; font.family: theme.monospaceFamily; font.pointSize: theme.smallPointSize; wrapMode: TextEdit.NoWrap; background: null }
            }
        }

        RowLayout {
            visible: root.hasSummary
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
