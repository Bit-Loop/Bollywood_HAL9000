import QtQuick
import QtQuick.Controls
import "."

Slider {
    id: control
    implicitHeight: 30

    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth
        height: 4
        radius: 1
        color: "#252625"
        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            color: HalTheme.steel
        }
    }
    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 13
        height: 20
        radius: 2
        color: control.pressed ? "#d0d1ca" : "#8b8d87"
        border.width: 1
        border.color: "#30312f"
    }
}
