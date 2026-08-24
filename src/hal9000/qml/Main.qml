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
    readonly property real chassisMargin: Math.max(6, Math.min(width, height) * 0.014)

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
        anchors.fill: consoleFrame
        anchors.margins: -Math.max(5, window.chassisMargin * 0.65)
        color: "#000000"
        opacity: 0.82
        radius: 7
    }

    Item {
        id: consoleFrame
        objectName: "consoleFrame"
        anchors.fill: parent
        anchors.margins: window.chassisMargin

        Rectangle {
            anchors.fill: parent
            color: "#777974"
            border.width: 1
            border.color: "#c9cac5"
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.00; color: "#262827" }
                GradientStop { position: 0.018; color: "#e0e1dc" }
                GradientStop { position: 0.052; color: "#565956" }
                GradientStop { position: 0.50; color: "#8f918c" }
                GradientStop { position: 0.948; color: "#4c4f4c" }
                GradientStop { position: 0.982; color: "#d7d8d3" }
                GradientStop { position: 1.00; color: "#202221" }
            }
        }

        Rectangle {
            id: faceplate
            anchors.fill: parent
            anchors.margins: Math.max(7, Math.min(parent.width, parent.height) * 0.018)
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

            Rectangle {
                anchors { left: parent.left; right: parent.right; top: parent.top }
                height: Math.max(1, parent.height * 0.002)
                color: "#bfc1bc"
                opacity: 0.22
            }

            Rectangle {
                visible: !window.portrait
                x: parent.width * 0.525
                y: parent.height * 0.035
                width: Math.max(1, parent.width * 0.0015)
                height: parent.height * 0.91
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "#111211" }
                    GradientStop { position: 0.48; color: "#6a6c68" }
                    GradientStop { position: 1.0; color: "#0a0b0a" }
                }
                opacity: 0.72
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
        x: consoleFrame.x + consoleFrame.width * (window.portrait ? 0.095 : 0.055)
        y: consoleFrame.y + consoleFrame.height * (window.portrait ? 0.032 : 0.045)
        width: consoleFrame.width * (window.portrait ? 0.81 : 0.46)
        height: Math.min(
                    consoleFrame.height * (window.portrait ? 0.078 : 0.105),
                    width * 0.20
                )
        scaleFactor: window.uiScale
        active: controller.active
        animationAmount: window.animationAmount
        z: 2
    }

    HalEye {
        id: eye
        objectName: "halEye"
        readonly property real diameter: Math.min(
                                                 consoleFrame.width * (window.portrait ? 0.72 : 0.38),
                                                 consoleFrame.height * (window.portrait ? 0.46 : 0.60)
                                             )
        x: window.portrait
           ? consoleFrame.x + (consoleFrame.width - width) / 2
           : consoleFrame.x + consoleFrame.width * 0.7625 - width / 2
        y: consoleFrame.y + consoleFrame.height * (window.portrait ? 0.235 : 0.205)
        width: diameter
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
        x: consoleFrame.x + consoleFrame.width * 0.055
        y: consoleFrame.y + consoleFrame.height * (window.portrait ? 0.765 : 0.68)
        width: consoleFrame.width * (window.portrait ? 0.89 : 0.46)
        height: consoleFrame.height * (window.portrait ? 0.205 : 0.27)
        drawerOpen: controller.manualOpen
        speakerLevel: controller.speakerLevel
        visualizationEnabled: window.speakerVisualization
        animationAmount: window.animationAmount
        currentState: controller.state
        ttsEngine: controller.ttsEngine
        microphoneMuted: controller.microphoneMuted
        onGrilleClicked: controller.speakerClick()
        onKeyboardToggleRequested: {
            if (controller.manualOpen)
                controller.closeManual()
            else
                controller.openManual()
        }
        onSendText: text => controller.sendText(text)
        onMicrophoneRequested: controller.toggleManualMic()
        onStopGenerationRequested: controller.stopGeneration()
        onStopSpeechRequested: controller.stopSpeech()
        onApprovalAnswered: (requestId, choice) => controller.respondApproval(requestId, choice)
        onInteraction: controller.touchManual()
        z: 3

        states: State {
            name: "manual"
            when: controller.manualOpen
            PropertyChanges {
                speaker.x: window.width * 0.06
                speaker.y: window.height * (window.portrait ? 0.49 : 0.20)
                speaker.width: window.width * 0.88
                speaker.height: window.height * (window.portrait ? 0.47 : 0.74)
            }
        }

        transitions: Transition {
            from: ""
            to: "manual"
            reversible: true
            NumberAnimation {
                properties: "x,y,width,height"
                duration: 460 * Math.max(0.15, window.animationAmount)
                easing.type: Easing.InOutCubic
            }
        }
    }

    Text {
        x: window.portrait
           ? consoleFrame.x + (consoleFrame.width - width) / 2
           : consoleFrame.x + consoleFrame.width * 0.055
             + (consoleFrame.width * 0.46 - width) / 2
        y: consoleFrame.y + consoleFrame.height * (window.portrait ? 0.70 : 0.57)
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
