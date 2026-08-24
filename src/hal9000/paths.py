"""XDG paths used by HAL without hard-coded home directories."""

from __future__ import annotations

import os
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
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            # These directories may predate machine-self storage. HAL-owned
            # configuration, state, caches, and logs are user-private
            # regardless of the process umask or permissive XDG parents.
            os.chmod(path, 0o700)

    @property
    def config_file(self) -> Path:
        return self.config / "config.json"

    @property
    def log_file(self) -> Path:
        return self.logs / "hal9000.log"

    @property
    def model_cache(self) -> Path:
        return self.cache / "models"

    @property
    def sentience_root(self) -> Path:
        """Machine-self data isolated from installed models and virtualenvs."""

        return self.data / "machine-self"

    @property
    def sentience_database(self) -> Path:
        return self.sentience_root / "hal-state.sqlite"

    @property
    def sentience_blob_root(self) -> Path:
        return self.sentience_root / "blobs" / "sha256"

    @property
    def sentience_checkpoint_root(self) -> Path:
        return self.sentience_root / "checkpoints"

    @property
    def sentience_hmac_key(self) -> Path:
        return self.config / "sketch-hmac.key"
