import QtQuick

Item {
    id: root
    property real meter: 0.0
    property bool energized: false
    property int surfacePaintCount: grilleSurface.paintCount
    clip: true

    Canvas {
        id: grilleSurface
        objectName: "speakerGrilleTexture"
        anchors.fill: parent
        property int paintCount: 0
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            paintCount += 1
            const ctx = getContext("2d")
            ctx.reset()
            const bg = ctx.createLinearGradient(0, 0, width, 0)
            bg.addColorStop(0, "#4f514e")
            bg.addColorStop(0.035, "#dedfda")
            bg.addColorStop(0.18, "#858783")
            bg.addColorStop(0.43, "#d8d9d4")
            bg.addColorStop(0.61, "#969894")
            bg.addColorStop(0.83, "#e0e1dc")
            bg.addColorStop(0.965, "#777975")
            bg.addColorStop(1, "#3b3d3b")
            ctx.fillStyle = bg
            ctx.fillRect(0, 0, width, height)

            for (let brush = 2; brush < width; brush += 5) {
                const alpha = brush % 20 === 2 ? 0.10 : 0.035
                ctx.strokeStyle = "rgba(255,255,250," + alpha + ")"
                ctx.lineWidth = 0.5
                ctx.beginPath(); ctx.moveTo(brush + 0.5, 1); ctx.lineTo(brush + 0.5, height - 1); ctx.stroke()
            }

            const marginX = Math.max(8, width * 0.022)
            const marginY = Math.max(9, height * 0.065)
            const gapX = Math.max(6, Math.min(11, width / 52))
            const gapY = Math.max(6, Math.min(10, height / 22))
            const holeX = Math.max(1.25, Math.min(2.35, gapX * 0.23))
            const holeY = holeX * 0.72
            let row = 0
            for (let y = marginY; y < height - marginY; y += gapY) {
                const offset = row % 2 ? gapX / 2 : 0
                for (let x = marginX + offset; x < width - marginX; x += gapX) {
                    ctx.fillStyle = "rgba(255,255,250,0.42)"
                    ctx.beginPath(); ctx.ellipse(x, y - 0.7, holeX * 1.25, holeY * 1.2, 0, 0, Math.PI * 2); ctx.fill()
                    ctx.fillStyle = "#090a0a"
                    ctx.beginPath(); ctx.ellipse(x, y, holeX * 1.25, holeY, 0, 0, Math.PI * 2); ctx.fill()
                    ctx.fillStyle = "rgba(0,0,0,0.64)"
                    ctx.beginPath(); ctx.ellipse(x, y + 0.45, holeX * 0.92, holeY * 0.66, 0, 0, Math.PI * 2); ctx.fill()
                }
                row += 1
            }
        }
    }

    Item {
        id: meterClip
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: root.energized ? root.height * Math.min(0.86, Math.max(0.04, root.meter * 0.86)) : 0
        clip: true
        opacity: root.energized ? 0.86 : 0

        Canvas {
            anchors.bottom: parent.bottom
            width: root.width
            height: root.height
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const marginX = Math.max(8, width * 0.022)
                const marginY = Math.max(9, height * 0.065)
                const gapX = Math.max(6, Math.min(11, width / 52))
                const gapY = Math.max(6, Math.min(10, height / 22))
                const holeX = Math.max(1.25, Math.min(2.35, gapX * 0.23))
                const holeY = holeX * 0.72
                let row = 0
                for (let y = marginY; y < height - marginY; y += gapY) {
                    const offset = row % 2 ? gapX / 2 : 0
                    for (let x = marginX + offset; x < width - marginX; x += gapX) {
                        ctx.fillStyle = "rgba(157,8,11,0.92)"
                        ctx.beginPath(); ctx.ellipse(x, y, holeX * 1.15, holeY * 0.88, 0, 0, Math.PI * 2); ctx.fill()
                    }
                    row += 1
                }
            }
        }

        Behavior on height { NumberAnimation { duration: 95; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    }

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.width: Math.max(1, width * 0.0025)
        border.color: "#ecece7"
        opacity: 0.74
    }
}
