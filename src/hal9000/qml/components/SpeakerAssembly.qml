import QtQuick
import "."

Item {
    id: root
    property bool drawerOpen: false
    property real speakerLevel: 0.0
    property bool visualizationEnabled: true
    property real animationAmount: 0.72
    property string currentState: "STANDBY"
    property string ttsEngine: "AUTO"
    property bool microphoneMuted: false
    signal sendText(string text)
    signal microphoneRequested()
    signal stopGenerationRequested()
    signal stopSpeechRequested()
    signal approvalAnswered(string requestId, string choice)
    signal grilleClicked()
    signal interaction()

    property real reveal: drawerOpen ? 1.0 : 0.0
    Behavior on reveal {
        NumberAnimation {
            duration: 520 * Math.max(0.15, root.animationAmount)
            easing.type: Easing.OutCubic
        }
    }

    Rectangle {
        anchors.fill: parent
        color: "#090a0a"
        border.width: Math.max(1, width * 0.004)
        border.color: "#b9bab5"
        radius: Math.max(1, width * 0.004)
    }

    ManualDrawer {
        id: drawer
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            margins: Math.max(5, parent.width * 0.012)
        }
        height: Math.max(0, parent.height * 0.76 * root.reveal - 8)
        opacity: Math.max(0, (root.reveal - 0.15) / 0.85)
        enabled: root.drawerOpen
        currentState: root.currentState
        ttsEngine: root.ttsEngine
        microphoneMuted: root.microphoneMuted
        onSendText: text => root.sendText(text)
        onMicrophoneRequested: root.microphoneRequested()
        onStopGenerationRequested: root.stopGenerationRequested()
        onStopSpeechRequested: root.stopSpeechRequested()
        onApprovalAnswered: (requestId, choice) => root.approvalAnswered(requestId, choice)
        onInteraction: root.interaction()
    }

    Item {
        id: face
        x: Math.max(5, root.width * 0.012)
        y: x
        width: root.width - x * 2
        height: root.height - x * 2 - root.height * 0.73 * root.reveal
        transform: Rotation {
            origin.x: face.width / 2
            origin.y: 0
            axis { x: 1; y: 0; z: 0 }
            angle: -6 * root.reveal * root.animationAmount
        }

        Behavior on height {
            NumberAnimation {
                duration: 520 * Math.max(0.15, root.animationAmount)
                easing.type: Easing.OutCubic
            }
        }

        Canvas {
            id: grille
            anchors.fill: parent
            property real meter: root.visualizationEnabled ? root.speakerLevel : 0.0
            onMeterChanged: requestPaint()
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const bg = ctx.createLinearGradient(0, 0, width, 0)
                bg.addColorStop(0, "#656762")
                bg.addColorStop(0.05, "#d2d3ce")
                bg.addColorStop(0.24, "#9b9d98")
                bg.addColorStop(0.52, "#d7d8d3")
                bg.addColorStop(0.78, "#92948f")
                bg.addColorStop(0.96, "#d6d7d2")
                bg.addColorStop(1, "#5c5e5a")
                ctx.fillStyle = bg
                ctx.fillRect(0, 0, width, height)
                ctx.strokeStyle = "#e2e3de"
                ctx.lineWidth = Math.max(1, width * 0.003)
                ctx.strokeRect(1, 1, width - 2, height - 2)

                const marginX = Math.max(7, width * 0.025)
                const marginY = Math.max(9, height * 0.07)
                const gapX = Math.max(6, Math.min(11, width / 48))
                const gapY = Math.max(6, Math.min(10, height / 22))
                const radius = Math.max(1.1, Math.min(2.2, gapX * 0.22))
                let row = 0
                for (let y = marginY; y < height - marginY; y += gapY) {
                    const offset = row % 2 ? gapX / 2 : 0
                    for (let x = marginX + offset; x < width - marginX; x += gapX) {
                        const position = 1 - y / Math.max(1, height)
                        const active = root.visualizationEnabled && root.currentState === "SPEAKING"
                                     && position < grille.meter * 0.82
                        ctx.fillStyle = active ? "rgba(99,14,15,0.86)" : "#111212"
                        ctx.beginPath(); ctx.ellipse(x, y, radius * 1.35, radius, 0, 0, Math.PI * 2); ctx.fill()
                        ctx.strokeStyle = "rgba(255,255,250,0.38)"
                        ctx.lineWidth = 0.5
                        ctx.stroke()
                    }
                    row++
                }
            }
        }

        Rectangle {
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: Math.max(4, parent.height * 0.025)
            color: "#d0d1cc"
            border.width: 1
            border.color: "#50524f"
        }

        Rectangle {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: Math.max(5, parent.height * 0.03)
            color: "#b9bab5"
            border.width: 1
            border.color: "#3b3d3b"
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            cursorShape: Qt.PointingHandCursor
            onClicked: root.grilleClicked()
        }
    }

    Rectangle {
        width: Math.min(84, root.width * 0.16)
        height: 6
        anchors.horizontalCenter: parent.horizontalCenter
        y: face.y + face.height - 3
        color: "#d0d1cc"
        border.width: 1
        border.color: "#171817"
        radius: 1
        opacity: 0.35 + root.reveal * 0.55
        MouseArea {
            anchors.fill: parent
            anchors.margins: -12
            cursorShape: Qt.PointingHandCursor
            onClicked: root.grilleClicked()
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: mouse => mouse.accepted = true
    }

    function focusPrompt() {
        drawer.focusPrompt()
    }

    function submitPrompt() {
        drawer.submit()
    }
}
