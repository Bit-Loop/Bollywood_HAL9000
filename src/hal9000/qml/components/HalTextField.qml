import QtQuick
import QtQuick.Controls
import "."

TextField {
    id: control
    implicitHeight: 38
    color: HalTheme.text
    placeholderTextColor: HalTheme.dim
    selectionColor: HalTheme.redDeep
    selectedTextColor: HalTheme.text
    font.family: HalTheme.controlFont
    font.pixelSize: 11
    leftPadding: 10
    rightPadding: 10
    background: Rectangle {
        color: "#101111"
        border.width: 1
        border.color: control.activeFocus ? HalTheme.steel : HalTheme.line
        radius: HalTheme.radiusSmall
    }
}
