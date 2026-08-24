import QtQuick
import "."

Item {
    id: root
    property real scaleFactor: 1.0
    property bool active: false
    property real animationAmount: 0.72
    property real lampPulse: 0.0
    implicitHeight: 72

    SequentialAnimation on lampPulse {
        running: root.active && root.animationAmount > 0.05
        loops: Animation.Infinite
        NumberAnimation { from: 0.0; to: 1.0; duration: 1800; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1.0; to: 0.0; duration: 2200; easing.type: Easing.InOutSine }
    }

    Rectangle {
        id: badge
        anchors.fill: parent
        radius: Math.max(1, width * 0.006)
        border.width: Math.max(1, width * 0.008)
        border.color: "#d6d8d2"
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#333532" }
            GradientStop { position: 0.035; color: "#e0e1dc" }
            GradientStop { position: 0.09; color: "#6b6e69" }
            GradientStop { position: 0.50; color: "#a8aaa5" }
            GradientStop { position: 0.91; color: "#5b5e59" }
            GradientStop { position: 0.97; color: "#dedfda" }
            GradientStop { position: 1.0; color: "#30322f" }
        }

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

        Rectangle {
            anchors {
                left: blueField.left
                right: blackField.right
                top: blueField.top
                bottom: blueField.bottom
            }
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#28ffffff" }
                GradientStop { position: 0.20; color: "#0cffffff" }
                GradientStop { position: 0.52; color: "#00000000" }
                GradientStop { position: 1.0; color: "#18000000" }
            }
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
            color: root.active ? "#e7e9e2" : "#9a9c97"
            opacity: root.active ? 0.62 + root.lampPulse * 0.38 : 0.72
            scale: root.active ? 0.94 + root.lampPulse * 0.09 : 1.0
        }


        Rectangle {
            anchors { left: parent.left; right: parent.right; top: parent.top }
            anchors.leftMargin: badge.width * 0.025
            anchors.rightMargin: badge.width * 0.025
            height: 1
            color: "#ffffff"
            opacity: 0.36
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: mouse => mouse.accepted = true
    }
}
