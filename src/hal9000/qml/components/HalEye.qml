import QtQuick
import "."

Item {
    id: root
    property bool active: false
    property string state: "STANDBY"
    property real brightness: 0.9
    property real animationAmount: 0.72
    property real speakerLevel: 0.0
    readonly property real pulse: state === "SPEAKING" ? 0.72 + speakerLevel * 0.24
                                : state === "TRANSCRIBING" ? subtlePulse
                                : 0.86
    property real subtlePulse: 0.83

    implicitWidth: 420
    implicitHeight: 420

    NumberAnimation on subtlePulse {
        from: 0.78
        to: 0.9
        duration: 2100
        loops: Animation.Infinite
        easing.type: Easing.InOutSine
        running: root.active && root.animationAmount > 0.05
    }

    Item {
        id: lens
        width: Math.min(root.width, root.height) * 0.82
        height: width
        anchors.centerIn: parent
        scale: root.active && (root.state === "THINKING" || root.state === "TOOL RUNNING")
               ? 1.0 + root.subtlePulse * 0.006 * root.animationAmount : 1.0

        Behavior on scale {
            NumberAnimation { duration: 1200; easing.type: Easing.InOutSine }
        }

        Canvas {
            id: optics
            anchors.fill: parent
            property real energy: root.active ? root.brightness * root.pulse : 0.0
            onEnergyChanged: requestPaint()
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const w = width
                const c = w / 2

                let metal = ctx.createRadialGradient(c * 0.82, c * 0.72, w * 0.04, c, c, c)
                metal.addColorStop(0, "#9b9d98")
                metal.addColorStop(0.36, "#555754")
                metal.addColorStop(0.68, "#181919")
                metal.addColorStop(0.86, "#777873")
                metal.addColorStop(1, "#090909")
                ctx.fillStyle = metal
                ctx.beginPath(); ctx.arc(c, c, c * 0.98, 0, Math.PI * 2); ctx.fill()

                ctx.strokeStyle = "rgba(210,211,203,0.54)"
                ctx.lineWidth = Math.max(1, w * 0.008)
                ctx.beginPath(); ctx.arc(c, c, c * 0.91, 0, Math.PI * 2); ctx.stroke()

                let bezel = ctx.createRadialGradient(c, c, w * 0.12, c, c, w * 0.39)
                bezel.addColorStop(0, "#090707")
                bezel.addColorStop(0.58, "#111111")
                bezel.addColorStop(0.82, "#3c3d3a")
                bezel.addColorStop(1, "#090a0a")
                ctx.fillStyle = bezel
                ctx.beginPath(); ctx.arc(c, c, w * 0.39, 0, Math.PI * 2); ctx.fill()

                const energy = optics.energy
                let glass = ctx.createRadialGradient(c * 0.94, c * 0.89, w * 0.018, c, c, w * 0.315)
                if (energy > 0.001) {
                    glass.addColorStop(0, "rgba(255,241,215," + (0.94 * energy) + ")")
                    glass.addColorStop(0.08, "rgba(255,91,57," + energy + ")")
                    glass.addColorStop(0.26, "rgba(214,8,14," + (0.96 * energy) + ")")
                    glass.addColorStop(0.64, "rgba(104,0,4," + (0.9 * energy) + ")")
                    glass.addColorStop(1, "#170002")
                } else {
                    glass.addColorStop(0, "#270607")
                    glass.addColorStop(0.3, "#160203")
                    glass.addColorStop(0.72, "#090101")
                    glass.addColorStop(1, "#020202")
                }
                ctx.fillStyle = glass
                ctx.beginPath(); ctx.arc(c, c, w * 0.305, 0, Math.PI * 2); ctx.fill()

                ctx.strokeStyle = energy > 0 ? "rgba(244,42,44," + (0.42 * energy) + ")" : "rgba(90,40,40,0.18)"
                ctx.lineWidth = w * 0.018
                ctx.beginPath(); ctx.arc(c, c, w * 0.286, 0, Math.PI * 2); ctx.stroke()

                let emitter = ctx.createRadialGradient(c * 0.96, c * 0.94, 0, c, c, w * 0.105)
                emitter.addColorStop(0, energy > 0 ? "rgba(255,255,242," + energy + ")" : "#1a0505")
                emitter.addColorStop(0.15, energy > 0 ? "rgba(255,85,52," + energy + ")" : "#100202")
                emitter.addColorStop(0.55, energy > 0 ? "rgba(195,0,6," + energy + ")" : "#070101")
                emitter.addColorStop(1, "#030000")
                ctx.fillStyle = emitter
                ctx.beginPath(); ctx.arc(c, c, w * 0.108, 0, Math.PI * 2); ctx.fill()

                ctx.fillStyle = energy > 0 ? "rgba(255,255,255," + (0.46 * energy) + ")" : "rgba(255,255,255,0.055)"
                ctx.beginPath(); ctx.ellipse(c * 0.88, c * 0.78, w * 0.075, w * 0.025, -0.55, 0, Math.PI * 2); ctx.fill()

                ctx.strokeStyle = "rgba(255,255,255,0.09)"
                ctx.lineWidth = Math.max(1, w * 0.004)
                ctx.beginPath(); ctx.arc(c, c, w * 0.245, Math.PI * 1.08, Math.PI * 1.72); ctx.stroke()
            }
        }

        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.61
            height: width
            radius: width / 2
            color: "transparent"
            border.width: Math.max(1, width * 0.005)
            border.color: root.active ? "#6c080a" : "#210607"
            opacity: 0.7
        }
    }
}
