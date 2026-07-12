import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    id: root
    Theme { id: theme }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.pageMargin
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            PageHeading { text: "Diagnostics" }
            Item { Layout.fillWidth: true }
            AppComboBox {
                id: runPicker
                Layout.preferredWidth: 360
                model: hudStore.activeRuns
                textRole: "title"
                function selectedIndex() {
                    for (var i = 0; i < hudStore.activeRuns.length; ++i) {
                        if (hudStore.activeRuns[i] && hudStore.activeRuns[i].run_id === hudStore.selectedRunId)
                            return i
                    }
                    return hudStore.activeRuns.length > 0 ? 0 : -1
                }
                currentIndex: selectedIndex()
                displayText: currentIndex >= 0 && hudStore.activeRuns[currentIndex]
                             ? (hudStore.activeRuns[currentIndex].title || hudStore.activeRuns[currentIndex].run_id)
                             : "Select run"
                onActivated: if (hudStore.activeRuns[index]) hudStore.selectedRunId = hudStore.activeRuns[index].run_id
            }
        }

        RowLayout {
            Layout.fillWidth: true
            AppTextField { id: search; Layout.fillWidth: true; placeholderText: "Search code, message, file, or phase" }
            AppComboBox { id: severity; Layout.preferredWidth: 150; model: ["All", "error", "warning", "info"] }
            AppButton {
                text: "Copy all"
                enabled: hudStore.diagnostics.length > 0
                onClicked: {
                    var lines = []
                    for (var i = 0; i < hudStore.diagnostics.length; i++)
                        lines.push(hudStore.diagnostics[i].detail || hudStore.diagnostics[i].message)
                    appController.copyText(lines.join("\n"))
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Rectangle {
                Layout.preferredWidth: 160
                height: 58
                radius: theme.controlRadius
                color: theme.errorSurface
                border.color: theme.alpha(theme.error, 0.35)
                Label { anchors.centerIn: parent; text: "ERRORS  " + (hudStore.activeRun.errors || 0); color: theme.error; font.weight: Font.DemiBold; font.pointSize: theme.smallPointSize }
            }
            Rectangle {
                Layout.preferredWidth: 180
                height: 58
                radius: theme.controlRadius
                color: theme.warningSurface
                border.color: theme.alpha(theme.warning, 0.35)
                Label { anchors.centerIn: parent; text: "WARNINGS  " + (hudStore.activeRun.warnings || 0); color: theme.warning; font.weight: Font.DemiBold; font.pointSize: theme.smallPointSize }
            }
            Label { Layout.fillWidth: true; text: hudStore.activeRun.message || "No active diagnostic context"; color: theme.mutedText; elide: Text.ElideRight; font.pointSize: theme.bodyPointSize }
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 8
            model: hudStore.diagnostics
            delegate: Rectangle {
                required property var modelData
                property string haystack: ((modelData.code || "") + " " + (modelData.message || "") + " " + (modelData.file || "") + " " + (modelData.phase || "")).toLowerCase()
                visible: (severity.currentIndex === 0 || (modelData.severity || "info") === severity.currentText)
                         && (!search.text || haystack.includes(search.text.toLowerCase()))
                width: ListView.view.width
                height: visible ? detailColumn.implicitHeight + 26 : 0
                radius: theme.cardRadius
                color: theme.surface
                border.color: (modelData.severity || "") === "error" ? theme.alpha(theme.error, 0.45)
                              : (modelData.severity || "") === "warning" ? theme.alpha(theme.warning, 0.45)
                              : theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 13
                    spacing: 12
                    Rectangle {
                        Layout.preferredWidth: 76
                        height: 28
                        radius: 5
                        color: (modelData.severity || "") === "error" ? theme.errorSurface
                               : (modelData.severity || "") === "warning" ? theme.warningSurface
                               : theme.infoSurface
                        Label {
                            anchors.centerIn: parent
                            text: (modelData.severity || "info").toUpperCase()
                            color: (modelData.severity || "") === "error" ? theme.error
                                   : (modelData.severity || "") === "warning" ? theme.warning
                                   : theme.info
                            font.weight: Font.DemiBold
                            font.pointSize: theme.captionPointSize
                        }
                    }
                    ColumnLayout {
                        id: detailColumn
                        Layout.fillWidth: true
                        spacing: 4
                        Label {
                            Layout.fillWidth: true
                            text: (modelData.code ? modelData.code + " · " : "") + (modelData.message || modelData.detail || "Diagnostic")
                            color: theme.text
                            wrapMode: Text.Wrap
                            font.weight: Font.DemiBold
                            font.pointSize: theme.bodyPointSize
                        }
                        Label {
                            visible: modelData.file || modelData.phase
                            Layout.fillWidth: true
                            text: (modelData.file || "") + (modelData.line ? ":" + modelData.line : "") + (modelData.phase ? " · " + modelData.phase : "")
                            color: theme.mutedText
                            elide: Text.ElideMiddle
                            font.family: "monospace"
                            font.pointSize: theme.smallPointSize
                        }
                    }
                    AppButton { text: "Copy"; variant: "ghost"; compact: true; onClicked: appController.copyText(modelData.detail || modelData.message || "") }
                    AppButton { visible: !!modelData.file; text: "Open"; variant: "ghost"; compact: true; onClicked: appController.openPath(modelData.file) }
                }
            }
        }

        Label {
            visible: hudStore.diagnostics.length === 0
            text: "Detailed diagnostics appear after compilers and tests report issues. Live counts remain available above."
            color: theme.subtleText
            wrapMode: Text.Wrap
            font.pointSize: theme.bodyPointSize
        }
    }
}
