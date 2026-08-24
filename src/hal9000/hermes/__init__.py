"""Hermes Agent discovery, process lifecycle, and gateway protocol."""

from hal9000.hermes.discovery import HermesInstallation, discover_hermes
from hal9000.hermes.service import HermesService

__all__ = ["HermesInstallation", "HermesService", "discover_hermes"]
