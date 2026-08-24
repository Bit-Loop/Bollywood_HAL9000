import QtQuick
import "."

Item {
    id: root
    property bool active: false
    property string state: "STANDBY"
    property real brightness: 0.9
    property real animationAmount: 0.72
    property real speakerLevel: 0.0
    property real motion: 0.0
    property real response: active
                            ? (state === "SPEAKING"
                               ? 0.68 + Math.min(1, speakerLevel) * 0.32
                               : state === "THINKING" || state === "TOOL RUNNING"
                                 ? 0.70 + motion * 0.10
                                 : state === "TRANSCRIBING" ? 0.62 + motion * 0.08 : 0.58)
                            : 0.18

    implicitWidth: 420
    implicitHeight: 420

    SequentialAnimation on motion {
        running: root.active && root.animationAmount > 0.05
        loops: Animation.Infinite
        NumberAnimation { from: 0.0; to: 1.0; duration: 1900; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1.0; to: 0.0; duration: 2300; easing.type: Easing.InOutSine }
    }

    Item {
        id: opticalAssembly
        anchors.centerIn: parent
        width: Math.min(root.width, root.height) * 0.98
        height: width
        scale: 0.997 + root.motion * 0.004 * root.animationAmount

        HalLensOptics {
            anchors.fill: parent
            energy: Math.min(1, root.response * root.brightness)
            animationAmount: root.animationAmount
            active: root.active
            currentState: root.state
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: mouse => mouse.accepted = true
    }
}
