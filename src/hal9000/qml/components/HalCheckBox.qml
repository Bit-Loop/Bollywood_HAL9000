import QtQuick
import QtQuick.Controls
import "."

CheckBox {
    id: control
    implicitHeight: 34
    spacing: 8
    indicator: Rectangle {
        implicitWidth: 30
        implicitHeight: 16
        x: control.leftPadding
        y: parent.height / 2 - height / 2
        radius: 8
        color: control.checked ? "#5d1012" : "#202120"
        border.width: 1
        border.color: control.checked ? "#a23538" : HalTheme.steelDark
        Rectangle {
            width: 12
            height: 12
            radius: 6
            y: 2
            x: control.checked ? parent.width - width - 2 : 2
            color: control.checked ? "#dfd7ce" : "#777974"
            Behavior on x { NumberAnimation { duration: 130 } }
        }
    }
    contentItem: Text {
        text: control.text
        color: HalTheme.muted
        font.family: HalTheme.controlFont
        font.pixelSize: 10
        verticalAlignment: Text.AlignVCenter
        leftPadding: control.indicator.width + control.spacing
    }
}
