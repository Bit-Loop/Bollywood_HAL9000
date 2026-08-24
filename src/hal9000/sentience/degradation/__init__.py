"""Exact capability degradation and recovery state machine."""

from .engine import DegradationEngine
from .outbox import OutboxDispatcher

__all__ = ["DegradationEngine", "OutboxDispatcher"]
