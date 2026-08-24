"""XDG paths used by HAL without hard-coded home directories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs


@dataclass(frozen=True, slots=True)
class AppPaths:
    config: Path
    data: Path
    state: Path
    cache: Path
    logs: Path

    @classmethod
    def discover(cls) -> "AppPaths":
        dirs = PlatformDirs(appname="hal9000", appauthor=False, ensure_exists=False)
        state = Path(dirs.user_state_dir)
        return cls(
            config=Path(dirs.user_config_dir),
            data=Path(dirs.user_data_dir),
            state=state,
            cache=Path(dirs.user_cache_dir),
            logs=state / "logs",
        )

    def ensure(self) -> None:
        for path in (self.config, self.data, self.state, self.cache, self.logs):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def config_file(self) -> Path:
        return self.config / "config.json"

    @property
    def log_file(self) -> Path:
        return self.logs / "hal9000.log"

    @property
    def model_cache(self) -> Path:
        return self.cache / "models"
