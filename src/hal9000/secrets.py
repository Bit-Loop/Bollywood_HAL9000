"""Non-config-file storage for optional remote Hermes credentials."""

from __future__ import annotations

import logging
import os


class SecretStore:
    SERVICE = "com.bitloop.HAL9000"
    HERMES_ACCOUNT = "hermes-backend-token"

    def get_hermes_token(self) -> str:
        environment_token = os.environ.get("HAL9000_HERMES_TOKEN", "").strip()
        if environment_token:
            return environment_token
        try:
            import keyring

            return (keyring.get_password(self.SERVICE, self.HERMES_ACCOUNT) or "").strip()
        except Exception as exc:
            logging.getLogger("hal9000.secrets").warning(
                "The desktop keyring could not be read: %s", exc
            )
            return ""

    def set_hermes_token(self, token: str) -> None:
        import keyring

        clean = token.strip()
        if clean:
            keyring.set_password(self.SERVICE, self.HERMES_ACCOUNT, clean)
        else:
            try:
                keyring.delete_password(self.SERVICE, self.HERMES_ACCOUNT)
            except keyring.errors.PasswordDeleteError:
                return
