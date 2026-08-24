"""Structured Hermes Gateway integration for the machine-self subsystem."""

from .event_mapper import HermesEventMapper
from .gateway_adapter import SelfMcpRegistration

__all__ = ["HermesEventMapper", "SelfMcpRegistration"]
