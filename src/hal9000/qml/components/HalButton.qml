import QtQuick
import QtQuick.Controls
import "."

Button {
    id: control
    property bool danger: false
    implicitHeight: 40
    implicitWidth: Math.max(92, contentItem.implicitWidth + 28)
    padding: 10

    contentItem: Text {
        text: control.text
        color: control.enabled ? HalTheme.text : HalTheme.dim
        font.family: HalTheme.controlFont
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 0.8
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        color: control.down ? "#292a28" : control.hovered ? "#242624" : "#1b1c1b"
        border.width: 1
        border.color: control.activeFocus ? (control.danger ? HalTheme.red : HalTheme.steelLight)
                                          : (control.danger ? HalTheme.redDeep : HalTheme.steelDark)
        radius: HalTheme.radiusSmall
        Rectangle {
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 1
            color: control.danger ? "#7c1113" : "#50524f"
            opacity: 0.65
        }
    }
}
