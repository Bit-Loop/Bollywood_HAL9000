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
        color: "#0a0b0b"
        border.width: Math.max(1, width * 0.003)
        border.color: "#4b4d49"
        radius: Math.max(2, width * 0.008)
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
                bg.addColorStop(0, "#101111")
                bg.addColorStop(0.18, "#292a29")
                bg.addColorStop(0.5, "#1c1d1c")
                bg.addColorStop(0.82, "#2a2b29")
                bg.addColorStop(1, "#0d0e0e")
                ctx.fillStyle = bg
                ctx.fillRect(0, 0, width, height)
                ctx.strokeStyle = "#555752"
                ctx.lineWidth = Math.max(1, width * 0.003)
                ctx.strokeRect(1, 1, width - 2, height - 2)

                const margin = Math.max(14, width * 0.04)
                const gapX = Math.max(8, Math.min(15, width / 62))
                const gapY = Math.max(8, Math.min(15, height / 30))
                const radius = Math.max(1.2, Math.min(2.8, gapX * 0.24))
                let row = 0
                for (let y = margin; y < height - margin; y += gapY) {
                    const offset = row % 2 ? gapX / 2 : 0
                    for (let x = margin + offset; x < width - margin; x += gapX) {
                        const position = 1 - y / Math.max(1, height)
                        const active = root.visualizationEnabled && root.currentState === "SPEAKING"
                                     && position < grille.meter * 0.82
                        ctx.fillStyle = active ? "rgba(118,22,22,0.58)" : "#050606"
                        ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill()
                        ctx.strokeStyle = "rgba(180,182,175,0.1)"
                        ctx.lineWidth = 0.5
                        ctx.stroke()
                    }
                    row++
                }
            }
        }

        Rectangle {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: Math.max(5, parent.height * 0.025)
            color: "#080909"
            border.width: 1
            border.color: "#3c3e3b"
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
        color: "#737570"
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

    function focusPrompt() {
        drawer.focusPrompt()
    }

    function submitPrompt() {
        drawer.submit()
    }
}
