"""Deterministic, watermark-idempotent event-to-episode consolidation."""

from __future__ import annotations

import json
import uuid

from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.memory.episodes import Episode, EpisodeStore
from hal9000.sentience.storage.database import SentienceDatabase


class MemoryConsolidator:
    def __init__(self, database: SentienceDatabase, *, version: int = 1) -> None:
        self.database = database
        self.version = version
        self.episodes = EpisodeStore(database)

    def consolidate_task(self, task_id: str) -> Episode | None:
        with self.database.read_connection() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            watermark = int(
                connection.execute(
                    "SELECT COALESCE(MAX(input_watermark_end),0) FROM episodes WHERE "
                    "task_id=? AND kind='task_history' AND compaction_version=?",
                    (task_id, self.version),
                ).fetchone()[0]
            )
            events = connection.execute(
                "SELECT sequence,event_id,type,subject,occurred_at_utc,origin,confidence,payload_json "
                "FROM exact_events WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT 1000",
                (task_id, watermark),
            ).fetchall()
        if task is None or not events:
            return None
        low, high = int(events[0]["sequence"]), int(events[-1]["sequence"])
        with self.database.read_connection() as connection:
            runs = connection.execute(
                "SELECT run_id,type,subject,first_seen,last_seen,count,severity_max,normalized_template "
                "FROM event_runs WHERE task_id=? AND last_seen>=? AND first_seen<=? "
                "ORDER BY first_seen LIMIT 500",
                (
                    task_id,
                    str(events[0]["occurred_at_utc"]),
                    str(events[-1]["occurred_at_utc"]),
                ),
            ).fetchall()
        job_kind = f"task_episode:{task_id}"
        job_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hal-compaction:task:{task_id}:{self.version}:{low}:{high}",
            )
        )
        with self.database.read_connection() as connection:
            existing = connection.execute(
                "SELECT output_refs_json,state FROM compaction_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if existing and existing["state"] == "complete":
            references = json.loads(existing["output_refs_json"])
            return self.episodes.get(references[0]) if references else None
        observations = tuple(
            {
                "type": row["type"],
                "subject": row["subject"],
                "origin": row["origin"],
                "confidence": row["confidence"],
                "evidence_ref": f"event:{row['event_id']}",
            }
            for row in events[:100]
            if not str(row["type"]).startswith("action.")
        )
        actions = tuple(
            {
                "type": row["type"],
                "subject": row["subject"],
                "evidence_ref": f"event:{row['event_id']}",
            }
            for row in events[:100]
            if str(row["type"]).startswith(("action.", "tool."))
        )
        unresolved_raw = json.loads(task["unresolved_json"] or "[]")
        unresolved = tuple(
            item if isinstance(item, dict) else {"statement": str(item)}
            for item in unresolved_raw[:50]
        )
        summary = (
            f"Task {task['title']} is {task['state']}; {len(events)} exact transitions and "
            f"{len(runs)} coalesced observation runs are retained with evidence."
        )
        episode = self.episodes.create(
            kind="task_history",
            subject=task_id,
            task_id=task_id,
            started_at=str(task["created_at"]),
            ended_at=str(task["completed_at"] or task["interrupted_at"] or task["updated_at"]),
            state=str(task["state"]),
            observations=observations,
            inferences=(),
            actions=actions,
            outcome={
                "statement": f"Task state is {task['state']}.",
                "verified": bool(task["exact_completion_event_id"]),
                "evidence_refs": (
                    [f"event:{task['exact_completion_event_id']}"]
                    if task["exact_completion_event_id"]
                    else []
                ),
            },
            unresolved=unresolved,
            event_run_refs=tuple(str(row["run_id"]) for row in runs),
            exact_event_refs=tuple(str(row["event_id"]) for row in events),
            summary=summary,
            confidence=1.0,
            input_watermark_start=low,
            input_watermark_end=high,
            compaction_version=self.version,
        )
        now = utc_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO compaction_jobs(job_id,job_kind,algorithm_version,input_watermark_start,"
                "input_watermark_end,state,started_at,completed_at,output_refs_json) "
                "VALUES(?,?,?,?,?,'complete',?,?,?) "
                "ON CONFLICT(job_kind,algorithm_version,input_watermark_start,input_watermark_end) "
                "DO UPDATE SET state='complete',completed_at=excluded.completed_at,"
                "output_refs_json=excluded.output_refs_json",
                (
                    job_id,
                    job_kind,
                    self.version,
                    low,
                    high,
                    now,
                    now,
                    json.dumps([episode.episode_id]),
                ),
            )
        return episode

    def consolidate_due(self, *, maximum_tasks: int = 32) -> tuple[Episode, ...]:
        """Advance a bounded page for recently terminal/interrupted tasks."""

        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT task_id FROM tasks WHERE state IN "
                "('completed','completed_unverified','interrupted','cancelled') "
                "ORDER BY updated_at LIMIT ?",
                (max(1, min(256, maximum_tasks)),),
            ).fetchall()
        episodes: list[Episode] = []
        for row in rows:
            episode = self.consolidate_task(str(row["task_id"]))
            if episode is not None:
                episodes.append(episode)
        return tuple(episodes)
