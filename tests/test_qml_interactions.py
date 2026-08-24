from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from hal9000.config import AppConfig, ConfigStore
from hal9000.controller import HalController
from hal9000.paths import AppPaths


def build_ui(qtbot, tmp_path):
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    paths.ensure()
    config = AppConfig()
    config.general.setup_complete = True
    controller = HalController(paths, ConfigStore(paths), config, tmp_path, services_enabled=False)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("controller", controller)
    engine.rootContext().setContextProperty("conversationsModel", controller.conversations)
    engine.rootContext().setContextProperty("activityModel", controller.activities)
    engine.rootContext().setContextProperty("approvalModel", controller.approvals)
    engine.rootContext().setContextProperty("audioDevices", controller.audio_devices)
    qml = Path(__file__).parents[1] / "src" / "hal9000" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.show()
    qtbot.waitUntil(lambda: controller.state == "STANDBY", timeout=1500)
    return controller, engine, window


def close_ui(qtbot, controller, engine, window) -> None:
    window.close()
    controller.shutdown()
    qtbot.wait(50)
    engine.deleteLater()
    qtbot.wait(20)
    controller.deleteLater()
    qtbot.wait(20)


def test_ctrl_shift_s_opens_settings_from_closed_console(qtbot, tmp_path) -> None:
    controller, engine, window = build_ui(qtbot, tmp_path)
    try:
        assert controller.settingsOpen is False
        QTest.keyClick(
            window,
            Qt.Key.Key_S,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        qtbot.waitUntil(lambda: controller.settingsOpen, timeout=700)
        assert window.findChild(QObject, "postalCodeField") is not None
        assert window.findChild(QObject, "hermesModelSelector") is not None
    finally:
        close_ui(qtbot, controller, engine, window)


def test_qml_setting_signal_accepts_javascript_primitive_values(qtbot, tmp_path) -> None:
    controller, engine, window = build_ui(qtbot, tmp_path)
    try:
        selector = window.findChild(QObject, "backendModeSelector")
        assert selector is not None

        selector.setProperty("currentIndex", 1)
        selector.activated.emit(1)

        qtbot.waitUntil(lambda: controller.config.hermes.mode == "remote", timeout=700)
    finally:
        close_ui(qtbot, controller, engine, window)


def test_right_click_empty_chassis_opens_settings_but_controls_do_not(qtbot, tmp_path) -> None:
    controller, engine, window = build_ui(qtbot, tmp_path)
    try:
        console = window.findChild(QObject, "consoleFrame")
        eye = window.findChild(QObject, "halEye")
        speaker = window.findChild(QObject, "speakerAssembly")
        assert console is not None and eye is not None and speaker is not None

        empty_point = QPoint(
            int(console.property("x") + console.property("width") * 0.5),
            int(console.property("y") + console.property("height") * 0.2),
        )
        QTest.mouseClick(window, Qt.MouseButton.RightButton, pos=empty_point)
        qtbot.waitUntil(lambda: controller.settingsOpen, timeout=700)

        controller.closeSettings()
        eye_point = QPoint(
            int(eye.property("x") + eye.property("width") * 0.5),
            int(eye.property("y") + eye.property("height") * 0.5),
        )
        QTest.mouseClick(window, Qt.MouseButton.RightButton, pos=eye_point)
        QTest.qWait(80)
        assert controller.settingsOpen is False

        speaker_point = QPoint(
            int(speaker.property("x") + speaker.property("width") * 0.5),
            int(speaker.property("y") + speaker.property("height") * 0.5),
        )
        QTest.mouseClick(window, Qt.MouseButton.RightButton, pos=speaker_point)
        QTest.qWait(80)
        assert controller.settingsOpen is False
    finally:
        close_ui(qtbot, controller, engine, window)


def test_double_then_triple_speaker_control_never_reopens_drawer(qtbot, tmp_path) -> None:
    controller, engine, window = build_ui(qtbot, tmp_path)
    try:
        controller.speakerClick()
        controller.speakerClick()
        qtbot.waitUntil(lambda: controller.manualOpen, timeout=800)
        for _ in range(3):
            controller.speakerClick()
        qtbot.waitUntil(lambda: not controller.manualOpen, timeout=400)
        qtbot.wait(420)
        assert controller.manualOpen is False
        assert controller.state == "STANDBY"
    finally:
        close_ui(qtbot, controller, engine, window)


def test_required_responsive_sizes_keep_physical_components_in_bounds(qtbot, tmp_path) -> None:
    controller, engine, window = build_ui(qtbot, tmp_path)
    try:
        components = [
            window.findChild(QObject, "halHeader"),
            window.findChild(QObject, "halEye"),
            window.findChild(QObject, "speakerAssembly"),
        ]
        assert all(components)
        for width, height in (
            (1080, 1920),
            (900, 1600),
            (720, 1280),
            (800, 1000),
            (1280, 900),
            (600, 800),
        ):
            window.setWidth(width)
            window.setHeight(height)
            QTest.qWait(30)
            for item in components:
                assert item.property("width") > 0
                assert item.property("height") > 0
                assert item.property("x") >= 0
                assert item.property("y") >= 0
                assert item.property("x") + item.property("width") <= width + 1
                assert item.property("y") + item.property("height") <= height + 1
    finally:
        close_ui(qtbot, controller, engine, window)


def test_hal_and_9000_are_centered_in_their_own_nameplate_fields(qtbot, tmp_path) -> None:
    controller, engine, window = build_ui(qtbot, tmp_path)
    try:
        blue = window.findChild(QObject, "headerBlueField")
        black = window.findChild(QObject, "headerBlackField")
        hal = window.findChild(QObject, "headerHalLabel")
        model = window.findChild(QObject, "header9000Label")
        assert all((blue, black, hal, model))

        hal_center = hal.property("x") + hal.property("width") / 2
        blue_center = blue.property("x") + blue.property("width") / 2
        model_center = model.property("x") + model.property("width") / 2
        black_center = black.property("x") + black.property("width") / 2

        assert abs(hal_center - blue_center) <= 1.5
        assert abs(model_center - black_center) <= 1.5
    finally:
        close_ui(qtbot, controller, engine, window)


def test_transcript_text_can_be_selected_copied_and_pasted(qtbot, tmp_path) -> None:
    controller, engine, window = build_ui(qtbot, tmp_path)
    sample = "Copy this HAL transcript into the manual prompt."
    try:
        controller.openManual()
        qtbot.waitUntil(lambda: controller.manualOpen, timeout=700)
        qtbot.wait(600)
        controller.conversations.append(
            {
                "role": "assistant",
                "text": sample,
                "streaming": False,
                "error": False,
                "timestamp": "12:00",
            }
        )

        def visual_descendants(item):
            for child in item.childItems():
                yield child
                yield from visual_descendants(child)

        def transcript_message():
            transcript_view = next(
                child
                for child in window.findChildren(QQuickItem)
                if child.metaObject().className() == "QQuickListView"
                and child.property("count") == 1
                and child.property("width") > 0
            )
            return next(
                (
                    child
                    for child in visual_descendants(transcript_view)
                    if child.property("text") == sample
                    and child.property("selectByMouse") is not None
                ),
                None,
            )

        qtbot.waitUntil(lambda: transcript_message() is not None, timeout=1000)
        message = transcript_message()
        assert message is not None
        assert message.property("selectByMouse") is True

        context = QQmlEngine.contextForObject(message)
        select_all = QQmlExpression(context, message, "selectAll()")
        select_all.evaluate()
        assert not select_all.hasError(), select_all.error().toString()
        assert message.property("selectedText") == sample

        clipboard = QGuiApplication.clipboard()
        clipboard.clear()
        focus_message = QQmlExpression(context, message, "forceActiveFocus()")
        focus_message.evaluate()
        assert not focus_message.hasError(), focus_message.error().toString()
        QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        qtbot.waitUntil(lambda: clipboard.text() == sample, timeout=700)

        prompt = window.findChild(QObject, "manualPrompt")
        assert prompt is not None
        prompt_context = QQmlEngine.contextForObject(prompt)
        focus_prompt = QQmlExpression(prompt_context, prompt, "forceActiveFocus()")
        focus_prompt.evaluate()
        assert not focus_prompt.hasError(), focus_prompt.error().toString()
        QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        qtbot.waitUntil(lambda: prompt.property("text") == sample, timeout=700)
    finally:
        close_ui(qtbot, controller, engine, window)
