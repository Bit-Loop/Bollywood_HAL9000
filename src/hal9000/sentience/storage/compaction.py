"""Storage-facing entrypoint for idempotent causal memory compaction."""

from hal9000.sentience.memory.consolidation import MemoryConsolidator

CompactionService = MemoryConsolidator

__all__ = ["CompactionService"]
