import QtQuick
import "."

Item {
    id: root
    property real scaleFactor: 1.0
    implicitHeight: 180

    Rectangle {
        id: badge
        width: Math.min(parent.width * 0.62, 520 * root.scaleFactor)
        height: Math.min(parent.height * 0.75, width * 0.31)
        anchors.centerIn: parent
        radius: Math.max(2, width * 0.012)
        color: HalTheme.black
        border.width: Math.max(1, width * 0.006)
        border.color: "#9a9b96"

        Rectangle {
            anchors.fill: parent
            anchors.margins: Math.max(3, parent.width * 0.012)
            color: "#0b0b0b"
            border.width: 1
            border.color: "#353634"
            radius: Math.max(1, parent.radius - 1)
        }

        Row {
            anchors.centerIn: parent
            spacing: badge.width * 0.04

            Text {
                text: "HAL"
                color: HalTheme.text
                font.family: HalTheme.displayFont
                font.pixelSize: badge.height * 0.46
                font.weight: Font.Black
                font.italic: true
                font.letterSpacing: badge.width * 0.006
                anchors.verticalCenter: parent.verticalCenter
            }

            Rectangle {
                width: 1
                height: badge.height * 0.43
                color: HalTheme.steel
                anchors.verticalCenter: parent.verticalCenter
            }

            Text {
                text: "9000"
                color: HalTheme.text
                font.family: HalTheme.displayFont
                font.pixelSize: badge.height * 0.31
                font.weight: Font.DemiBold
                font.italic: true
                font.letterSpacing: badge.width * 0.003
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
