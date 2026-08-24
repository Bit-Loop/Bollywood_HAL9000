import QtQuick
import QtQuick.Controls
import "."

ComboBox {
    id: control
    implicitHeight: 38
    leftPadding: 10
    rightPadding: 30

    contentItem: Text {
        text: control.displayText
        color: HalTheme.text
        font.family: HalTheme.controlFont
        font.pixelSize: 10
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Canvas {
        x: control.width - width - 10
        y: (control.height - height) / 2
        width: 10
        height: 6
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = HalTheme.muted
            ctx.lineWidth = 1.3
            ctx.beginPath()
            ctx.moveTo(1, 1)
            ctx.lineTo(width / 2, height - 1)
            ctx.lineTo(width - 1, 1)
            ctx.stroke()
        }
    }

    background: Rectangle {
        color: control.down ? "#202222" : "#101111"
        border.width: 1
        border.color: control.activeFocus ? HalTheme.steel : HalTheme.line
        radius: HalTheme.radiusSmall
    }

    delegate: ItemDelegate {
        required property var modelData
        width: control.width
        height: 34
        contentItem: Text {
            text: typeof modelData === "object" && modelData.name !== undefined
                  ? modelData.name : String(modelData)
            color: HalTheme.text
            font.family: HalTheme.controlFont
            font.pixelSize: 10
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle { color: parent.highlighted ? "#292b29" : "#141515" }
    }

    popup: Popup {
        y: control.height + 2
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight + 2, 280)
        padding: 1
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle {
            color: "#141515"
            border.width: 1
            border.color: HalTheme.steelDark
        }
    }
}
