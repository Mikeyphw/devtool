import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ScrollView {
    id: root
    Theme { id: theme }
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
            AppButton { text: "Clear"; variant: "ghost"; onClicked: historyModel.clear() }
        }
        Repeater {
            model: historyModel
            delegate: SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: theme.pageMargin
                Layout.rightMargin: theme.pageMargin
                implicitHeight: 108
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 16
                    Rectangle {
                        Layout.preferredWidth: 7
                        Layout.fillHeight: true
                        radius: 4
                        color: status === "ok" ? theme.success : status === "failed" ? theme.error : theme.info
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Label { text: title; color: theme.windowText; font.pointSize: theme.cardTitlePointSize; font.weight: Font.DemiBold }
                        Label { text: repo + (target ? " · " + target : ""); color: theme.mutedText; font.pointSize: theme.smallPointSize; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Label { text: summary || status; color: theme.text; elide: Text.ElideRight; Layout.fillWidth: true; font.pointSize: theme.bodyPointSize }
                    }
                    ColumnLayout {
                        Layout.alignment: Qt.AlignVCenter
                        Label { text: status.toUpperCase(); color: status === "ok" ? theme.success : status === "failed" ? theme.error : theme.info; font.weight: Font.DemiBold; font.pointSize: theme.smallPointSize }
                        Label { text: errors + " errors"; color: theme.mutedText; font.pointSize: theme.smallPointSize }
                        Label { text: warnings + " warnings"; color: theme.mutedText; font.pointSize: theme.smallPointSize }
                    }
                }
            }
        }
        Label { visible: historyModel.count === 0; Layout.leftMargin: theme.pageMargin; text: "Completed runs will appear here."; color: theme.subtleText; font.pointSize: theme.bodyPointSize }
        Item { height: 16 }
    }
}
