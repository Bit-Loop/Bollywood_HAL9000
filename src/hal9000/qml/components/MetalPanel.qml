import QtQuick
import "."

Item {
    id: root
    property color baseColor: HalTheme.panel

    Rectangle {
        anchors.fill: parent
        color: root.baseColor
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#020202" }
            GradientStop { position: 0.18; color: "#070707" }
            GradientStop { position: 0.5; color: root.baseColor }
            GradientStop { position: 0.82; color: "#070707" }
            GradientStop { position: 1.0; color: "#020202" }
        }
    }

    Canvas {
        anchors.fill: parent
        opacity: 0.09
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            for (let x = 1; x < width; x += 3) {
                const alpha = (x % 11 === 0) ? 0.09 : 0.025
                ctx.strokeStyle = "rgba(255,255,255," + alpha + ")"
                ctx.beginPath()
                ctx.moveTo(x + 0.5, 0)
                ctx.lineTo(x + 0.5, height)
                ctx.stroke()
            }
        }
    }
}
