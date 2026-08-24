import QtQuick
import "."

FocusScope {
    id: control
    property int from: 0
    property int to: 100
    property int stepSize: 1
    property int value: 0
    signal valueModified()
    implicitHeight: 38
    activeFocusOnTab: true

    Keys.onLeftPressed: adjust(-stepSize)
    Keys.onDownPressed: adjust(-stepSize)
    Keys.onRightPressed: adjust(stepSize)
    Keys.onUpPressed: adjust(stepSize)

    Rectangle {
        anchors.fill: parent
        color: "#101111"
        border.width: 1
        border.color: control.activeFocus ? HalTheme.steel : HalTheme.line
        radius: HalTheme.radiusSmall
    }
    Text {
        anchors { left: parent.left; right: decrement.left; top: parent.top; bottom: parent.bottom }
        text: String(control.value)
        color: HalTheme.text
        font.family: HalTheme.controlFont
        font.pixelSize: 10
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    Rectangle {
        id: decrement
        anchors { right: increment.left; top: parent.top; bottom: parent.bottom }
        width: 30
        color: decrementArea.pressed ? "#292b29" : "#191a1a"
        border.width: 1
        border.color: HalTheme.line
        Text { anchors.centerIn: parent; text: "−"; color: HalTheme.muted; font.family: HalTheme.controlFont }
        MouseArea {
            id: decrementArea
            anchors.fill: parent
            onClicked: { control.forceActiveFocus(); control.adjust(-control.stepSize) }
        }
    }
    Rectangle {
        id: increment
        anchors { right: parent.right; top: parent.top; bottom: parent.bottom }
        width: 30
        color: incrementArea.pressed ? "#292b29" : "#191a1a"
        border.width: 1
        border.color: HalTheme.line
        Text { anchors.centerIn: parent; text: "+"; color: HalTheme.muted; font.family: HalTheme.controlFont }
        MouseArea {
            id: incrementArea
            anchors.fill: parent
            onClicked: { control.forceActiveFocus(); control.adjust(control.stepSize) }
        }
    }

    function adjust(delta) {
        const next = Math.max(from, Math.min(to, value + delta))
        if (next === value)
            return
        value = next
        valueModified()
    }
}
