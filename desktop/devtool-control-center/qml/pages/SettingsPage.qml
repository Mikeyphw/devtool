import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"

AppScrollView {
    id: root
    Theme { id: theme }
    FolderDialog { id: stateDialog; onAccepted: hudStore.stateDirectory = selectedFolder.toString().replace("file://", "") }

    ColumnLayout {
        width: root.availableWidth
        spacing: theme.sectionSpacing
        PageHeading { text: "Settings"; Layout.leftMargin: theme.pageMargin; Layout.topMargin: 20 }

        SurfaceCard {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            implicitHeight: settingsColumn.implicitHeight + 32
            ColumnLayout {
                id: settingsColumn
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10
                SectionHeading { text: "Devtool executable" }
                AppTextField { Layout.fillWidth: true; text: appController.devtoolExecutable; onEditingFinished: appController.devtoolExecutable = text }
                Label {
                    Layout.fillWidth: true
                    text: appController.devtoolAvailable ? "Devtool CLI found" : "Devtool CLI not found. Install it with pip/pipx or select its executable here."
                    color: appController.devtoolAvailable ? theme.success : theme.error
                    wrapMode: Text.Wrap
                    font.pointSize: theme.smallPointSize
                    lineHeight: theme.bodyLineHeight
                    lineHeightMode: Text.ProportionalHeight
                }
                SectionHeading { text: "Live state directory"; Layout.topMargin: 6 }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField { Layout.fillWidth: true; text: hudStore.stateDirectory; onEditingFinished: hudStore.stateDirectory = text }
                    AppButton { text: "Browse…"; onClicked: stateDialog.open() }
                }
                AppCheckBox { text: "Show compact HUD"; checked: appController.compactHudVisible; onToggled: appController.compactHudVisible = checked }
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    AppButton { text: "Save settings"; variant: "primary"; onClicked: appController.saveSettings() }
                }
            }
        }

        SurfaceCard {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            implicitHeight: integrationColumn.implicitHeight + 32
            ColumnLayout {
                id: integrationColumn
                anchors.fill: parent
                anchors.margins: 16
                spacing: 8
                Label { text: "Live integration"; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold }
                Label {
                    Layout.fillWidth: true
                    text: hudStore.ipcConnected ? "Connected through the native Unix socket." : "IPC disconnected; file monitoring is active as a fallback."
                    color: hudStore.ipcConnected ? theme.success : theme.warning
                    wrapMode: Text.Wrap
                    font.pointSize: theme.bodyPointSize
                    lineHeight: theme.bodyLineHeight
                    lineHeightMode: Text.ProportionalHeight
                }
                Label { Layout.fillWidth: true; text: "Socket: " + ipcClient.socketPath; color: theme.subtleText; font.family: "monospace"; font.pointSize: theme.smallPointSize; elide: Text.ElideMiddle }
                Label {
                    Layout.fillWidth: true
                    text: "Runs started here use --hud-mode control-center. Live updates and cancellation use IPC; state.json remains the recovery fallback."
                    color: theme.mutedText
                    wrapMode: Text.Wrap
                    font.pointSize: theme.bodyPointSize
                    lineHeight: theme.bodyLineHeight
                    lineHeightMode: Text.ProportionalHeight
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: "Appearance follows the active Qt 6 style, system palette, accent color, font, and display scaling. Override the control style with QT_QUICK_CONTROLS_STYLE or -style."
                        color: theme.mutedText
                        wrapMode: Text.Wrap
                        font.pointSize: theme.smallPointSize
                        lineHeight: theme.bodyLineHeight
                        lineHeightMode: Text.ProportionalHeight
                    }
                    AppButton { text: "Reconnect IPC"; onClicked: ipcClient.reconnect() }
                }
            }
        }
        SurfaceCard {
            Layout.fillWidth: true
            Layout.leftMargin: theme.pageMargin
            Layout.rightMargin: theme.pageMargin
            implicitHeight: clipboardColumn.implicitHeight + 32
            ColumnLayout {
                id: clipboardColumn
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                SectionHeading { text: "Clipboard monitoring" }
                Label {
                    Layout.fillWidth: true
                    text: "The optional user service watches for local devtool artifact paths and commands. It never executes clipboard content automatically. Other clipboard text is ignored."
                    color: theme.mutedText
                    wrapMode: Text.Wrap
                    font.pointSize: theme.bodyPointSize
                    lineHeight: theme.bodyLineHeight
                    lineHeightMode: Text.ProportionalHeight
                }
                RowLayout {
                    Layout.fillWidth: true
                    Rectangle {
                        width: 9
                        height: 9
                        radius: 5
                        color: appController.clipboardMonitorActive ? theme.success : appController.clipboardMonitorInstalled ? theme.warning : theme.subtleText
                    }
                    Label {
                        Layout.fillWidth: true
                        text: appController.clipboardMonitorStatus
                        color: appController.clipboardMonitorActive ? theme.success : appController.clipboardMonitorInstalled ? theme.warning : theme.mutedText
                        wrapMode: Text.Wrap
                        font.pointSize: theme.bodyPointSize
                    }
                    AppButton { text: "Refresh"; variant: "ghost"; onClicked: appController.refreshClipboardMonitorStatus() }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    AppButton {
                        text: appController.clipboardMonitorInstalled ? "Reinstall & enable" : "Install & enable"
                        variant: "primary"
                        onClicked: appController.installClipboardMonitor()
                    }
                    AppButton {
                        text: "Enable"
                        enabled: appController.clipboardMonitorInstalled && !appController.clipboardMonitorActive
                        onClicked: appController.enableClipboardMonitor()
                    }
                    AppButton {
                        text: "Disable"
                        enabled: appController.clipboardMonitorActive
                        onClicked: appController.disableClipboardMonitor()
                    }
                    AppButton {
                        text: "Remove"
                        variant: "danger"
                        enabled: appController.clipboardMonitorInstalled
                        onClicked: appController.removeClipboardMonitor()
                    }
                    Item { Layout.fillWidth: true }
                    AppButton { text: "Check clipboard now"; variant: "ghost"; onClicked: appController.scanClipboardNow() }
                }

                Rectangle {
                    Layout.fillWidth: true
                    visible: appController.clipboardCandidateSummary !== ""
                    implicitHeight: candidateColumn.implicitHeight + 24
                    radius: 8
                    color: theme.recessedSurface
                    border.color: theme.border
                    border.width: 1
                    ColumnLayout {
                        id: candidateColumn
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 6
                        Label {
                            text: appController.clipboardCandidateKind === "artifact" ? "Latest detected artifact" : "Latest detected command"
                            color: theme.windowText
                            font.pointSize: theme.cardTitlePointSize
                            font.weight: Font.DemiBold
                            lineHeight: theme.titleLineHeight
                            lineHeightMode: Text.ProportionalHeight
                        }
                        Label {
                            Layout.fillWidth: true
                            text: appController.clipboardCandidateSummary
                            color: theme.mutedText
                            wrapMode: Text.Wrap
                            font.family: appController.clipboardCandidateKind === "command" ? "monospace" : ""
                            font.pointSize: theme.smallPointSize
                            lineHeight: theme.bodyLineHeight
                            lineHeightMode: Text.ProportionalHeight
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: appController.clipboardCandidateTimestamp !== ""
                            text: appController.clipboardCandidateTimestamp
                            color: theme.subtleText
                            font.pointSize: theme.captionPointSize
                            lineHeight: theme.compactLineHeight
                            lineHeightMode: Text.ProportionalHeight
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton {
                                text: appController.clipboardCandidateKind === "artifact" ? "Inspect artifact" : "Review & run"
                                variant: "primary"
                                onClicked: appController.useClipboardCandidate()
                            }
                            AppButton { text: "Copy"; onClicked: appController.copyText(appController.clipboardCandidateCopyText()) }
                            AppButton { text: "Dismiss"; variant: "ghost"; onClicked: appController.clearClipboardCandidate() }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }
            }
        }

        Item { height: 16 }
    }
}
