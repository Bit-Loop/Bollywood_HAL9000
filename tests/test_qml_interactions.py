from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
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
