import QtQuick
import QtQuick.Layouts
import "."

Item {
    id: root
    property string label: ""
    property string detail: ""
    readonly property bool compact: width < 680
    default property alias controlData: controlHost.data
    width: parent ? parent.width : 600
    // A deterministic row height avoids a loop between the anchored control host,
    // the row layout, and childrenRect during hidden StackLayout page creation.
    implicitHeight: compact ? (detail.length > 0 ? 116 : 94) : (detail.length > 0 ? 72 : 56)

    GridLayout {
        anchors.fill: parent
        columns: root.compact ? 1 : 2
        rowSpacing: HalTheme.spacing2
        columnSpacing: HalTheme.spacing4

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
            Layout.preferredWidth: root.compact ? root.width : Math.min(330, root.width * 0.48)
            Layout.fillWidth: root.compact
            Layout.preferredHeight: root.compact ? 44 : root.height
            Layout.alignment: Qt.AlignVCenter
        }
    }
}
