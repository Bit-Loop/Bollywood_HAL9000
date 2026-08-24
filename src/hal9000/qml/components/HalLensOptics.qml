import QtQuick

Item {
    id: root
    property real energy: 0.22
    property real animationAmount: 0.72
    property bool active: false
    property string currentState: "STANDBY"
    property int surfacePaintCount: opticsSurface.paintCount

    implicitWidth: 420
    implicitHeight: 420

    Canvas {
        id: opticsSurface
        objectName: "lensOpticsTexture"
        anchors.fill: parent
        property int paintCount: 0
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            paintCount += 1
            const ctx = getContext("2d")
            ctx.reset()
            const d = Math.min(width, height)
            const c = d / 2

            let shadow = ctx.createRadialGradient(c, c * 1.035, d * 0.38, c, c, d * 0.5)
            shadow.addColorStop(0, "rgba(0,0,0,0)")
            shadow.addColorStop(0.82, "rgba(0,0,0,0.45)")
            shadow.addColorStop(1, "rgba(0,0,0,0.92)")
            ctx.fillStyle = shadow
            ctx.beginPath(); ctx.arc(c, c, d * 0.495, 0, Math.PI * 2); ctx.fill()

            let outerMetal = ctx.createRadialGradient(d * 0.37, d * 0.31, d * 0.015, c, c, d * 0.49)
            outerMetal.addColorStop(0, "#f3f3ee")
            outerMetal.addColorStop(0.10, "#b8bab5")
            outerMetal.addColorStop(0.28, "#4c4f4d")
            outerMetal.addColorStop(0.53, "#171918")
            outerMetal.addColorStop(0.72, "#060707")
            outerMetal.addColorStop(0.87, "#9a9c97")
            outerMetal.addColorStop(0.94, "#e0e1dc")
            outerMetal.addColorStop(1, "#242625")
            ctx.fillStyle = outerMetal
            ctx.beginPath(); ctx.arc(c, c, d * 0.475, 0, Math.PI * 2); ctx.fill()

            ctx.lineWidth = Math.max(1, d * 0.008)
            ctx.strokeStyle = "rgba(255,255,250,0.74)"
            ctx.beginPath(); ctx.arc(c, c, d * 0.443, Math.PI * 0.92, Math.PI * 1.86); ctx.stroke()
            ctx.strokeStyle = "rgba(0,0,0,0.88)"
            ctx.beginPath(); ctx.arc(c, c, d * 0.443, -0.10, Math.PI * 0.88); ctx.stroke()

            let innerBezel = ctx.createRadialGradient(d * 0.42, d * 0.39, 0, c, c, d * 0.40)
            innerBezel.addColorStop(0, "#565957")
            innerBezel.addColorStop(0.20, "#1f2120")
            innerBezel.addColorStop(0.44, "#070808")
            innerBezel.addColorStop(0.77, "#020303")
            innerBezel.addColorStop(0.90, "#242625")
            innerBezel.addColorStop(1, "#090a0a")
            ctx.fillStyle = innerBezel
            ctx.beginPath(); ctx.arc(c, c, d * 0.405, 0, Math.PI * 2); ctx.fill()

            ctx.lineWidth = Math.max(2, d * 0.018)
            ctx.strokeStyle = "rgba(0,0,0,0.82)"
            ctx.beginPath(); ctx.arc(c, c, d * 0.37, 0, Math.PI * 2); ctx.stroke()
            ctx.lineWidth = Math.max(1, d * 0.004)
            ctx.strokeStyle = "rgba(203,205,199,0.22)"
            ctx.beginPath(); ctx.arc(c, c, d * 0.352, 0, Math.PI * 2); ctx.stroke()

            let glass = ctx.createRadialGradient(d * 0.42, d * 0.39, d * 0.012, c, c, d * 0.345)
            glass.addColorStop(0, "#3b0a0c")
            glass.addColorStop(0.16, "#190608")
            glass.addColorStop(0.42, "#090607")
            glass.addColorStop(0.73, "#030304")
            glass.addColorStop(0.91, "#100506")
            glass.addColorStop(1, "#000000")
            ctx.fillStyle = glass
            ctx.beginPath(); ctx.arc(c, c, d * 0.345, 0, Math.PI * 2); ctx.fill()

            ctx.save()
            ctx.translate(c, c)
            for (let blade = 0; blade < 7; blade++) {
                ctx.rotate(Math.PI * 2 / 7)
                ctx.fillStyle = blade % 2
                              ? "rgba(17,18,18,0.78)"
                              : "rgba(37,38,38,0.54)"
                ctx.beginPath()
                ctx.moveTo(0, 0)
                ctx.lineTo(-d * 0.13, -d * 0.30)
                ctx.quadraticCurveTo(d * 0.04, -d * 0.35, d * 0.18, -d * 0.25)
                ctx.closePath(); ctx.fill()
            }
            ctx.restore()

            ctx.lineWidth = Math.max(1, d * 0.003)
            ctx.strokeStyle = "rgba(255,255,255,0.11)"
            ctx.beginPath(); ctx.arc(c, c, d * 0.296, Math.PI * 1.02, Math.PI * 1.74); ctx.stroke()
            ctx.strokeStyle = "rgba(126,0,5,0.28)"
            ctx.beginPath(); ctx.arc(c, c, d * 0.265, -0.18, Math.PI * 0.72); ctx.stroke()

            ctx.fillStyle = "rgba(255,255,255,0.58)"
            ctx.beginPath(); ctx.ellipse(d * 0.43, d * 0.30, d * 0.105, d * 0.022, -0.10, 0, Math.PI * 2); ctx.fill()
            ctx.fillStyle = "rgba(255,255,255,0.25)"
            ctx.beginPath(); ctx.ellipse(d * 0.31, d * 0.39, d * 0.052, d * 0.013, -0.50, 0, Math.PI * 2); ctx.fill()
            ctx.fillStyle = "rgba(255,219,205,0.12)"
            ctx.beginPath(); ctx.ellipse(d * 0.67, d * 0.67, d * 0.115, d * 0.028, -0.76, 0, Math.PI * 2); ctx.fill()
        }
    }

    Item {
        id: internalReflection
        anchors.fill: parent
        rotation: 0
        opacity: 0.42 + root.energy * 0.34

        Canvas {
            anchors.fill: parent
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const d = Math.min(width, height)
                ctx.strokeStyle = "rgba(255,232,224,0.26)"
                ctx.lineWidth = Math.max(1, d * 0.008)
                ctx.beginPath(); ctx.arc(d / 2, d / 2, d * 0.245, Math.PI * 1.20, Math.PI * 1.55); ctx.stroke()
                ctx.strokeStyle = "rgba(255,255,255,0.14)"
                ctx.lineWidth = Math.max(1, d * 0.005)
                ctx.beginPath(); ctx.arc(d / 2, d / 2, d * 0.305, Math.PI * 1.78, Math.PI * 1.97); ctx.stroke()
            }
        }

        SequentialAnimation on rotation {
            running: root.active && root.animationAmount > 0.05
            loops: Animation.Infinite
            NumberAnimation { from: -0.7; to: 0.8; duration: 4200; easing.type: Easing.InOutSine }
            NumberAnimation { from: 0.8; to: -0.7; duration: 5200; easing.type: Easing.InOutSine }
        }
    }

    Canvas {
        anchors.centerIn: parent
        width: parent.width * 0.36
        height: width
        opacity: 0.18 + root.energy * 0.82
        scale: 0.91 + root.energy * 0.12
        onWidthChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const c = width / 2
            let glow = ctx.createRadialGradient(c, c, 0, c, c, c)
            glow.addColorStop(0, "rgba(255,242,198,1)")
            glow.addColorStop(0.10, "rgba(255,105,35,0.98)")
            glow.addColorStop(0.28, "rgba(230,5,10,0.86)")
            glow.addColorStop(0.58, "rgba(126,0,4,0.42)")
            glow.addColorStop(1, "rgba(74,0,2,0)")
            ctx.fillStyle = glow
            ctx.fillRect(0, 0, width, height)
        }
        Behavior on opacity { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
    }

    Canvas {
        anchors.centerIn: parent
        width: parent.width * 0.092
        height: width
        opacity: Math.max(0.72, root.energy)
        scale: 0.94 + root.energy * 0.08
        onWidthChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const c = width / 2
            let core = ctx.createRadialGradient(c * 0.88, c * 0.80, 0, c, c, c)
            core.addColorStop(0, "#fff8d8")
            core.addColorStop(0.22, "#ffb13e")
            core.addColorStop(0.52, "#ff2b18")
            core.addColorStop(0.80, "#a70005")
            core.addColorStop(1, "rgba(50,0,1,0)")
            ctx.fillStyle = core
            ctx.fillRect(0, 0, width, height)
        }
        Behavior on opacity { NumberAnimation { duration: 90; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }
}
