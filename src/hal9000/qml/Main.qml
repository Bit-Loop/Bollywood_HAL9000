import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

ApplicationWindow {
    id: window
    visible: false
    title: "HAL 9000"
    width: controller.settingsSnapshot.general.window_width || 800
    height: controller.settingsSnapshot.general.window_height || 1000
    minimumWidth: 600
    minimumHeight: 800
    color: HalTheme.black

    readonly property bool portrait: height >= width * 1.08
    readonly property real uiScale: controller.settingsSnapshot.appearance.ui_scale || 1.0
    readonly property real animationAmount: controller.settingsSnapshot.appearance.animation_amount ?? 0.72
    readonly property real eyeBrightness: controller.settingsSnapshot.appearance.eye_brightness || 0.9
    readonly property bool speakerVisualization: controller.settingsSnapshot.appearance.speaker_visualization ?? true

    Component.onCompleted: {
        controller.startup()
    }

    onClosing: {
        if (controller.servicesEnabled)
            controller.saveWindowGeometry(x, y, width, height)
    }

    Connections {
        target: controller
        function onFullscreenRequested(enabled) {
            if (enabled)
                window.showFullScreen()
            else
                window.showNormal()
        }
        function onFocusInputRequested() { speaker.focusPrompt() }
        function onNotification(message) {
            toastText.text = message
            toast.opacity = 1
            toastTimer.restart()
        }
    }

    Shortcut { sequence: "Ctrl+Shift+S"; context: Qt.ApplicationShortcut; onActivated: controller.openSettings() }
    Shortcut { sequence: "Escape"; context: Qt.ApplicationShortcut; onActivated: controller.handleEscape() }
    Shortcut { sequence: "Ctrl+L"; context: Qt.ApplicationShortcut; onActivated: controller.focusInput() }
    Shortcut { sequence: "Ctrl+Return"; context: Qt.ApplicationShortcut; onActivated: speaker.submitPrompt() }
    Shortcut { sequence: "Ctrl+Enter"; context: Qt.ApplicationShortcut; onActivated: speaker.submitPrompt() }
    Shortcut { sequence: "Ctrl+Shift+M"; context: Qt.ApplicationShortcut; onActivated: controller.toggleMicrophoneMute() }
    Shortcut { sequence: "F11"; context: Qt.ApplicationShortcut; onActivated: controller.toggleFullscreen() }

    MetalPanel { anchors.fill: parent; baseColor: "#050505" }

    Rectangle {
        anchors.centerIn: consoleFrame
        width: consoleFrame.width + Math.max(18, consoleFrame.width * 0.055)
        height: consoleFrame.height + Math.max(18, consoleFrame.width * 0.055)
        color: "#000000"
        opacity: 0.72
        radius: 5
    }

    Item {
        id: consoleFrame
        objectName: "consoleFrame"
        width: Math.min(window.width * (window.portrait ? 0.67 : 0.42), window.height * 0.37)
        height: window.height * (window.portrait ? 0.975 : 0.95)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter

        Rectangle {
            anchors.fill: parent
            color: "#777974"
            border.width: 1
            border.color: "#c9cac5"
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.00; color: "#262827" }
                GradientStop { position: 0.025; color: "#b8bab5" }
                GradientStop { position: 0.065; color: "#4a4c49" }
                GradientStop { position: 0.50; color: "#8d8f8a" }
                GradientStop { position: 0.935; color: "#414340" }
                GradientStop { position: 0.975; color: "#c7c8c3" }
                GradientStop { position: 1.00; color: "#202221" }
            }
        }

        Rectangle {
            id: faceplate
            anchors.fill: parent
            anchors.leftMargin: Math.max(7, parent.width * 0.026)
            anchors.rightMargin: Math.max(7, parent.width * 0.026)
            anchors.topMargin: Math.max(7, parent.width * 0.021)
            anchors.bottomMargin: Math.max(7, parent.width * 0.021)
            color: "#080808"
            border.width: 1
            border.color: "#202120"

            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "#050505" }
                GradientStop { position: 0.18; color: "#0b0b0b" }
                GradientStop { position: 0.52; color: "#090909" }
                GradientStop { position: 0.84; color: "#0d0d0d" }
                GradientStop { position: 1.0; color: "#050505" }
            }

            Canvas {
                anchors.fill: parent
                opacity: 0.13
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    for (let x = 2; x < width; x += 4) {
                        ctx.strokeStyle = x % 12 === 2
                                          ? "rgba(255,255,255,0.055)"
                                          : "rgba(255,255,255,0.018)"
                        ctx.beginPath()
                        ctx.moveTo(x + 0.5, 0)
                        ctx.lineTo(x + 0.5, height)
                        ctx.stroke()
                    }
                }
            }

            MouseArea {
                id: settingsContextArea
                objectName: "settingsContextArea"
                anchors.fill: parent
                acceptedButtons: Qt.RightButton
                onClicked: mouse => {
                    if (mouse.button === Qt.RightButton)
                        controller.openSettings()
                }
            }
        }
    }

    HalHeader {
        id: header
        objectName: "halHeader"
        x: consoleFrame.x + consoleFrame.width * 0.105
        y: consoleFrame.y + consoleFrame.height * 0.028
        width: consoleFrame.width * 0.79
        height: consoleFrame.height * 0.072
        scaleFactor: window.uiScale
        z: 2
    }

    HalEye {
        id: eye
        objectName: "halEye"
        x: consoleFrame.x + consoleFrame.width * 0.11
        y: consoleFrame.y + consoleFrame.height * 0.335
        width: consoleFrame.width * 0.78
        height: width
        active: controller.active
        state: controller.state
        brightness: window.eyeBrightness
        animationAmount: window.animationAmount
        speakerLevel: controller.speakerLevel
        z: 2
    }

    SpeakerAssembly {
        id: speaker
        objectName: "speakerAssembly"
        x: controller.manualOpen
           ? window.width * (window.portrait ? 0.06 : 0.23)
           : consoleFrame.x + consoleFrame.width * 0.055
        y: controller.manualOpen
           ? window.height * (window.portrait ? 0.49 : 0.25)
           : consoleFrame.y + consoleFrame.height * 0.765
        width: controller.manualOpen
               ? window.width * (window.portrait ? 0.88 : 0.54)
               : consoleFrame.width * 0.89
        height: controller.manualOpen
                ? window.height * (window.portrait ? 0.47 : 0.70)
                : consoleFrame.height * 0.205
        drawerOpen: controller.manualOpen
        speakerLevel: controller.speakerLevel
        visualizationEnabled: window.speakerVisualization
        animationAmount: window.animationAmount
        currentState: controller.state
        ttsEngine: controller.ttsEngine
        microphoneMuted: controller.microphoneMuted
        onGrilleClicked: controller.speakerClick()
        onSendText: text => controller.sendText(text)
        onMicrophoneRequested: controller.toggleManualMic()
        onStopGenerationRequested: controller.stopGeneration()
        onStopSpeechRequested: controller.stopSpeech()
        onApprovalAnswered: (requestId, choice) => controller.respondApproval(requestId, choice)
        onInteraction: controller.touchManual()
        z: 3

        Behavior on x { enabled: controller.manualOpen; NumberAnimation { duration: 420 * Math.max(0.15, window.animationAmount); easing.type: Easing.OutCubic } }
        Behavior on y { enabled: controller.manualOpen; NumberAnimation { duration: 420 * Math.max(0.15, window.animationAmount); easing.type: Easing.OutCubic } }
        Behavior on width { enabled: controller.manualOpen; NumberAnimation { duration: 420 * Math.max(0.15, window.animationAmount); easing.type: Easing.OutCubic } }
        Behavior on height { enabled: controller.manualOpen; NumberAnimation { duration: 420 * Math.max(0.15, window.animationAmount); easing.type: Easing.OutCubic } }
    }

    Text {
        anchors.horizontalCenter: consoleFrame.horizontalCenter
        y: consoleFrame.y + consoleFrame.height * 0.68
        text: controller.state === "STANDBY" ? "" : controller.state
        color: "#777872"
        font.family: HalTheme.controlFont
        font.pixelSize: Math.max(8, consoleFrame.width * 0.018)
        font.letterSpacing: 1.6
        opacity: controller.state === "STANDBY" ? 0 : 0.78
        z: 2
    }

    SettingsOverlay {
        id: settings
        objectName: "settingsOverlay"
        anchors.fill: parent
        anchors.margins: Math.max(8, Math.min(window.width, window.height) * 0.022)
        visible: opacity > 0.01
        enabled: controller.settingsOpen
        opacity: controller.settingsOpen ? 1 : 0
        z: 30
        snapshot: controller.settingsSnapshot
        diagnostics: controller.diagnostics
        integrations: controller.integrations
        onCloseRequested: controller.closeSettings()
        Behavior on opacity { NumberAnimation { duration: 180 * Math.max(0.15, window.animationAmount) } }
    }

    FirstRunOverlay {
        objectName: "firstRunOverlay"
        anchors.fill: parent
        anchors.margins: Math.max(8, Math.min(window.width, window.height) * 0.025)
        visible: controller.firstRun
        enabled: visible
        busy: controller.setupInProgress
        task: controller.modelTask
        progress: controller.modelProgress
        z: 40
        onSetupRequested: controller.beginSetup()
        onTypedRequested: controller.useTypedMode()
    }

    Rectangle {
        id: toast
        z: 60
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 24 }
        width: Math.min(parent.width * 0.82, toastText.implicitWidth + 42)
        height: toastText.implicitHeight + 24
        color: "#eb151616"
        border.width: 1
        border.color: "#666862"
        radius: HalTheme.radiusSmall
        opacity: 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 180 } }
        Text {
            id: toastText
            anchors.fill: parent
            anchors.margins: 12
            color: HalTheme.text
            font.family: HalTheme.controlFont
            font.pixelSize: 10
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        Timer { id: toastTimer; interval: 4200; onTriggered: toast.opacity = 0 }
    }
}
