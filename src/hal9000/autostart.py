"""Freedesktop login-autostart integration."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path


AUTOSTART_NAME = "com.bitloop.HAL9000.desktop"


def autostart_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "autostart" / AUTOSTART_NAME


def launch_command() -> list[str]:
    installed = shutil.which("hal9000")
    if installed:
        return [installed]
    return [sys.executable, "-m", "hal9000"]


def set_launch_on_login(enabled: bool) -> Path:
    destination = autostart_path()
    if not enabled:
        destination.unlink(missing_ok=True)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = shlex.join([*launch_command(), "--windowed"])
    payload = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=HAL 9000\n"
        "Comment=HAL 9000 desktop companion for Hermes Agent\n"
        f"Exec={command}\n"
        "Icon=com.bitloop.HAL9000\n"
        "Terminal=false\n"
        "Categories=Utility;AudioVideo;\n"
        "StartupNotify=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    temporary = destination.with_suffix(".desktop.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(destination)
    return destination
