from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.identity.continuity import ContinuityService
from hal9000.sentience.identity.lease import CanonicalLease, LeaseConflict
from hal9000.sentience.identity.service import IdentityService
from hal9000.sentience.storage.database import SentienceDatabase


def database_for(tmp_path) -> SentienceDatabase:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    return SentienceDatabase.open(paths, AppConfig().sentience)


def test_identity_is_stable_while_incarnation_and_boot_are_new(tmp_path) -> None:
    database = database_for(tmp_path)
    try:
        first = IdentityService(database, canonical_name="HAL").load_or_create()
        second = IdentityService(database, canonical_name="HAL").load_or_create()

        assert first.instance_id == second.instance_id
        assert first.lineage_id == second.lineage_id
        assert first.incarnation_id != second.incarnation_id
        assert first.lineage_verified is True
        assert database.count("identity_state") == 1
        assert database.count("exact_events") == 2
    finally:
        database.close()


def test_only_one_writer_can_hold_canonical_lease_and_observer_is_read_only(tmp_path) -> None:
    database = database_for(tmp_path)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    first = CanonicalLease(
        database,
        instance_id="hal-test",
        boot_id="133a2f43-eb3b-42ac-a76e-f10d04535712",
        owner_id="writer-a",
        ttl_seconds=10,
        now=lambda: now,
    )
    second = CanonicalLease(
        database,
        instance_id="hal-test",
        boot_id="1f496411-d895-476c-805c-501f77c68136",
        owner_id="writer-b",
        ttl_seconds=10,
        now=lambda: now,
    )
    try:
        assert first.acquire() is True
        with pytest.raises(LeaseConflict, match="writer-a"):
            second.acquire()
        observer = second.observe()
        assert observer.writer_owner == "writer-a"
        assert observer.read_only is True

        now = now + timedelta(seconds=11)
        second._now = lambda: now
        assert second.acquire() is True
        assert second.renew() is True
        assert first.renew() is False
    finally:
        second.release()
        database.close()


def test_unclean_boot_marks_interrupted_tasks_and_uncertain_actions(tmp_path) -> None:
    database = database_for(tmp_path)
    identity = IdentityService(database, canonical_name="HAL").load_or_create()
    first = ContinuityService(database, identity.incarnation_id)
    boot_one = first.start_boot()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO tasks(task_id,title,state,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("task-running", "running", "active", boot_one.started_at, boot_one.started_at),
        )
        connection.execute(
            "INSERT INTO consequential_actions(action_id,task_id,action_type,target,state,"
            "started_at,event_id) VALUES(?,?,?,?,?,?,?)",
            (
                "action-running",
                "task-running",
                "filesystem.write",
                "/tmp/example",
                "running",
                boot_one.started_at,
                "event-action",
            ),
        )

    identity_two = IdentityService(database, canonical_name="HAL").load_or_create()
    second = ContinuityService(database, identity_two.incarnation_id)
    boot_two = second.start_boot()
    status = second.status()

    try:
        assert boot_two.recovered_from_unclean_shutdown is True
        assert status.state == "recovered_with_uncertainty"
        with database.read_connection() as connection:
            task = connection.execute(
                "SELECT state,interrupted_at FROM tasks WHERE task_id='task-running'"
            ).fetchone()
            action = connection.execute(
                "SELECT state,uncertainty_reason FROM consequential_actions "
                "WHERE action_id='action-running'"
            ).fetchone()
        assert tuple(task) == ("interrupted", boot_two.started_at)
        assert action["state"] == "uncertain"
        assert "unclean shutdown" in action["uncertainty_reason"]
        assert second.finish_boot(clean=True) is True
        assert second.finish_boot(clean=True) is False
        with database.read_connection() as connection:
            recovered_boot = connection.execute(
                "SELECT ended_at,shutdown_clean,recovery_state FROM boot_sessions WHERE boot_id=?",
                (boot_one.boot_id,),
            ).fetchone()
        assert recovered_boot["ended_at"] == boot_two.started_at
        assert recovered_boot["shutdown_clean"] == 0
        assert recovered_boot["recovery_state"] == "superseded_unclean"

        identity_three = IdentityService(database, canonical_name="HAL").load_or_create()
        third = ContinuityService(database, identity_three.incarnation_id)
        assert third.start_boot().recovered_from_unclean_shutdown is False
    finally:
        database.close()
