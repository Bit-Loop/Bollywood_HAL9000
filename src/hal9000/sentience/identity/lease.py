"""Renewable canonical-instance lease preventing duplicate HAL writers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.storage.database import SentienceDatabase


class LeaseConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LeaseObservation:
    instance_id: str
    writer_owner: str | None
    expires_at: str | None
    stale: bool
    read_only: bool = True


class CanonicalLease:
    def __init__(
        self,
        database: SentienceDatabase,
        *,
        instance_id: str,
        boot_id: str,
        owner_id: str,
        ttl_seconds: int = 10,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds < 2:
            raise ValueError("canonical lease TTL must be at least two seconds")
        self.database = database
        self.instance_id = instance_id
        self.boot_id = boot_id
        self.owner_id = owner_id
        self.ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self.held = False

    def acquire(self) -> bool:
        now = self._now().astimezone(UTC)
        expires = now + timedelta(seconds=self.ttl_seconds)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM instance_leases WHERE instance_id=?", (self.instance_id,)
            ).fetchone()
            if row is not None:
                expiry = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if row["owner_id"] != self.owner_id and expiry > now:
                    raise LeaseConflict(
                        f"canonical instance {self.instance_id} is held by {row['owner_id']}"
                    )
            connection.execute(
                "INSERT INTO instance_leases(instance_id,owner_id,mode,acquired_at,renewed_at,"
                "expires_at,boot_id,process_id) VALUES(?,?,'writer',?,?,?,?,?) "
                "ON CONFLICT(instance_id) DO UPDATE SET owner_id=excluded.owner_id,"
                "mode='writer',acquired_at=excluded.acquired_at,renewed_at=excluded.renewed_at,"
                "expires_at=excluded.expires_at,boot_id=excluded.boot_id,process_id=excluded.process_id",
                (
                    self.instance_id,
                    self.owner_id,
                    utc_iso(now),
                    utc_iso(now),
                    utc_iso(expires),
                    self.boot_id,
                    os.getpid(),
                ),
            )
        self.held = True
        return True

    def renew(self) -> bool:
        now = self._now().astimezone(UTC)
        expires = now + timedelta(seconds=self.ttl_seconds)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE instance_leases SET renewed_at=?,expires_at=?,process_id=? "
                "WHERE instance_id=? AND owner_id=?",
                (utc_iso(now), utc_iso(expires), os.getpid(), self.instance_id, self.owner_id),
            )
        self.held = cursor.rowcount == 1
        return self.held

    def release(self) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM instance_leases WHERE instance_id=? AND owner_id=?",
                (self.instance_id, self.owner_id),
            )
        self.held = False
        return cursor.rowcount == 1

    def observe(self) -> LeaseObservation:
        now = self._now().astimezone(UTC)
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT owner_id,expires_at FROM instance_leases WHERE instance_id=?",
                (self.instance_id,),
            ).fetchone()
        if row is None:
            return LeaseObservation(self.instance_id, None, None, True)
        expiry = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        return LeaseObservation(
            self.instance_id,
            str(row["owner_id"]),
            str(row["expires_at"]),
            expiry <= now,
        )
