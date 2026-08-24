"""Exact dependency-graph queries over persisted capability definitions."""

from __future__ import annotations

from hal9000.sentience.storage.database import SentienceDatabase


class CapabilityGraph:
    def __init__(self, database: SentienceDatabase) -> None:
        self.database = database

    def dependencies(self, capability_id: str, *, max_depth: int = 8) -> tuple[str, ...]:
        if max_depth < 0 or max_depth > 32:
            raise ValueError("capability graph depth must be between 0 and 32")
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "WITH RECURSIVE deps(id,depth) AS ("
                "SELECT required_capability,1 FROM capability_edges WHERE parent_capability=? "
                "UNION SELECT e.required_capability,deps.depth+1 FROM capability_edges e "
                "JOIN deps ON e.parent_capability=deps.id WHERE deps.depth<?) "
                "SELECT DISTINCT id FROM deps ORDER BY id LIMIT 256",
                (capability_id, max_depth),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)
