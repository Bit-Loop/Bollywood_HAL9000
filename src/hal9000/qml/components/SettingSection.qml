import QtQuick
import "."

Item {
    id: root
    property string title: ""
    property string detail: ""
    implicitHeight: heading.implicitHeight + (detail ? description.implicitHeight + 7 : 0) + 10
    width: parent ? parent.width : 600

    Text {
        id: heading
        anchors { left: parent.left; right: parent.right; top: parent.top }
        text: root.title.toUpperCase()
        color: HalTheme.text
        font.family: HalTheme.controlFont
        font.pixelSize: 12
        font.weight: Font.Bold
        font.letterSpacing: 1.4
    }
    Text {
        id: description
        anchors { left: parent.left; right: parent.right; top: heading.bottom; topMargin: 7 }
        text: root.detail
        visible: root.detail.length > 0
        color: HalTheme.dim
        font.family: HalTheme.displayFont
        font.pixelSize: 11
        wrapMode: Text.Wrap
    }
    Rectangle {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 1
        color: HalTheme.line
    }
}
