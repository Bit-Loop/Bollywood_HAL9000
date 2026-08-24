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

    MetalPanel { anchors.fill: parent }

    Rectangle {
        id: chassis
        anchors.fill: parent
        anchors.margins: window.portrait
                         ? Math.max(8, window.width * 0.018)
                         : Math.max(10, window.height * 0.025)
        color: "transparent"
        border.width: Math.max(1, Math.min(width, height) * 0.002)
        border.color: "#575954"
        radius: 3

        Rectangle {
            anchors.fill: parent
            anchors.margins: 5
            color: "transparent"
            border.width: 1
            border.color: "#1e201f"
        }
    }

    HalHeader {
        id: header
        objectName: "halHeader"
        x: window.portrait ? window.width * 0.08 : window.width * 0.23
        y: window.portrait ? window.height * 0.035 : window.height * 0.035
        width: window.portrait ? window.width * 0.84 : window.width * 0.54
        height: window.portrait ? window.height * 0.14 : window.height * 0.18
        scaleFactor: window.uiScale
    }

    HalEye {
        id: eye
        objectName: "halEye"
        x: window.portrait ? window.width * 0.16 : window.width * 0.06
        y: window.portrait ? window.height * 0.18 : window.height * 0.235
        width: window.portrait ? window.width * 0.68 : window.width * 0.38
        height: window.portrait ? window.height * 0.30 : window.height * 0.60
        active: controller.active
        state: controller.state
        brightness: window.eyeBrightness
        animationAmount: window.animationAmount
        speakerLevel: controller.speakerLevel
    }

    SpeakerAssembly {
        id: speaker
        objectName: "speakerAssembly"
        x: window.portrait ? window.width * 0.075 : window.width * 0.46
        y: window.portrait ? window.height * 0.51 : window.height * 0.235
        width: window.portrait ? window.width * 0.85 : window.width * 0.48
        height: window.portrait ? window.height * 0.445 : window.height * 0.70
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
    }

    Text {
        anchors.horizontalCenter: window.portrait ? parent.horizontalCenter : eye.horizontalCenter
        y: window.portrait ? window.height * 0.475 : window.height * 0.86
        text: controller.state === "STANDBY" ? "" : controller.state
        color: "#777872"
        font.family: HalTheme.controlFont
        font.pixelSize: Math.max(8, Math.min(11, width * 0.012))
        font.letterSpacing: 1.6
        opacity: controller.state === "STANDBY" ? 0 : 0.78
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
