import QtQuick
import QtQuick.Layouts
import "."

Item {
    id: root
    property string label: ""
    property string detail: ""
    default property alias controlData: controlHost.data
    width: parent ? parent.width : 600
    // A deterministic row height avoids a loop between the anchored control host,
    // the row layout, and childrenRect during hidden StackLayout page creation.
    implicitHeight: detail.length > 0 ? 72 : 56

    RowLayout {
        anchors.fill: parent
        spacing: HalTheme.spacing4

        Column {
            id: labels
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 3
            Text {
                width: parent.width
                text: root.label
                color: HalTheme.text
                font.family: HalTheme.displayFont
                font.pixelSize: 13
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
            }
            Text {
                width: parent.width
                text: root.detail
                visible: root.detail.length > 0
                color: HalTheme.dim
                font.family: HalTheme.displayFont
                font.pixelSize: 10
                wrapMode: Text.Wrap
            }
        }

        Item {
            id: controlHost
            Layout.preferredWidth: Math.min(330, root.width * 0.48)
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignVCenter
        }
    }
}
