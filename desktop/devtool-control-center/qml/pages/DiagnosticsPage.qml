import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import Devtool.ControlCenter.Components 1.0

Item {
    id: root
    readonly property bool narrowMode: width < 760
    Theme { id: theme }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: theme.pageMargin
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            PageHeading { text: "Logs" }
            Item { Layout.fillWidth: true }
            AppComboBox {
                id: runPicker
                accessibleName: "Diagnostic run selector"
                Layout.fillWidth: root.narrowMode
                Layout.preferredWidth: root.narrowMode ? 240 : 360
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

        GridLayout {
            Layout.fillWidth: true
            columns: root.narrowMode ? 1 : 3
            columnSpacing: 10
            rowSpacing: 10
            AppTextField { id: search; accessibleName: "Search diagnostics"; Layout.fillWidth: true; placeholderText: "Search code, message, file, or phase" }
            AppComboBox { id: severity; accessibleName: "Diagnostic severity filter"; Layout.fillWidth: root.narrowMode; Layout.preferredWidth: root.narrowMode ? 0 : 150; model: ["All", "error", "warning", "info"] }
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

        GridLayout {
            Layout.fillWidth: true
            columns: root.narrowMode ? 2 : 3
            columnSpacing: 10
            rowSpacing: 8
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 160
                height: 58
                radius: theme.controlRadius
                color: (hudStore.activeRun.errors || 0) > 0 ? theme.errorSurface : theme.recessedSurface
                border.color: (hudStore.activeRun.errors || 0) > 0 ? theme.alpha(theme.error, 0.35) : theme.border
                Label { anchors.centerIn: parent; text: "ERRORS  " + (hudStore.activeRun.errors || 0); color: theme.error; font.weight: Font.DemiBold; font.pointSize: theme.smallPointSize }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 180
                height: 58
                radius: theme.controlRadius
                color: (hudStore.activeRun.warnings || 0) > 0 ? theme.warningSurface : theme.recessedSurface
                border.color: (hudStore.activeRun.warnings || 0) > 0 ? theme.alpha(theme.warning, 0.35) : theme.border
                Label { anchors.centerIn: parent; text: "WARNINGS  " + (hudStore.activeRun.warnings || 0); color: theme.warning; font.weight: Font.DemiBold; font.pointSize: theme.smallPointSize }
            }
            Label { Layout.fillWidth: true; Layout.columnSpan: root.narrowMode ? 2 : 1; text: hudStore.activeRun.message || "No active diagnostic context"; color: theme.mutedText; elide: Text.ElideRight; font.pointSize: theme.bodyPointSize; Accessible.name: text }
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 8
            visible: hudStore.diagnostics.length > 0
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
                            font.family: theme.monospaceFamily
                            font.pointSize: theme.smallPointSize
                        }
                    }
                    AppButton { text: "Copy"; variant: "ghost"; compact: true; onClicked: appController.copyText(modelData.detail || modelData.message || "") }
                    AppButton { visible: !!modelData.file; text: "Open"; variant: "ghost"; compact: true; onClicked: appController.openPath(modelData.file) }
                }
            }
        }

        SurfaceCard {
            visible: hudStore.diagnostics.length === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 220
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 560)
                spacing: 10
                StatusMark {
                    Layout.alignment: Qt.AlignHCenter
                    kind: "ok"
                    markColor: theme.success
                    markSize: 28
                }
                Label {
                    Layout.fillWidth: true
                    text: hudStore.activeRun.message || "Completed successfully"
                    color: theme.windowText
                    font.pointSize: theme.cardTitlePointSize
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                }
                Label {
                    Layout.fillWidth: true
                    text: "No diagnostics were reported for this run. Live counts remain available above."
                    color: theme.mutedText
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                }
                Flow {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 8
                    AppButton { text: "Copy summary"; onClicked: appController.copyText(hudStore.focusedSummary(hudStore.activeRun)) }
                    AppButton { text: "Open log"; enabled: !!hudStore.activeRun.log_path; onClicked: appController.openPath(hudStore.activeRun.log_path || "") }
                }
            }
        }
    }
}
