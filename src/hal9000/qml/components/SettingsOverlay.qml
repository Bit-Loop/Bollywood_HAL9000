import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Rectangle {
    id: root
    property var snapshot: ({})
    property var diagnostics: ({})
    property var integrations: []
    signal closeRequested()

    function benchmarkSummary() {
        const values = root.snapshot.voice ? root.snapshot.voice.benchmark_results : null
        if (!values)
            return "No benchmark data"
        const names = ["XTTS", "Piper"]
        return names.map(name => {
            const rows = values[name] || []
            const passed = rows.filter(row => row.synthesized).length
            const worst = passed ? Math.max(...rows.filter(row => row.synthesized).map(row => Number(row.real_time_factor || 0))) : 0
            const backend = rows.length ? rows[0].backend : "not tested"
            return name.toUpperCase() + " // " + passed + "/4 PASS // RTF " + worst.toFixed(2) + " // " + backend
        }).join("\n")
    }
    color: "#0c0d0d"
    border.width: 1
    border.color: "#555752"

    Rectangle {
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 64
        color: "#121313"
        border.width: 1
        border.color: HalTheme.line
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 20
            anchors.rightMargin: 14
            Text {
                text: "HAL 9000 // CONFIGURATION"
                color: HalTheme.text
                font.family: HalTheme.controlFont
                font.pixelSize: 14
                font.weight: Font.Bold
                font.letterSpacing: 1.4
                Layout.fillWidth: true
            }
            Text {
                text: controller.hermesStatus.toUpperCase()
                color: HalTheme.muted
                font.family: HalTheme.controlFont
                font.pixelSize: 9
            }
            StatusLamp { status: controller.hermesStatus; diameter: 8 }
            HalButton { text: "CLOSE  ESC"; onClicked: root.closeRequested() }
        }
    }

    RowLayout {
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: parent.bottom; topMargin: 64 }
        spacing: 0

        Rectangle {
            Layout.preferredWidth: Math.min(158, root.width * 0.23)
            Layout.fillHeight: true
            color: "#101111"
            border.width: 1
            border.color: HalTheme.line

            ButtonGroup { id: settingsTabs }
            Column {
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
                spacing: 4
                Repeater {
                    model: ["GENERAL", "HERMES", "WAKE", "SPEECH", "VOICE", "APPEARANCE", "SAFETY", "DIAGNOSTICS"]
                    delegate: Button {
                        required property string modelData
                        required property int index
                        width: parent.width
                        height: 39
                        text: modelData
                        checkable: true
                        checked: index === pages.currentIndex
                        ButtonGroup.group: settingsTabs
                        onClicked: pages.currentIndex = index
                        contentItem: Text {
                            text: parent.text
                            color: parent.checked ? HalTheme.text : HalTheme.dim
                            font.family: HalTheme.controlFont
                            font.pixelSize: 9
                            font.weight: parent.checked ? Font.Bold : Font.Normal
                            font.letterSpacing: 0.8
                            verticalAlignment: Text.AlignVCenter
                            leftPadding: 10
                        }
                        background: Rectangle {
                            color: parent.checked ? "#232423" : parent.hovered ? "#171818" : "transparent"
                            border.width: parent.checked ? 1 : 0
                            border.color: HalTheme.steelDark
                            radius: HalTheme.radiusSmall
                        }
                    }
                }
            }
        }

        StackLayout {
            id: pages
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: 0

            SettingsPage {
                leftMargin: 22; rightMargin: 22
                SettingSection { title: "Application"; detail: "Window lifecycle and dedicated-display behavior." }
                SettingRow {
                    label: "Launch mode"; detail: "Remember last is recommended for mixed dedicated/windowed use."
                    HalComboBox {
                        anchors.fill: parent
                        model: ["windowed", "fullscreen", "remember_last"]
                        currentIndex: Math.max(0, model.indexOf(root.snapshot.general ? root.snapshot.general.launch_mode : "remember_last"))
                        onActivated: controller.updateSetting("general.launch_mode", currentText)
                    }
                }
                SettingRow {
                    label: "Target monitor"; detail: "Screen name; blank follows the active desktop screen."
                    HalTextField {
                        anchors.fill: parent
                        text: root.snapshot.general ? root.snapshot.general.target_monitor : ""
                        placeholderText: "automatic"
                        onEditingFinished: controller.updateSetting("general.target_monitor", text)
                    }
                }
                SettingRow {
                    label: "ZIP / postal code"
                    detail: "Used only as coarse context for weather, nearby places, and other explicitly local requests."
                    HalTextField {
                        id: postalCodeField
                        objectName: "postalCodeField"
                        anchors.fill: parent
                        text: root.snapshot.general ? root.snapshot.general.zip_code : ""
                        placeholderText: "60601"
                        maximumLength: 16
                        onEditingFinished: controller.updateSetting("general.zip_code", text)
                    }
                }
                SettingRow {
                    label: "Launch on login"
                    HalCheckBox {
                        anchors.centerIn: parent
                        checked: root.snapshot.general ? root.snapshot.general.launch_on_login : false
                        onToggled: controller.updateSetting("general.launch_on_login", checked)
                    }
                }
                SettingRow {
                    label: "Start in standby"; detail: "Eye remains dark while the local wake detector listens."
                    HalCheckBox {
                        anchors.centerIn: parent
                        checked: root.snapshot.general ? root.snapshot.general.start_in_standby : true
                        onToggled: controller.updateSetting("general.start_in_standby", checked)
                    }
                }
                SettingRow {
                    label: "Auto-return timeout"; detail: "Seconds before an inactive manual console returns to standby."
                    HalSpinBox {
                        anchors.fill: parent
                        from: 0; to: 600
                        value: root.snapshot.general ? root.snapshot.general.standby_timeout_seconds : 45
                        onValueModified: controller.updateSetting("general.standby_timeout_seconds", value)
                    }
                }
            }

            SettingsPage {
                leftMargin: 22; rightMargin: 22
                SettingSection { title: "Hermes Agent"; detail: "Hermes remains the brain. HAL connects to its supported JSON-RPC gateway and preserves tool approvals." }
                SettingRow {
                    label: "Detected executable"
                    Text { anchors.fill: parent; text: controller.hermesExecutable || "not found"; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; elide: Text.ElideMiddle; verticalAlignment: Text.AlignVCenter }
                }
                SettingRow {
                    label: "Runtime"
                    RowLayout {
                        anchors.fill: parent
                        StatusLamp { status: controller.hermesStatus; diameter: 8 }
                        Text { text: "v" + controller.hermesVersion + " / " + controller.hermesStatus; color: HalTheme.text; font.family: HalTheme.controlFont; font.pixelSize: 10 }
                    }
                }
                SettingRow {
                    label: "Chat model"
                    detail: "Pulled from authenticated Hermes providers. The choice applies only to this HAL session."
                    RowLayout {
                        anchors.fill: parent
                        spacing: 5
                        HalComboBox {
                            id: hermesModelSelector
                            objectName: "hermesModelSelector"
                            Layout.fillWidth: true
                            model: controller.hermesModels
                            textRole: "label"
                            currentIndex: controller.hermesModelIndex
                            enabled: controller.hermesModels.length > 0
                            onActivated: {
                                const choice = controller.hermesModels[currentIndex]
                                if (choice)
                                    controller.selectHermesModel(choice.provider, choice.model)
                            }
                        }
                        HalButton { text: "REFRESH"; onClicked: controller.refreshHermesModels() }
                    }
                }
                SettingRow {
                    label: "Reasoning effort"
                    detail: "Session-scoped. This changes only HAL's selected Hermes model."
                    HalComboBox {
                        anchors.fill: parent
                        model: ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
                        currentIndex: Math.max(0, model.indexOf(root.snapshot.hermes ? root.snapshot.hermes.reasoning_effort : "medium"))
                        onActivated: controller.updateSetting("hermes.reasoning_effort", currentText)
                    }
                }
                SettingRow {
                    label: "Active route"
                    Text {
                        anchors.fill: parent
                        text: controller.hermesModelLabel
                        color: HalTheme.muted
                        font.family: HalTheme.controlFont
                        font.pixelSize: 9
                        elide: Text.ElideMiddle
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "Backend mode"
                    HalComboBox {
                        id: backendModeSelector
                        objectName: "backendModeSelector"
                        anchors.fill: parent
                        model: ["local", "remote"]
                        currentIndex: Math.max(0, model.indexOf(root.snapshot.hermes ? root.snapshot.hermes.mode : "local"))
                        onActivated: controller.updateSetting("hermes.mode", currentText)
                    }
                }
                SettingRow {
                    label: "Backend URL"; detail: "Remote credentials are never shown here after entry."
                    HalTextField {
                        anchors.fill: parent
                        text: root.snapshot.hermes ? root.snapshot.hermes.backend_url : ""
                        onEditingFinished: controller.updateSetting("hermes.backend_url", text)
                    }
                }
                SettingRow {
                    label: "Remote token"
                    detail: controller.hermesCredentialPresent
                            ? "Stored in the desktop keyring. The saved value is never displayed."
                            : "Optional. HAL9000_HERMES_TOKEN may also supply it without persistence."
                    RowLayout {
                        anchors.fill: parent
                        HalTextField {
                            id: remoteToken
                            Layout.fillWidth: true
                            echoMode: TextInput.Password
                            placeholderText: controller.hermesCredentialPresent ? "credential stored" : "enter token"
                            onEditingFinished: {
                                if (text.length) {
                                    controller.setHermesToken(text)
                                    clear()
                                }
                            }
                        }
                        HalButton {
                            text: "CLEAR"
                            enabled: controller.hermesCredentialPresent
                            onClicked: controller.setHermesToken("")
                        }
                    }
                }
                SettingRow {
                    label: "Profile"
                    HalTextField {
                        anchors.fill: parent
                        text: root.snapshot.hermes ? root.snapshot.hermes.profile : ""
                        placeholderText: "default profile"
                        onEditingFinished: controller.updateSetting("hermes.profile", text)
                    }
                }
                SettingRow {
                    label: "Backend controls"
                    RowLayout {
                        anchors.fill: parent
                        spacing: 5
                        HalButton { text: "START"; onClicked: controller.startHermes() }
                        HalButton { text: "STOP"; onClicked: controller.stopHermes() }
                        HalButton { text: "RECONNECT"; onClicked: controller.reconnectHermes() }
                    }
                }
                SettingSection { title: "Available integrations"; detail: "Read-only summary reported by the active Hermes session." }
                Text {
                    width: parent.width
                    text: root.integrations && root.integrations.length ? root.integrations.join("  //  ") : "Waiting for Hermes tool inventory"
                    color: HalTheme.muted
                    font.family: HalTheme.controlFont
                    font.pixelSize: 10
                    wrapMode: Text.Wrap
                }
                Row {
                    spacing: 6
                    HalButton { text: "REVEAL LOGS"; onClicked: controller.revealPath("logs") }
                    HalButton { text: "REVEAL CONFIG"; onClicked: controller.revealPath("config") }
                }
            }

            SettingsPage {
                leftMargin: 22; rightMargin: 22
                SettingSection { title: "Wake word"; detail: "Always-listening detection is local. Only the phrase detector runs in standby." }
                SettingRow {
                    label: "Enabled"
                    HalCheckBox { anchors.centerIn: parent; checked: root.snapshot.wake ? root.snapshot.wake.enabled : true; onToggled: controller.updateSetting("wake.enabled", checked) }
                }
                SettingRow {
                    label: "Provider"
                    HalComboBox { anchors.fill: parent; model: ["sherpa"]; currentIndex: 0; enabled: false }
                }
                SettingRow {
                    label: "Phrase"; detail: "Sherpa tokenizes this phrase at runtime; no custom training is required."
                    HalTextField { anchors.fill: parent; text: root.snapshot.wake ? root.snapshot.wake.phrase : "hey hal"; onEditingFinished: controller.updateSetting("wake.phrase", text) }
                }
                SettingRow {
                    label: "Sensitivity"
                    RowLayout {
                        anchors.fill: parent
                        HalSlider { id: wakeSensitivity; Layout.fillWidth: true; from: 0; to: 1; stepSize: 0.05; value: root.snapshot.wake ? root.snapshot.wake.sensitivity : 0.6; onMoved: controller.updateSetting("wake.sensitivity", value) }
                        Text { text: wakeSensitivity.value.toFixed(2); color: HalTheme.text; font.family: HalTheme.controlFont; font.pixelSize: 10 }
                    }
                }
                SettingRow {
                    label: "Microphone device"
                    HalComboBox {
                        anchors.fill: parent
                        model: audioDevices.inputDevices
                        textRole: "name"
                        onActivated: controller.updateSetting("wake.input_device", model[currentIndex].id)
                    }
                }
                SettingRow {
                    label: "Detector status"
                    RowLayout {
                        anchors.fill: parent
                        StatusLamp { status: controller.wakeStatus; diameter: 8 }
                        Text {
                            text: controller.wakeStatus.toUpperCase()
                            color: HalTheme.muted
                            font.family: HalTheme.controlFont
                            font.pixelSize: 10
                        }
                        HalButton { text: "TEST"; onClicked: controller.testWakeDetector() }
                    }
                }
            }

            SettingsPage {
                leftMargin: 22; rightMargin: 22
                SettingSection { title: "Speech recognition"; detail: "Faster-Whisper runs locally and raw microphone audio is not retained." }
                SettingRow { label: "Provider"; HalComboBox { anchors.fill: parent; model: ["local"]; enabled: false } }
                SettingRow {
                    label: "Model"
                    HalComboBox {
                        anchors.fill: parent
                        model: ["tiny", "base", "small", "medium", "large-v3"]
                        currentIndex: Math.max(0, model.indexOf(root.snapshot.stt ? root.snapshot.stt.model : "small"))
                        onActivated: controller.updateSetting("stt.model", currentText)
                    }
                }
                SettingRow {
                    label: "Language"
                    HalTextField { anchors.fill: parent; text: root.snapshot.stt ? root.snapshot.stt.language : "en"; onEditingFinished: controller.updateSetting("stt.language", text) }
                }
                SettingRow {
                    label: "Input device"
                    HalComboBox { anchors.fill: parent; model: audioDevices.inputDevices; textRole: "name"; onActivated: controller.updateSetting("stt.input_device", model[currentIndex].id) }
                }
                SettingRow {
                    label: "Microphone level"
                    Rectangle {
                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter }
                        height: 12; color: "#0a0b0b"; border.width: 1; border.color: HalTheme.line
                        Rectangle { width: parent.width * controller.micLevel; height: parent.height; color: "#7b7d77" }
                    }
                }
                SettingRow {
                    label: "Recognition status"
                    RowLayout {
                        anchors.fill: parent
                        StatusLamp { status: controller.sttStatus; diameter: 8 }
                        Text {
                            text: controller.sttStatus + " / " + controller.sttBackend
                            color: HalTheme.muted
                            font.family: HalTheme.controlFont
                            font.pixelSize: 9
                            Layout.fillWidth: true
                        }
                        HalButton { text: "MIC TEST"; onClicked: controller.testMicrophone() }
                    }
                }
            }

            SettingsPage {
                leftMargin: 22; rightMargin: 22
                SettingSection { title: "HAL voice"; detail: "Piper is the low-latency default. XTTS remains available as an explicit high-fidelity option." }
                SettingRow {
                    label: "Engine"
                    HalComboBox {
                        anchors.fill: parent
                        model: ["auto", "xtts", "piper"]
                        currentIndex: Math.max(0, model.indexOf(root.snapshot.voice ? root.snapshot.voice.mode : "auto"))
                        onActivated: controller.updateSetting("voice.mode", currentText)
                    }
                }
                SettingRow {
                    label: "Output device"
                    HalComboBox { anchors.fill: parent; model: audioDevices.outputDevices; textRole: "name"; onActivated: controller.updateSetting("voice.output_device", model[currentIndex].id) }
                }
                SettingRow {
                    label: "Volume"
                    HalSlider { anchors.fill: parent; from: 0; to: 1; stepSize: 0.05; value: root.snapshot.voice ? root.snapshot.voice.volume : 0.82; onMoved: controller.updateSetting("voice.volume", value) }
                }
                SettingRow {
                    label: "Speaking rate"
                    HalSlider { anchors.fill: parent; from: 0.5; to: 2; stepSize: 0.05; value: root.snapshot.voice ? root.snapshot.voice.speaking_rate : 1; onMoved: controller.updateSetting("voice.speaking_rate", value) }
                }
                SettingRow {
                    label: "Runtime"
                    Text { anchors.fill: parent; text: controller.ttsEngine + " / " + controller.ttsStatus + "\n" + controller.cudaStatus; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter; wrapMode: Text.Wrap }
                }
                SettingRow {
                    label: "Model state"
                    Text {
                        anchors.fill: parent
                        text: "XTTS // " + controller.xttsStatus + "\nPIPER // " + controller.piperStatus
                        color: HalTheme.muted
                        font.family: HalTheme.controlFont
                        font.pixelSize: 9
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "A/B voice test"; detail: "Listen to the same line through each installed engine."
                    RowLayout {
                        anchors.fill: parent
                        HalButton { text: "XTTS"; onClicked: controller.testVoice("XTTS") }
                        HalButton { text: "PIPER"; onClicked: controller.testVoice("Piper") }
                    }
                }
                SettingRow {
                    label: "Benchmark"; detail: "Four fixed phrases measure initialization, synthesis, RTF, output duration, backend, and failures."
                    HalButton { anchors.fill: parent; text: "RUN BENCHMARK"; onClicked: controller.runVoiceBenchmark() }
                }
                SettingSection { title: "Last selection" }
                Text {
                    width: parent.width
                    text: root.snapshot.voice && root.snapshot.voice.selected_engine
                          ? "AUTO SELECTED // " + root.snapshot.voice.selected_engine + "\n" + (root.snapshot.voice.last_fallback_reason || "XTTS passed the reliability gate")
                          : "Benchmark has not completed"
                    color: HalTheme.muted
                    font.family: HalTheme.controlFont
                    font.pixelSize: 9
                    wrapMode: Text.Wrap
                }
                Text {
                    width: parent.width
                    text: root.benchmarkSummary()
                    color: HalTheme.dim
                    font.family: HalTheme.controlFont
                    font.pixelSize: 9
                    lineHeight: 1.25
                    wrapMode: Text.Wrap
                }
            }

            SettingsPage {
                leftMargin: 22; rightMargin: 22
                SettingSection { title: "Appearance"; detail: "The restrained physical console is the default. Animation values affect only low-cost opacity and transforms." }
                SettingRow { label: "Fullscreen"; HalCheckBox { anchors.centerIn: parent; checked: controller.fullscreen; onToggled: if (checked !== controller.fullscreen) controller.toggleFullscreen() } }
                SettingRow { label: "UI scale"; HalSlider { anchors.fill: parent; from: 0.7; to: 1.6; stepSize: 0.05; value: root.snapshot.appearance ? root.snapshot.appearance.ui_scale : 1; onMoved: controller.updateSetting("appearance.ui_scale", value) } }
                SettingRow { label: "Animation amount"; HalSlider { anchors.fill: parent; from: 0; to: 1; stepSize: 0.05; value: root.snapshot.appearance ? root.snapshot.appearance.animation_amount : 0.72; onMoved: controller.updateSetting("appearance.animation_amount", value) } }
                SettingRow { label: "Eye brightness"; HalSlider { anchors.fill: parent; from: 0.1; to: 1; stepSize: 0.05; value: root.snapshot.appearance ? root.snapshot.appearance.eye_brightness : 0.9; onMoved: controller.updateSetting("appearance.eye_brightness", value) } }
                SettingRow { label: "Speaker visualization"; HalCheckBox { anchors.centerIn: parent; checked: root.snapshot.appearance ? root.snapshot.appearance.speaker_visualization : true; onToggled: controller.updateSetting("appearance.speaker_visualization", checked) } }
            }

            SettingsPage {
                leftMargin: 22; rightMargin: 22
                SettingSection { title: "Safety boundary"; detail: "Voice activation is not authentication." }
                Text {
                    width: parent.width
                    text: "HAL sends requests into the same Hermes session and surfaces Hermes approval prompts. It never grants root access, bypasses approvals, logs API keys, or treats a recognized voice as identity. Wake detection and speech recognition remain local. Raw microphone recordings are not persisted by default."
                    color: HalTheme.text
                    font.family: HalTheme.displayFont
                    font.pixelSize: 14
                    lineHeight: 1.35
                    wrapMode: Text.Wrap
                }
                SettingRow {
                    label: "Hermes security status"
                    RowLayout {
                        anchors.fill: parent
                        StatusLamp { status: controller.hermesStatus; diameter: 8 }
                        Text {
                            text: controller.hermesStatus === "connected" ? "APPROVAL CHANNEL ACTIVE" : "BACKEND NOT CONNECTED"
                            color: HalTheme.muted
                            font.family: HalTheme.controlFont
                            font.pixelSize: 9
                        }
                    }
                }
            }

            SettingsPage {
                leftMargin: 22; rightMargin: 22
                SettingSection { title: "Diagnostics"; detail: "Fresh read-only checks of the current runtime and optional subsystems." }
                SettingRow { label: "Hermes"; Text { anchors.fill: parent; text: controller.hermesStatus + " / " + controller.backendLatency.toFixed(0) + " ms"; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "Wake detector"; Text { anchors.fill: parent; text: controller.wakeStatus; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "Microphone"; Text { anchors.fill: parent; text: controller.microphoneStatus; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "STT"; Text { anchors.fill: parent; text: controller.sttStatus + " / " + controller.sttBackend; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "XTTS / Piper"; Text { anchors.fill: parent; text: controller.xttsStatus + " / " + controller.piperStatus + " / " + controller.ttsEngine; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "Compute"; Text { anchors.fill: parent; text: controller.cudaStatus; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight } }
                HalButton { width: Math.min(260, parent.width); text: root.diagnostics.status === "running" ? "RUNNING…" : "RUN DIAGNOSTICS"; enabled: root.diagnostics.status !== "running"; onClicked: controller.runDiagnostics() }
                Repeater {
                    model: root.diagnostics.checks || []
                    delegate: Row {
                        required property var modelData
                        width: parent.width
                        height: 28
                        spacing: 9
                        StatusLamp { status: modelData.status; diameter: 7; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: modelData.name.toUpperCase() + " // " + modelData.detail; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; anchors.verticalCenter: parent.verticalCenter }
                    }
                }
                SettingSection { title: "Recent errors"; detail: "Technical detail is also written to the rotating application log." }
                Text {
                    width: parent.width
                    text: controller.recentErrors.length ? controller.recentErrors.join("\n") : "No recent subsystem errors"
                    color: HalTheme.dim
                    font.family: HalTheme.controlFont
                    font.pixelSize: 8
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
