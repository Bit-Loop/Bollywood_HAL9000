"""HLL public surface; callers use the crash-safe hybrid distinct bucket."""

from hal9000.sentience.sketches.hybrid_distinct import (
    HybridDistinctBucket,
    HybridMode,
    IncompatibleSketchError,
)

__all__ = ["HybridDistinctBucket", "HybridMode", "IncompatibleSketchError"]
