import QtQuick
import QtQuick.Controls
import "."

ProgressBar {
    id: control
    implicitHeight: 8
    background: Rectangle {
        implicitWidth: 200
        implicitHeight: 8
        color: "#090a0a"
        border.width: 1
        border.color: HalTheme.line
    }
    contentItem: Item {
        clip: true
        Rectangle {
            width: control.indeterminate ? parent.width * 0.28 : control.visualPosition * parent.width
            height: parent.height
            x: control.indeterminate ? pulse * (parent.width - width) : 0
            color: HalTheme.redDeep
            property real pulse: 0
            NumberAnimation on pulse {
                from: 0
                to: 1
                duration: 1100
                loops: Animation.Infinite
                running: control.indeterminate
            }
        }
    }
}
