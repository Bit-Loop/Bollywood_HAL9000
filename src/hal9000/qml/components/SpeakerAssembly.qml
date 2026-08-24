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
            easing.type: Easing.InOutCubic
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: Math.max(1, width * 0.004)
        border.width: Math.max(1, width * 0.004)
        border.color: "#d7d8d3"
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.00; color: "#303230" }
            GradientStop { position: 0.025; color: "#d4d5d0" }
            GradientStop { position: 0.065; color: "#71736f" }
            GradientStop { position: 0.50; color: "#a9aba6" }
            GradientStop { position: 0.94; color: "#666864" }
            GradientStop { position: 0.98; color: "#e1e2dd" }
            GradientStop { position: 1.00; color: "#282a29" }
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: Math.max(4, root.width * 0.009)
        color: "#080909"
        border.width: 1
        border.color: "#202220"
    }

    ManualDrawer {
        id: drawer
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            margins: Math.max(7, parent.width * 0.015)
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
        x: Math.max(7, root.width * 0.015)
        y: x
        width: root.width - x * 2
        height: root.height - x * 2 - root.height * 0.73 * root.reveal
        transform: Rotation {
            origin.x: face.width / 2
            origin.y: 0
            axis { x: 1; y: 0; z: 0 }
            angle: -5 * root.reveal * root.animationAmount
        }

        Behavior on height {
            NumberAnimation {
                duration: 520 * Math.max(0.15, root.animationAmount)
                easing.type: Easing.InOutCubic
            }
        }

        SpeakerGrille {
            anchors.fill: parent
            meter: root.visualizationEnabled ? root.speakerLevel : 0.0
            energized: root.visualizationEnabled && root.currentState === "SPEAKING"
        }

        Rectangle {
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: Math.max(4, parent.height * 0.026)
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#f3f3ee" }
                GradientStop { position: 0.45; color: "#aaaca7" }
                GradientStop { position: 1.0; color: "#484a48" }
            }
            border.width: 1
            border.color: "#4c4e4b"
        }

        Rectangle {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: Math.max(5, parent.height * 0.034)
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#6a6c68" }
                GradientStop { position: 0.55; color: "#c8c9c4" }
                GradientStop { position: 1.0; color: "#4c4e4b" }
            }
            border.width: 1
            border.color: "#303230"
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            cursorShape: Qt.PointingHandCursor
            onClicked: root.grilleClicked()
        }
    }

    Rectangle {
        width: Math.min(94, root.width * 0.17)
        height: 6
        anchors.horizontalCenter: parent.horizontalCenter
        y: face.y + face.height - 3
        color: "#d9dad5"
        border.width: 1
        border.color: "#171817"
        radius: 1
        opacity: 0.34 + root.reveal * 0.58
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

    function focusPrompt() { drawer.focusPrompt() }
    function submitPrompt() { drawer.submit() }
}
