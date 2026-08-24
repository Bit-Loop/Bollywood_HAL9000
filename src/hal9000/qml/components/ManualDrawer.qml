import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Rectangle {
    id: root
    property string currentState: "MANUAL"
    property string ttsEngine: "AUTO"
    property bool microphoneMuted: false
    signal sendText(string text)
    signal microphoneRequested()
    signal stopGenerationRequested()
    signal stopSpeechRequested()
    signal approvalAnswered(string requestId, string choice)
    signal interaction()

    color: "#0a0b0b"
    border.width: 1
    border.color: "#3e403e"
    radius: HalTheme.radiusSmall
    clip: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Math.max(10, Math.min(parent.width, parent.height) * 0.022)
        spacing: HalTheme.spacing2

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            spacing: HalTheme.spacing3

            StatusLamp { status: root.currentState; diameter: 7 }
            Text {
                text: root.currentState
                color: HalTheme.text
                font.family: HalTheme.controlFont
                font.pixelSize: 10
                font.weight: Font.DemiBold
                font.letterSpacing: 1.1
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: HalTheme.line }
            Text {
                text: "VOICE // " + root.ttsEngine
                color: HalTheme.muted
                font.family: HalTheme.controlFont
                font.pixelSize: 9
                font.letterSpacing: 0.7
                elide: Text.ElideRight
            }
        }

        ListView {
            id: transcript
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 10
            model: conversationsModel
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            onCountChanged: Qt.callLater(positionViewAtEnd)

            delegate: Item {
                required property string role
                required property string text
                required property bool streaming
                required property bool error
                required property string timestamp
                width: transcript.width
                height: messageText.implicitHeight + 25

                Rectangle {
                    anchors.fill: parent
                    color: role === "user" ? "#141616" : "transparent"
                    border.width: role === "user" ? 1 : 0
                    border.color: "#292b29"
                    radius: HalTheme.radiusSmall
                }

                Text {
                    id: roleLabel
                    anchors { left: parent.left; top: parent.top; leftMargin: 10; topMargin: 7 }
                    text: role === "assistant" ? "HAL" : role === "user" ? "OPERATOR" : "SYSTEM"
                    color: error ? HalTheme.red : role === "assistant" ? "#d4d3ca" : HalTheme.dim
                    font.family: HalTheme.controlFont
                    font.pixelSize: 8
                    font.weight: Font.DemiBold
                    font.letterSpacing: 1
                }

                Text {
                    id: messageText
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: roleLabel.bottom
                        leftMargin: 10
                        rightMargin: 10
                        topMargin: 3
                    }
                    text: parent.text + (parent.streaming ? "  ▮" : "")
                    color: parent.error ? "#c87173" : HalTheme.text
                    font.family: HalTheme.displayFont
                    font.pixelSize: Math.max(12, Math.min(16, transcript.width * 0.026))
                    lineHeight: 1.22
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }
            }
        }

        ListView {
            id: approvals
            Layout.fillWidth: true
            Layout.preferredHeight: count > 0 ? Math.min(150, contentHeight) : 0
            visible: count > 0
            clip: true
            spacing: 6
            model: approvalModel

            delegate: Rectangle {
                id: approvalDelegate
                required property string requestId
                required property string title
                required property string detail
                required property string risk
                required property bool resolved
                width: approvals.width
                height: resolved ? 0 : approvalColumn.implicitHeight + 18
                visible: !resolved
                color: "#180e0f"
                border.width: 1
                border.color: HalTheme.redDeep
                radius: HalTheme.radiusSmall

                ColumnLayout {
                    id: approvalColumn
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 9 }
                    spacing: 5
                    Text {
                        Layout.fillWidth: true
                        text: "APPROVAL REQUIRED // " + approvalDelegate.risk.toUpperCase()
                        color: "#da8a8c"
                        font.family: HalTheme.controlFont
                        font.pixelSize: 9
                        font.weight: Font.Bold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: approvalDelegate.title + "\n" + approvalDelegate.detail
                        color: HalTheme.text
                        font.family: HalTheme.displayFont
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.alignment: Qt.AlignRight
                        HalButton {
                            text: "DENY"
                            danger: true
                            onClicked: root.approvalAnswered(approvalDelegate.requestId, "deny")
                        }
                        HalButton {
                            text: "ALLOW ONCE"
                            onClicked: root.approvalAnswered(approvalDelegate.requestId, "allow")
                        }
                    }
                }
            }
        }

        ListView {
            id: activity
            Layout.fillWidth: true
            Layout.preferredHeight: count > 0 ? Math.min(78, contentHeight) : 0
            visible: count > 0
            model: activityModel
            clip: true
            spacing: 2

            delegate: RowLayout {
                id: activityDelegate
                required property string label
                required property string detail
                required property string status
                width: activity.width
                height: 21
                spacing: 7
                StatusLamp { status: activityDelegate.status; diameter: 6 }
                Text {
                    text: activityDelegate.label
                    color: HalTheme.muted
                    font.family: HalTheme.controlFont
                    font.pixelSize: 8
                    font.letterSpacing: 0.8
                }
                Text {
                    Layout.fillWidth: true
                    text: activityDelegate.detail
                    color: HalTheme.dim
                    font.family: HalTheme.controlFont
                    font.pixelSize: 8
                    elide: Text.ElideRight
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            Layout.minimumHeight: 46
            Layout.maximumHeight: 46
            Layout.fillHeight: false
            spacing: 6

            TextField {
                id: prompt
                objectName: "manualPrompt"
                Layout.fillWidth: true
                Layout.preferredHeight: 46
                placeholderText: "MANUAL INPUT"
                color: HalTheme.text
                placeholderTextColor: HalTheme.dim
                selectionColor: HalTheme.redDeep
                selectedTextColor: HalTheme.text
                font.family: HalTheme.controlFont
                font.pixelSize: 12
                leftPadding: 12
                rightPadding: 12
                background: Rectangle {
                    color: "#101111"
                    border.width: 1
                    border.color: prompt.activeFocus ? HalTheme.steel : HalTheme.line
                    radius: HalTheme.radiusSmall
                }
                Keys.onReturnPressed: event => {
                    if (event.modifiers & Qt.ControlModifier) {
                        root.submit()
                        event.accepted = true
                    }
                }
                onTextEdited: root.interaction()
            }

            HalButton { text: "SEND"; onClicked: { root.interaction(); root.submit() } }
            HalButton {
                text: root.microphoneMuted ? "MIC OFF" : "MIC"
                danger: root.microphoneMuted
                onClicked: { root.interaction(); root.microphoneRequested() }
            }
            HalButton { text: "STOP RUN"; danger: true; onClicked: { root.interaction(); root.stopGenerationRequested() } }
            HalButton { text: "STOP VOICE"; onClicked: { root.interaction(); root.stopSpeechRequested() } }
        }
    }

    function submit() {
        const value = prompt.text.trim()
        if (!value)
            return
        root.sendText(value)
        prompt.clear()
    }

    function focusPrompt() {
        prompt.forceActiveFocus()
    }
}
