from __future__ import annotations

from hal9000.autostart import set_launch_on_login


def test_login_autostart_is_reversible(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    destination = set_launch_on_login(True)
    payload = destination.read_text(encoding="utf-8")
    assert "Name=HAL 9000" in payload
    assert "Exec=" in payload
    assert destination.stat().st_mode & 0o777 == 0o644
    set_launch_on_login(False)
    assert not destination.exists()
