"""Compatibility facade for exact capability projections."""

from hal9000.sentience.capabilities.registry import CapabilityRegistry

CapabilityProjector = CapabilityRegistry

__all__ = ["CapabilityProjector"]
