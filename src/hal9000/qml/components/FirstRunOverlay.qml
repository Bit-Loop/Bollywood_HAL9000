import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Rectangle {
    id: root
    signal setupRequested()
    signal typedRequested()
    property bool busy: false
    property string task: ""
    property real progress: 0.0
    color: "#ed0a0b0b"
    border.width: 1
    border.color: "#454743"

    Rectangle {
        width: Math.min(parent.width * 0.78, 680)
        height: Math.min(parent.height * 0.68, 620)
        anchors.centerIn: parent
        color: "#111212"
        border.width: 1
        border.color: HalTheme.steelDark
        radius: HalTheme.radiusMedium

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Math.max(24, parent.width * 0.065)
            spacing: HalTheme.spacing4

            Text {
                text: "HAL 9000"
                color: HalTheme.text
                font.family: HalTheme.displayFont
                font.pixelSize: Math.max(30, Math.min(48, parent.width * 0.09))
                font.weight: Font.Black
                font.letterSpacing: 2
            }
            Text {
                text: "LOCAL SYSTEM PREPARATION"
                color: HalTheme.muted
                font.family: HalTheme.controlFont
                font.pixelSize: 11
                font.letterSpacing: 1.4
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: HalTheme.line }
            Text {
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "HAL found the native console runtime. Guided setup prepares the local Sherpa “hey hal” detector, Faster-Whisper small/en, XTTS-v2 HAL voice, and Piper fallback without blocking the interface.\n\nThe XTTS checkpoint is approximately 5.6 GB. Piper and Sherpa are much smaller. Weights remain in your XDG cache and are not copied into the application.\n\nHermes continues to own sessions, models, tools, MCP servers, memory, and security approvals."
                color: HalTheme.text
                font.family: HalTheme.displayFont
                font.pixelSize: Math.max(13, Math.min(17, parent.width * 0.029))
                lineHeight: 1.35
                wrapMode: Text.Wrap
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.busy
                spacing: 7
                Text {
                    Layout.fillWidth: true
                    text: (root.task || "PREPARING LOCAL MODELS").toUpperCase()
                    color: HalTheme.muted
                    font.family: HalTheme.controlFont
                    font.pixelSize: 9
                    font.letterSpacing: 0.8
                    elide: Text.ElideRight
                }
                HalProgressBar {
                    Layout.fillWidth: true
                    from: 0
                    to: 1
                    value: root.progress
                    indeterminate: root.progress <= 0
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: HalTheme.spacing2
                HalButton { text: "USE TYPED MODE"; Layout.fillWidth: true; onClicked: root.typedRequested() }
                HalButton {
                    text: root.busy ? "PREPARING…" : "PREPARE LOCAL VOICE"
                    Layout.fillWidth: true
                    enabled: !root.busy
                    onClicked: root.setupRequested()
                }
            }
        }
    }
}
