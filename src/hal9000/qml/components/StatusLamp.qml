import QtQuick
import "."

Item {
    id: root
    property string status: "offline"
    property int diameter: 8
    implicitWidth: diameter
    implicitHeight: diameter

    readonly property bool good: ["connected", "ready", "pass", "wake", "record", "speaking"].indexOf(status.toLowerCase()) >= 0
    readonly property bool warning: ["starting", "connecting", "reconnecting", "loading", "downloading", "warn", "benchmarking", "transcribing", "synthesizing"].indexOf(status.toLowerCase()) >= 0

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: root.good ? "#aeb4a7" : root.warning ? HalTheme.amber : HalTheme.redDeep
        border.width: 1
        border.color: "#111"
    }
}
