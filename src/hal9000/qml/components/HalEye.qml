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
        width: Math.min(root.width, root.height) * 0.96
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
            // The standby lamp keeps the tiny red emitter visible like the
            // physical HAL panel, while active states energize the wider glow.
            property real energy: root.active
                                  ? root.brightness * root.pulse
                                  : root.brightness * 0.22
            onEnergyChanged: requestPaint()
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const w = width
                const c = w / 2

                let metal = ctx.createRadialGradient(c * 0.77, c * 0.67, w * 0.03, c, c, c)
                metal.addColorStop(0, "#e3e4df")
                metal.addColorStop(0.12, "#a9aaa6")
                metal.addColorStop(0.49, "#303230")
                metal.addColorStop(0.73, "#111212")
                metal.addColorStop(0.88, "#969893")
                metal.addColorStop(0.96, "#d0d1cc")
                metal.addColorStop(1, "#242625")
                ctx.fillStyle = metal
                ctx.beginPath(); ctx.arc(c, c, c * 0.98, 0, Math.PI * 2); ctx.fill()

                ctx.strokeStyle = "rgba(240,241,236,0.74)"
                ctx.lineWidth = Math.max(1, w * 0.009)
                ctx.beginPath(); ctx.arc(c, c, c * 0.91, 0, Math.PI * 2); ctx.stroke()
                ctx.strokeStyle = "rgba(10,10,10,0.92)"
                ctx.lineWidth = Math.max(2, w * 0.027)
                ctx.beginPath(); ctx.arc(c, c, c * 0.82, 0, Math.PI * 2); ctx.stroke()

                let bezel = ctx.createRadialGradient(c * 0.92, c * 0.86, w * 0.03, c, c, w * 0.405)
                bezel.addColorStop(0, "#180809")
                bezel.addColorStop(0.24, "#090607")
                bezel.addColorStop(0.62, "#050506")
                bezel.addColorStop(0.84, "#18191a")
                bezel.addColorStop(1, "#030303")
                ctx.fillStyle = bezel
                ctx.beginPath(); ctx.arc(c, c, w * 0.405, 0, Math.PI * 2); ctx.fill()

                const energy = optics.energy
                let glass = ctx.createRadialGradient(c * 0.96, c * 0.92, w * 0.015, c, c, w * 0.34)
                glass.addColorStop(0, energy > 0 ? "rgba(96,8,10," + (0.42 * energy) + ")" : "#0c0506")
                glass.addColorStop(0.26, "#100607")
                glass.addColorStop(0.58, "#080607")
                glass.addColorStop(0.84, "#030304")
                glass.addColorStop(1, "#000000")
                ctx.fillStyle = glass
                ctx.beginPath(); ctx.arc(c, c, w * 0.34, 0, Math.PI * 2); ctx.fill()

                // Dark iris blades make the eye read as an optical assembly,
                // rather than a large flat red disk.
                ctx.fillStyle = "rgba(35,36,36,0.62)"
                ctx.beginPath(); ctx.moveTo(c, c); ctx.lineTo(w * 0.18, w * 0.30); ctx.lineTo(w * 0.24, w * 0.18); ctx.closePath(); ctx.fill()
                ctx.beginPath(); ctx.moveTo(c, c); ctx.lineTo(w * 0.77, w * 0.18); ctx.lineTo(w * 0.84, w * 0.31); ctx.closePath(); ctx.fill()
                ctx.fillStyle = "rgba(5,5,6,0.72)"
                ctx.beginPath(); ctx.moveTo(c, c); ctx.lineTo(w * 0.83, w * 0.70); ctx.lineTo(w * 0.72, w * 0.82); ctx.closePath(); ctx.fill()

                let glow = ctx.createRadialGradient(c, c, 0, c, c, w * 0.15)
                glow.addColorStop(0, energy > 0 ? "rgba(255,46,30," + (0.92 * energy) + ")" : "rgba(55,0,2,0.3)")
                glow.addColorStop(0.42, energy > 0 ? "rgba(188,0,5," + (0.5 * energy) + ")" : "rgba(25,0,1,0.16)")
                glow.addColorStop(1, "rgba(80,0,2,0)")
                ctx.fillStyle = glow
                ctx.beginPath(); ctx.arc(c, c, w * 0.15, 0, Math.PI * 2); ctx.fill()

                let emitter = ctx.createRadialGradient(c * 0.96, c * 0.92, 0, c, c, w * 0.065)
                emitter.addColorStop(0, energy > 0 ? "rgba(255,250,218," + energy + ")" : "#2a0907")
                emitter.addColorStop(0.18, energy > 0 ? "rgba(255,116,45," + energy + ")" : "#160203")
                emitter.addColorStop(0.58, energy > 0 ? "rgba(224,4,9," + energy + ")" : "#080101")
                emitter.addColorStop(1, "#030000")
                ctx.fillStyle = emitter
                ctx.beginPath(); ctx.arc(c, c, w * 0.066, 0, Math.PI * 2); ctx.fill()

                // A pinpoint incandescent core stays legible even while the
                // wider optic is idling at standby intensity.
                const coreLevel = Math.max(0.68, energy)
                let core = ctx.createRadialGradient(c, c, 0, c, c, w * 0.022)
                core.addColorStop(0, "rgba(255,238,178," + coreLevel + ")")
                core.addColorStop(0.28, "rgba(255,80,28," + coreLevel + ")")
                core.addColorStop(0.72, "rgba(210,0,5," + (coreLevel * 0.8) + ")")
                core.addColorStop(1, "rgba(90,0,2,0)")
                ctx.fillStyle = core
                ctx.beginPath(); ctx.arc(c, c, w * 0.022, 0, Math.PI * 2); ctx.fill()

                ctx.fillStyle = "rgba(255,255,255,0.62)"
                ctx.beginPath(); ctx.ellipse(w * 0.43, w * 0.31, w * 0.105, w * 0.023, -0.12, 0, Math.PI * 2); ctx.fill()
                ctx.fillStyle = "rgba(255,255,255,0.28)"
                ctx.beginPath(); ctx.ellipse(w * 0.31, w * 0.39, w * 0.055, w * 0.014, -0.5, 0, Math.PI * 2); ctx.fill()

                ctx.strokeStyle = "rgba(255,255,255,0.13)"
                ctx.lineWidth = Math.max(1, w * 0.005)
                ctx.beginPath(); ctx.arc(c, c, w * 0.31, Math.PI * 1.04, Math.PI * 1.72); ctx.stroke()
            }
        }

        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.68
            height: width
            radius: width / 2
            color: "transparent"
            border.width: Math.max(1, width * 0.005)
            border.color: root.active ? "#451012" : "#1b0809"
            opacity: 0.46
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: mouse => mouse.accepted = true
    }
}
