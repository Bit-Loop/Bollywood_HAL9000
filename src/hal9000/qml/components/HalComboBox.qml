import QtQuick
import QtQuick.Controls
import "."

ComboBox {
    id: control
    implicitHeight: 38
    readonly property real popupAvailableWidth: control.Window.window
                                                ? Math.max(control.width, control.Window.window.width - 24)
                                                : 720
    property real popupMaximumWidth: Math.max(
                                         width,
                                         Math.min(popupAvailableWidth, Math.max(420, width * 1.8))
                                     )
    leftPadding: 10
    rightPadding: 30

    function labelFor(modelData) {
        if (typeof modelData === "object" && modelData !== null) {
            if (control.textRole && modelData[control.textRole] !== undefined)
                return String(modelData[control.textRole])
            if (modelData.name !== undefined)
                return String(modelData.name)
        }
        return String(modelData)
    }

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
        width: control.popupMaximumWidth
        height: typeof modelData === "object" && modelData !== null && modelData.modelLabel ? 48 : 34
        contentItem: Column {
            spacing: 2
            Text {
                width: parent.width
                text: typeof modelData === "object" && modelData !== null && modelData.providerName
                      ? String(modelData.providerName).toUpperCase() : control.labelFor(modelData)
                color: HalTheme.text
                font.family: HalTheme.controlFont
                font.pixelSize: 10
                elide: Text.ElideRight
            }
            Text {
                width: parent.width
                visible: typeof modelData === "object" && modelData !== null && !!modelData.modelLabel
                text: visible ? String(modelData.modelLabel) : ""
                color: HalTheme.dim
                font.family: HalTheme.controlFont
                font.pixelSize: 9
                elide: Text.ElideMiddle
            }
        }
        background: Rectangle { color: parent.highlighted ? "#292b29" : "#141515" }
    }

    popup: Popup {
        x: {
            if (!control.Window.window)
                return 0
            const screenPoint = control.mapToItem(null, 0, 0)
            return Math.min(0, control.Window.window.width - screenPoint.x - width - 12)
        }
        y: control.height + 2
        width: control.popupMaximumWidth
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
