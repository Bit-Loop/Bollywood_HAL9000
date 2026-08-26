import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Rectangle {
    id: root
    objectName: "settingsOverlay"
    property var snapshot: ({})
    property var diagnostics: ({})
    property var integrations: []
    readonly property bool compact: width < 760
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
    function byteLabel(value) {
        const bytes = Number(value || 0)
        if (bytes >= 1073741824)
            return (bytes / 1073741824).toFixed(2) + " GiB"
        if (bytes >= 1048576)
            return (bytes / 1048576).toFixed(1) + " MiB"
        if (bytes >= 1024)
            return (bytes / 1024).toFixed(1) + " KiB"
        return bytes + " B"
    }
    function machineReport() {
        return root.diagnostics.machineSelf || ({})
    }
    function dimensionLabel(name) {
        const interoception = machineReport().interoception || ({})
        const values = interoception.values || interoception
        const item = values[name]
        if (!item || item.value === null || item.value === undefined)
            return "UNKNOWN"
        return Number(item.value).toFixed(2) + (item.approximate ? " APPROX" : " EXACT")
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

    GridLayout {
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: parent.bottom; topMargin: 64 }
        columns: root.compact ? 1 : 2
        rowSpacing: 0
        columnSpacing: 0

        Rectangle {
            Layout.preferredWidth: root.compact ? root.width : Math.min(158, root.width * 0.23)
            Layout.preferredHeight: root.compact ? 54 : -1
            Layout.fillWidth: root.compact
            Layout.fillHeight: !root.compact
            color: "#101111"
            border.width: 1
            border.color: HalTheme.line

            ButtonGroup { id: settingsTabs }
            ListView {
                id: settingsNavigation
                objectName: "settingsNavigation"
                readonly property bool horizontalNavigation: root.compact
                anchors.fill: parent
                anchors.margins: root.compact ? 5 : 10
                spacing: 4
                orientation: root.compact ? ListView.Horizontal : ListView.Vertical
                clip: true
                model: ["GENERAL", "HERMES", "WAKE", "SPEECH", "VOICE", "APPEARANCE", "SAFETY", "DIAGNOSTICS"]
                ScrollBar.horizontal: ScrollBar {
                    policy: root.compact ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
                }
                delegate: Button {
                    required property string modelData
                    required property int index
                    width: root.compact ? 112 : settingsNavigation.width
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
                        horizontalAlignment: root.compact ? Text.AlignHCenter : Text.AlignLeft
                        leftPadding: root.compact ? 0 : 10
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
                    label: "Your name"
                    detail: "HAL uses this preferred name as user-provided context, never as identity or authorization."
                    HalTextField {
                        anchors.fill: parent
                        text: root.snapshot.operator ? root.snapshot.operator.preferred_name : ""
                        placeholderText: "Isaiah"
                        maximumLength: 80
                        onEditingFinished: controller.updateSetting("operator.preferred_name", text)
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
                    detail: "Automatic uses Terra medium normally and Sol medium for coding or consequential work. Other choices are sticky manual overrides."
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
                    label: "Resource policy"
                    detail: "Balanced prefers subscription models. Offline local allows only models that fit fresh resource evidence and reserved headroom."
                    HalComboBox {
                        anchors.fill: parent
                        model: ["balanced", "constrained", "offline_local"]
                        currentIndex: Math.max(0, model.indexOf(root.snapshot.hermes && root.snapshot.hermes.router ? root.snapshot.hermes.router.resource_policy : "balanced"))
                        onActivated: controller.updateSetting("hermes.router.resource_policy", currentText)
                    }
                }
                SettingRow {
                    label: "Automatic recovery"
                    detail: "Reconnects the Hermes backend and independently retries HAL Self MCP with bounded backoff."
                    HalCheckBox {
                        anchors.centerIn: parent
                        checked: root.snapshot.hermes && root.snapshot.hermes.router ? root.snapshot.hermes.router.auto_recovery : true
                        onToggled: controller.updateSetting("hermes.router.auto_recovery", checked)
                    }
                }
                SettingRow {
                    label: "HAL Self MCP"
                    Text {
                        anchors.fill: parent
                        text: controller.selfMcpStatus.toUpperCase()
                        color: controller.selfMcpStatus === "ready" ? HalTheme.text : HalTheme.muted
                        font.family: HalTheme.controlFont
                        font.pixelSize: 9
                        verticalAlignment: Text.AlignVCenter
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
                    label: "Observed route"
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
                        HalSlider { id: wakeSensitivity; Layout.fillWidth: true; from: 0; to: 1; stepSize: 0.05; value: root.snapshot.wake ? root.snapshot.wake.sensitivity : 0.6; onMoved: controller.previewSetting("wake.sensitivity", value); onPressedChanged: if (!pressed) controller.commitSetting("wake.sensitivity") }
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
                    label: "Response voice"
                    detail: "Always speaks every reply; Voice prompts only speaks wake-word or microphone conversations; Text only stays silent."
                    HalComboBox {
                        anchors.fill: parent
                        model: [
                            {"label": "Always", "value": "always"},
                            {"label": "Voice prompts only", "value": "voice_prompts"},
                            {"label": "Text only", "value": "text_only"}
                        ]
                        textRole: "label"
                        currentIndex: {
                            const wanted = root.snapshot.voice ? root.snapshot.voice.response_mode : "always"
                            for (let i = 0; i < model.length; ++i)
                                if (model[i].value === wanted) return i
                            return 0
                        }
                        onActivated: controller.updateSetting("voice.response_mode", model[currentIndex].value)
                    }
                }
                SettingRow {
                    label: "Output device"
                    HalComboBox { anchors.fill: parent; model: audioDevices.outputDevices; textRole: "name"; onActivated: controller.updateSetting("voice.output_device", model[currentIndex].id) }
                }
                SettingRow {
                    label: "Volume"
                    HalSlider { anchors.fill: parent; from: 0; to: 1; stepSize: 0.05; value: root.snapshot.voice ? root.snapshot.voice.volume : 0.82; onMoved: controller.previewSetting("voice.volume", value); onPressedChanged: if (!pressed) controller.commitSetting("voice.volume") }
                }
                SettingRow {
                    label: "Speaking rate"
                    HalSlider { anchors.fill: parent; from: 0.5; to: 2; stepSize: 0.05; value: root.snapshot.voice ? root.snapshot.voice.speaking_rate : 1; onMoved: controller.previewSetting("voice.speaking_rate", value); onPressedChanged: if (!pressed) controller.commitSetting("voice.speaking_rate") }
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
                SettingRow { label: "UI scale"; HalSlider { anchors.fill: parent; from: 0.7; to: 1.6; stepSize: 0.05; value: controller.uiScale; onMoved: controller.previewSetting("appearance.ui_scale", value); onPressedChanged: if (!pressed) controller.commitSetting("appearance.ui_scale") } }
                SettingRow { label: "Animation amount"; HalSlider { anchors.fill: parent; from: 0; to: 1; stepSize: 0.05; value: controller.animationAmount; onMoved: controller.previewSetting("appearance.animation_amount", value); onPressedChanged: if (!pressed) controller.commitSetting("appearance.animation_amount") } }
                SettingRow { label: "Eye brightness"; HalSlider { anchors.fill: parent; from: 0.1; to: 1; stepSize: 0.05; value: controller.eyeBrightness; onMoved: controller.previewSetting("appearance.eye_brightness", value); onPressedChanged: if (!pressed) controller.commitSetting("appearance.eye_brightness") } }
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
                SettingRow { label: "HAL Self MCP"; Text { anchors.fill: parent; text: controller.selfMcpStatus.toUpperCase(); color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "Subscription quota"; Text { anchors.fill: parent; text: controller.subscriptionUsageLabel; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter; wrapMode: Text.Wrap } }
                SettingRow { label: "Wake detector"; Text { anchors.fill: parent; text: controller.wakeStatus; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "Microphone"; Text { anchors.fill: parent; text: controller.microphoneStatus; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "STT"; Text { anchors.fill: parent; text: controller.sttStatus + " / " + controller.sttBackend; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "XTTS / Piper"; Text { anchors.fill: parent; text: controller.xttsStatus + " / " + controller.piperStatus + " / " + controller.ttsEngine; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 10; verticalAlignment: Text.AlignVCenter } }
                SettingRow { label: "Compute"; Text { anchors.fill: parent; text: controller.cudaStatus; color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight } }
                SettingSection { title: "Machine self"; detail: "Exact control state first; bounded statistical awareness remains explicitly approximate." }
                SettingRow {
                    objectName: "machineCapabilityStatus"
                    label: "Capability profile"
                    Text {
                        anchors.fill: parent
                        property var rows: root.machineReport().capabilities || []
                        property int readyCount: rows.filter(row => row.lifecycle_state === "READY").length
                        text: rows.length ? readyCount + " / " + rows.length + " READY // EVIDENCE-AGED" : "RUN DIAGNOSTICS"
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "Active task"
                    Text {
                        anchors.fill: parent
                        property var task: root.machineReport().active_task
                        property var missing: (root.machineReport().task_requirements || []).filter(row => row.lifecycle_state !== row.minimum_state)
                        text: task ? task.state.toUpperCase() + " // " + missing.length + " UNMET // " + task.title : "IDLE"
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    objectName: "machineDegradationStatus"
                    label: "Degradation"
                    Text {
                        anchors.fill: parent
                        property var episode: root.machineReport().degradation || ({})
                        text: String(episode.state || "unknown").toUpperCase()
                              + (episode.severity ? " // " + String(episode.severity).toUpperCase() : "")
                              + ((episode.lost_capabilities || []).length ? " // " + episode.lost_capabilities.join(", ") : "")
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "Recovery / revalidation"
                    Text {
                        anchors.fill: parent
                        property var episode: root.machineReport().degradation || ({})
                        text: episode.recovery_seconds_remaining === null || episode.recovery_seconds_remaining === undefined
                              ? Number((episode.conclusions_requiring_revalidation || []).length) + " CONCLUSIONS PENDING"
                              : Number(episode.recovery_seconds_remaining).toFixed(0) + " s STABILITY // "
                                + Number((episode.conclusions_requiring_revalidation || []).length) + " REVALIDATE"
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "Interoception"
                    Text {
                        anchors.fill: parent
                        text: "CAP " + root.dimensionLabel("cognitive_capacity")
                              + " // CONTEXT " + root.dimensionLabel("context_pressure")
                              + " // ANOMALY " + root.dimensionLabel("anomaly_pressure")
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 8; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    objectName: "machineStorageStatus"
                    label: "Bounded storage"
                    Text {
                        anchors.fill: parent
                        property var storage: root.machineReport().storage || ({})
                        text: root.byteLabel(storage.total_bytes) + " / " + root.byteLabel(storage.budget_bytes)
                              + " // " + String(storage.pressure || "unknown").toUpperCase()
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "Integrity"
                    Text {
                        anchors.fill: parent
                        property var report: root.machineReport().integrity || ({})
                        text: "DB " + (report.database_valid ? "PASS" : "UNKNOWN/FAIL")
                              + " // CHAIN " + (report.control_chain_valid ? "PASS" : "UNKNOWN/FAIL")
                              + " // FTS " + (report.fts_valid ? "PASS" : "UNKNOWN/FAIL")
                              + " // BLOBS MISSING " + Number(report.missing_blobs || 0)
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 8; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "Context / sketches"
                    Text {
                        anchors.fill: parent
                        property var capsule: root.machineReport().context_capsule || ({})
                        text: Number(capsule.tokens || 0) + " / " + Number(capsule.budget_tokens || 0)
                              + " TOKENS // " + Number((root.machineReport().sketches || []).length) + " BUCKETS"
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 9; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "Latest sketch"
                    Text {
                        anchors.fill: parent
                        property var rows: root.machineReport().sketches || []
                        property var item: rows.length ? rows[0] : null
                        text: item ? String(item.metric_name).toUpperCase() + " // " + String(item.mode)
                                     + " // " + (item.estimate === null || item.estimate === undefined
                                                  ? "UNKNOWN" : Number(item.estimate).toFixed(1))
                                     + (item.lower_bound === null ? "" : " [" + Number(item.lower_bound).toFixed(1)
                                        + ", " + Number(item.upper_bound).toFixed(1) + "]") : "NO ACTIVE BUCKETS"
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 8; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
                    }
                }
                SettingRow {
                    label: "Maintenance / recent"
                    Text {
                        anchors.fill: parent
                        property var maintenance: root.machineReport().maintenance || ({})
                        property var events: root.machineReport().recent_exact_events || []
                        text: Number(maintenance.compaction_jobs || 0) + " COMPACTIONS // "
                              + Number(maintenance.retention_tombstones || 0) + " TOMBSTONES // "
                              + (events.length ? String(events[0].type).toUpperCase() : "NO EVENTS")
                        color: HalTheme.muted; font.family: HalTheme.controlFont; font.pixelSize: 8; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
                    }
                }
                HalButton { width: Math.min(260, parent.width); text: root.diagnostics.status === "running" ? "RUNNING…" : "RUN DIAGNOSTICS"; enabled: root.diagnostics.status !== "running"; onClicked: controller.runDiagnostics() }
                HalButton { width: Math.min(260, parent.width); text: "EXPORT REDACTED REPORT"; enabled: Object.keys(root.machineReport()).length > 0; onClicked: controller.exportMachineSelfReport() }
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
