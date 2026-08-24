import QtQuick
import "."

Item {
    id: root
    property real scaleFactor: 1.0
    implicitHeight: 72

    Rectangle {
        id: badge
        anchors.fill: parent
        radius: Math.max(1, width * 0.006)
        color: "#050606"
        border.width: Math.max(1, width * 0.008)
        border.color: "#aeb1ad"

        Rectangle {
            anchors.fill: parent
            anchors.margins: Math.max(2, parent.width * 0.009)
            color: "#050606"
            border.width: 1
            border.color: "#171919"
        }

        Rectangle {
            id: blueField
            objectName: "headerBlueField"
            anchors {
                left: parent.left
                top: parent.top
                bottom: parent.bottom
                margins: Math.max(3, parent.width * 0.014)
            }
            width: parent.width * 0.52
            color: "#4e9ac7"
            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0.0; color: "#5eb2db" }
                GradientStop { position: 0.48; color: "#529fca" }
                GradientStop { position: 1.0; color: "#4287b2" }
            }
        }

        Rectangle {
            id: blackField
            objectName: "headerBlackField"
            anchors {
                left: blueField.right
                right: parent.right
                top: blueField.top
                bottom: blueField.bottom
                rightMargin: Math.max(3, parent.width * 0.014)
            }
            color: "#090a0a"
        }

        Text {
            objectName: "headerHalLabel"
            text: "HAL"
            color: "#f0f0eb"
            style: Text.Outline
            styleColor: "#5f6260"
            font.family: "DejaVu Sans Condensed"
            font.pixelSize: badge.height * 0.48
            font.weight: Font.Medium
            font.letterSpacing: badge.width * 0.004
            anchors.centerIn: blueField
        }

        Text {
            objectName: "header9000Label"
            text: "9000"
            color: "#111313"
            style: Text.Outline
            styleColor: "#d6d7d2"
            font.family: "DejaVu Sans Condensed"
            font.pixelSize: badge.height * 0.48
            font.weight: Font.Medium
            font.letterSpacing: badge.width * 0.002
            anchors.centerIn: blackField
        }

        Rectangle {
            width: Math.max(2, badge.width * 0.008)
            height: width
            radius: width / 2
            anchors { right: parent.right; rightMargin: badge.width * 0.035; bottom: parent.bottom; bottomMargin: badge.height * 0.17 }
            color: "#b6b8b3"
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: mouse => mouse.accepted = true
    }
}
