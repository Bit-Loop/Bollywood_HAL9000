"""Native Qt application entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from hal9000 import __version__
from hal9000.config import ConfigStore
from hal9000.controller import HalController
from hal9000.logging_setup import configure_logging
from hal9000.paths import AppPaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HAL 9000 desktop companion for Hermes Agent")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fullscreen", action="store_true", help="Start fullscreen")
    mode.add_argument("--windowed", action="store_true", help="Start in a normal window")
    parser.add_argument("--size", metavar="WIDTHxHEIGHT", help="Initial window size")
    parser.add_argument("--screenshot", type=Path, help="Capture the rendered window and exit")
    parser.add_argument("--quit-after", type=float, default=0.0, metavar="SECONDS")
    parser.add_argument("--no-services", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--open-manual", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--open-settings", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"HAL 9000 {__version__}")
    return parser


def _parse_size(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        width, height = value.lower().split("x", 1)
        return max(600, int(width)), max(800, int(height))
    except (ValueError, TypeError):
        raise SystemExit(f"Invalid --size {value!r}; expected WIDTHxHEIGHT") from None


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    size = _parse_size(arguments.size)
    paths = AppPaths.discover()
    paths.ensure()
    logger = configure_logging(paths, arguments.verbose)
    store = ConfigStore(paths)
    config = store.load()
    if size:
        config.general.window_width, config.general.window_height = size
    if arguments.fullscreen:
        config.general.last_fullscreen = True
    elif arguments.windowed:
        config.general.last_fullscreen = False
    elif config.general.launch_mode == "fullscreen":
        config.general.last_fullscreen = True
    elif config.general.launch_mode == "windowed":
        config.general.last_fullscreen = False

    app = QApplication(sys.argv[:1])
    app.setApplicationName("HAL 9000")
    app.setApplicationDisplayName("HAL 9000")
    app.setOrganizationName("Bit-Loop")
    app.setDesktopFileName("com.bitloop.HAL9000")
    resource_root = Path(__file__).resolve().parent
    icon_path = resource_root / "resources" / "hal9000.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    controller = HalController(
        paths,
        store,
        config,
        Path.cwd(),
        services_enabled=not arguments.no_services,
    )
    engine = QQmlApplicationEngine()
    engine.warnings.connect(
        lambda warnings: [logger.error("QML: %s", warning.toString()) for warning in warnings]
    )
    engine.rootContext().setContextProperty("controller", controller)
    engine.rootContext().setContextProperty("conversationsModel", controller.conversations)
    engine.rootContext().setContextProperty("activityModel", controller.activities)
    engine.rootContext().setContextProperty("approvalModel", controller.approvals)
    engine.rootContext().setContextProperty("audioDevices", controller.audio_devices)
    engine.addImportPath(str(resource_root / "qml"))
    qml_file = resource_root / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file)))
    if not engine.rootObjects():
        logger.error("QML failed to create the application window")
        controller.shutdown()
        return 2
    window = engine.rootObjects()[0]

    target_screen = next(
        (screen for screen in app.screens() if screen.name() == config.general.target_monitor),
        None,
    )
    if target_screen is not None:
        window.setScreen(target_screen)
        if config.general.window_x is None or config.general.window_y is None:
            window.setPosition(target_screen.geometry().topLeft())
    if not controller.fullscreen and config.general.window_x is not None and config.general.window_y is not None:
        window.setPosition(config.general.window_x, config.general.window_y)
    if controller.fullscreen:
        window.showFullScreen()
    else:
        window.show()

    if arguments.open_manual:
        QTimer.singleShot(220, controller.openManual)
    if arguments.open_settings:
        QTimer.singleShot(220, controller.openSettings)

    app.applicationStateChanged.connect(
        lambda state: controller.applicationStateChanged(int(state.value))
    )

    if arguments.screenshot:
        destination = arguments.screenshot.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        def capture() -> None:
            screen = window.screen() or app.primaryScreen()
            pixmap = screen.grabWindow(window.winId()) if screen else None
            if pixmap is None or pixmap.isNull() or not pixmap.save(str(destination)):
                logger.error("Could not save screenshot to %s", destination)
                app.exit(3)
                return
            logger.info("Saved screenshot to %s", destination)
            app.quit()

        QTimer.singleShot(1600, capture)
    elif arguments.quit_after > 0:
        QTimer.singleShot(int(arguments.quit_after * 1000), app.quit)

    app.aboutToQuit.connect(controller.shutdown)
    exit_code = app.exec()
    # Destroy QML while its context objects are still alive.  Otherwise Python
    # may release the controller first after the event loop has stopped, which
    # produces a cascade of harmless-but-noisy null binding errors on exit.
    engine.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
