"""Compact causal episodes; never transcript dumps or hidden reasoning."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.events.redact import redact_data, redact_text
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: str
    kind: str
    subject: str
    started_at: str
    ended_at: str | None
    state: str
    observations: tuple[dict, ...]
    inferences: tuple[dict, ...]
    actions: tuple[dict, ...]
    outcome: dict | None
    unresolved: tuple[dict, ...]
    event_run_refs: tuple[str, ...]
    exact_event_refs: tuple[str, ...]
    summary: str
    confidence: float


class EpisodeStore:
    def __init__(self, database: SentienceDatabase) -> None:
        self.database = database

    def get(self, episode_id: str) -> Episode | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE episode_id=?", (episode_id,)
            ).fetchone()
        if row is None:
            return None
        return Episode(
            str(row["episode_id"]),
            str(row["kind"]),
            str(row["subject"]),
            str(row["started_at"]),
            str(row["ended_at"]) if row["ended_at"] else None,
            str(row["state"]),
            tuple(json.loads(row["observations_json"])),
            tuple(json.loads(row["inferences_json"])),
            tuple(json.loads(row["actions_json"])),
            json.loads(row["outcome_json"]) if row["outcome_json"] else None,
            tuple(json.loads(row["unresolved_json"])),
            tuple(json.loads(row["event_run_refs_json"])),
            tuple(json.loads(row["exact_event_refs_json"])),
            str(row["summary"]),
            float(row["confidence"]),
        )

    def create(
        self,
        *,
        kind: str,
        subject: str,
        started_at: str,
        ended_at: str | None,
        state: str,
        observations: tuple[dict, ...],
        inferences: tuple[dict, ...],
        actions: tuple[dict, ...],
        outcome: dict | None,
        unresolved: tuple[dict, ...],
        event_run_refs: tuple[str, ...],
        exact_event_refs: tuple[str, ...],
        summary: str,
        confidence: float,
        input_watermark_start: int,
        input_watermark_end: int,
        task_id: str | None = None,
        compaction_version: int = 1,
        summary_model: str | None = None,
        summary_prompt_version: str | None = None,
    ) -> Episode:
        for observation in observations:
            if not observation.get("evidence_ref") and not observation.get("evidence_refs"):
                raise ValueError("every episode observation requires evidence")
        for inference in inferences:
            if not inference.get("evidence_refs"):
                raise ValueError("every episode inference requires evidence references")
        if outcome and outcome.get("verified") and not outcome.get("evidence_refs"):
            raise ValueError("a verified episode outcome requires evidence references")
        clean_subject = redact_text(subject)[:1000]
        clean_observations = tuple(redact_data(observations))
        clean_inferences = tuple(redact_data(inferences))
        clean_actions = tuple(redact_data(actions))
        clean_outcome = redact_data(outcome) if outcome else None
        clean_unresolved = tuple(redact_data(unresolved))
        clean_summary = redact_text(summary)[:20000]
        identity = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hal-episode:{kind}:{subject}:{compaction_version}:"
                f"{input_watermark_start}:{input_watermark_end}",
            )
        )
        evidence_refs = tuple(
            [f"event:{reference}" for reference in exact_event_refs]
            + [f"event-run:{reference}" for reference in event_run_refs]
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO episodes(episode_id,kind,subject,task_id,started_at,ended_at,state,"
                "observations_json,inferences_json,actions_json,outcome_json,unresolved_json,"
                "event_run_refs_json,exact_event_refs_json,summary,confidence,summary_model,"
                "summary_prompt_version,compaction_version,input_watermark_start,input_watermark_end) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(kind,subject,compaction_version,input_watermark_start,input_watermark_end) "
                "DO NOTHING",
                (
                    identity,
                    kind,
                    clean_subject,
                    task_id,
                    started_at,
                    ended_at,
                    state,
                    json.dumps(clean_observations, sort_keys=True, separators=(",", ":")),
                    json.dumps(clean_inferences, sort_keys=True, separators=(",", ":")),
                    json.dumps(clean_actions, sort_keys=True, separators=(",", ":")),
                    json.dumps(clean_outcome, sort_keys=True, separators=(",", ":")) if clean_outcome else None,
                    json.dumps(clean_unresolved, sort_keys=True, separators=(",", ":")),
                    json.dumps(event_run_refs),
                    json.dumps(exact_event_refs),
                    clean_summary,
                    confidence,
                    summary_model,
                    summary_prompt_version,
                    compaction_version,
                    input_watermark_start,
                    input_watermark_end,
                ),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO episode_evidence(episode_id,evidence_ref) VALUES(?,?)",
                ((identity, reference) for reference in evidence_refs),
            )
            connection.execute(
                "INSERT INTO retrieval_documents(reference,source_table,source_id,document_kind,"
                "title,body,subject,task_id,confidence,exact,created_at,updated_at,metadata_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_table,source_id) DO UPDATE SET body=excluded.body,"
                "updated_at=excluded.updated_at,confidence=excluded.confidence",
                (
                    f"episode:{identity}",
                    "episodes",
                    identity,
                    "episode",
                    kind.replace("_", " "),
                    clean_summary,
                    clean_subject,
                    task_id,
                    confidence,
                    0,
                    started_at,
                    ended_at or started_at,
                    '{"untrusted":false}',
                ),
            )
        result = self.get(identity)
        if result is None:
            raise RuntimeError("episode transaction committed without a readable episode")
        return result
