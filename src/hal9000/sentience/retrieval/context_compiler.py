"""Compile the minimum evidence-backed, task-specific machine-self capsule."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hal9000.config import SentienceSettings
from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.interoception.calculator import (
    CapabilityInput,
    ContextInput,
    ContinuityInput,
    EpistemicInput,
    FailureInput,
    InteroceptionCalculator,
    InteroceptionInputs,
    ResourceInput,
)
from hal9000.sentience.interoception.model import MachineDimension
from hal9000.sentience.interoception.language import SparseInteroceptionLanguageGate
from hal9000.sentience.models import CapabilityLifecycle
from hal9000.sentience.retrieval.planner import MemoryQuery, MemoryRetriever
from hal9000.sentience.retrieval.token_budget import estimate_tokens
from hal9000.sentience.storage.database import SentienceDatabase
from hal9000.sentience.sketches.frequency import FrequencySketch
from hal9000.sentience.sketches.quantiles import QuantileSketch


@dataclass(frozen=True, slots=True)
class SelfCapsule:
    data: dict[str, Any]
    json: str
    token_count: int
    byte_count: int
    truncated: bool
    evidence_handles: tuple[str, ...]


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def _dimension(value: MachineDimension, *, compact: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": None if value.value is None else round(value.value, 4),
        "confidence": round(value.confidence, 3),
        "approximate": value.approximate,
        "fresh_at": value.observed_at,
    }
    if value.sources:
        result["sources"] = list(value.sources[:1] if compact else value.sources)
    if value.baseline_state is not None:
        result["baseline"] = value.baseline_state
    if value.lower_bound is not None:
        result["lower"] = round(value.lower_bound, 3)
    if value.upper_bound is not None:
        result["upper"] = round(value.upper_bound, 3)
    return result


class ContextCompiler:
    """Read exact projections first, then add only bounded relevant memory."""

    def __init__(self, database: SentienceDatabase, settings: SentienceSettings) -> None:
        self.database = database
        self.settings = settings
        self.retriever = MemoryRetriever(database, settings.retrieval)
        self.language_gate = SparseInteroceptionLanguageGate()

    def compile(
        self,
        *,
        task_id: str | None,
        query: str,
        token_budget: int | None = None,
        active_model_class: str | None = None,
        context_usage: ContextInput | None = None,
    ) -> SelfCapsule:
        requested = int(token_budget or self.settings.retrieval.self_capsule_tokens)
        if requested < 64:
            raise ValueError("self capsule token budget must be at least 64")
        now = utc_iso()
        with self.database.read_connection() as connection:
            identity = connection.execute(
                "SELECT * FROM identity_state WHERE singleton=1"
            ).fetchone()
            boot = connection.execute(
                "SELECT * FROM boot_sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            capabilities = connection.execute(
                "SELECT d.*,c.lifecycle_state,c.health,c.permission_scope,c.trust_state,"
                "c.confidence,c.observed_at,c.evidence_event_id,c.active_profile "
                "FROM capability_definitions d LEFT JOIN capability_current c "
                "ON c.capability_id=d.capability_id WHERE d.configured=1 "
                "ORDER BY d.capability_id"
            ).fetchall()
            task = (
                connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if task_id
                else connection.execute(
                    "SELECT * FROM tasks WHERE state IN ('active','running','focused','interrupted') "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            )
            selected_task_id = str(task["task_id"]) if task else None
            requirements = (
                connection.execute(
                    "SELECT r.*,c.lifecycle_state FROM task_capability_requirements r "
                    "LEFT JOIN capability_current c ON c.capability_id=r.capability_id "
                    "WHERE r.task_id=? ORDER BY r.capability_id",
                    (selected_task_id,),
                ).fetchall()
                if selected_task_id
                else []
            )
            degradation = connection.execute(
                "SELECT * FROM degradation_episodes WHERE state!='NOMINAL' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            open_commitments = int(
                connection.execute(
                    "SELECT count(*) FROM commitments WHERE state='open'"
                ).fetchone()[0]
            )
            open_contradictions = int(
                connection.execute(
                    "SELECT count(*) FROM contradictions WHERE state='open'"
                ).fetchone()[0]
            )
            stale_facts = int(
                connection.execute(
                    "SELECT count(*) FROM semantic_facts WHERE state='stale' OR "
                    "(stale_after IS NOT NULL AND stale_after < ?)",
                    (now,),
                ).fetchone()[0]
            )
            uncertain_actions = int(
                connection.execute(
                    "SELECT count(*) FROM consequential_actions WHERE state='uncertain'"
                ).fetchone()[0]
            )
            missing_evidence = int(
                connection.execute(
                    "SELECT count(*) FROM semantic_facts f WHERE NOT EXISTS "
                    "(SELECT 1 FROM fact_evidence e WHERE e.fact_id=f.fact_id)"
                ).fetchone()[0]
            )
            failure_bucket = connection.execute(
                "SELECT * FROM sketch_buckets WHERE metric_name='unique_error_fingerprints' "
                "ORDER BY bucket_start DESC LIMIT 1"
            ).fetchone()
            baseline = connection.execute(
                "SELECT * FROM baseline_versions WHERE metric_name='unique_error_fingerprints' "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
            lease = (
                connection.execute(
                    "SELECT expires_at FROM instance_leases WHERE instance_id=? AND mode='writer'",
                    (str(identity["instance_id"]),),
                ).fetchone()
                if identity
                else None
            )
            operational_rows = connection.execute(
                "SELECT * FROM operational_metrics_current ORDER BY metric_name,scope"
            ).fetchall()
            quantile_rows = connection.execute(
                "SELECT metric_name,scope,blob FROM sketch_buckets WHERE sketch_kind='kll_floats' "
                "AND (metric_name,scope,bucket_start) IN (SELECT metric_name,scope,"
                "MAX(bucket_start) FROM sketch_buckets WHERE sketch_kind='kll_floats' "
                "GROUP BY metric_name,scope)"
            ).fetchall()
            frequency_failure = connection.execute(
                "SELECT blob FROM sketch_buckets WHERE metric_name='repeating_error_fingerprints' "
                "AND sketch_kind='frequent_strings' ORDER BY bucket_start DESC LIMIT 1"
            ).fetchone()

        if identity is None:
            raise RuntimeError("machine identity has not been initialized")
        # Startup and bounded maintenance own integrity scans. Prompt
        # compilation consumes their exact materialized projection and never
        # walks lifetime history or runs SQLite integrity work on demand.
        integrity_verified = bool(identity["lineage_verified"]) and str(
            identity["integrity_state"]
        ) == "verified"
        chain_valid = integrity_verified
        database_valid = integrity_verified
        capability_inputs = tuple(
            CapabilityInput(
                str(row["capability_id"]),
                str(row["category"]),
                bool(row["configured"]),
                float(row["weight"]),
                CapabilityLifecycle(str(row["lifecycle_state"] or "UNKNOWN")),
                any(str(requirement["capability_id"]) == str(row["capability_id"]) for requirement in requirements),
            )
            for row in capabilities
        )
        baseline_summary = json.loads(str(baseline["summary_json"])) if baseline else {}
        heavy_hitter_ratio = None
        if frequency_failure is not None:
            try:
                frequency = FrequencySketch.deserialize(bytes(frequency_failure["blob"]))
                hitters = frequency.frequent_items(no_false_negatives=True)
                if hitters and frequency.item_updates:
                    heavy_hitter_ratio = min(
                        1.0, max(item.estimate for item in hitters) / frequency.item_updates
                    )
            except (ValueError, RuntimeError):
                heavy_hitter_ratio = None
        failures = FailureInput(
            estimate=float(failure_bucket["estimate"]) if failure_bucket and failure_bucket["estimate"] is not None else None,
            exact=(str(failure_bucket["mode"]).upper() == "EXACT") if failure_bucket else None,
            lower_bound=float(failure_bucket["lower_bound"]) if failure_bucket and failure_bucket["lower_bound"] is not None else None,
            upper_bound=float(failure_bucket["upper_bound"]) if failure_bucket and failure_bucket["upper_bound"] is not None else None,
            baseline_state=str(baseline["state"]) if baseline else "learning",
            baseline_median=baseline_summary.get("median"),
            baseline_mad=baseline_summary.get("mad"),
            heavy_hitter_ratio=heavy_hitter_ratio,
            severity=(
                1.0
                if degradation and str(degradation["severity"]) == "critical"
                else 0.7
                if degradation and str(degradation["severity"]) == "cognitive"
                else None
            ),
            novelty=self._metric_value(operational_rows, "failure_novelty", "host"),
            persistence=self._metric_value(operational_rows, "failure_persistence", "host"),
            unresolved_episodes=1 if degradation else 0,
            current_task_relevance=1.0 if degradation and task else 0.0 if degradation else None,
        )
        context = context_usage or ContextInput(
            int(self._metric_value(operational_rows, "conversation_tokens", "hermes") or 0),
            int(self._metric_value(operational_rows, "system_prompt_tokens", "hermes") or 0),
            int(self._metric_value(operational_rows, "tool_schema_tokens", "hermes") or 0),
            int(self._metric_value(operational_rows, "retrieved_memory_tokens", "hal") or 0),
            int(self._metric_value(operational_rows, "context_max_tokens", "hermes") or 0),
            int(self._metric_value(operational_rows, "context_truncations", "hermes") or 0),
            self._quantile_p95(quantile_rows, "context_utilization", "hermes"),
        )
        current_resources = {
            name: value
            for name in (
                "cpu_utilization",
                "memory_utilization",
                "swap_utilization",
                "disk_utilization",
                "io_wait",
            )
            if (value := self._metric_value(operational_rows, name, "host")) is not None
        }
        percentile_resources = {
            name: rank
            for name, value in current_resources.items()
            if (
                rank := self._quantile_rank(quantile_rows, name, "host", value)
            )
            is not None
        }
        snapshot = InteroceptionCalculator(
            formula_version=self.settings.interoception.formula_version
        ).calculate(
            InteroceptionInputs(
                active_model_class=active_model_class,
                capabilities=capability_inputs,
                continuity=ContinuityInput(
                    chain_valid,
                    bool(boot and (boot["checkpoint_sequence"] is not None or not boot["recovery_state"].startswith("recovered"))),
                    bool(lease and str(lease["expires_at"]) > now),
                    0 if chain_valid else 1,
                    database_valid,
                    bool(boot and str(boot["recovery_state"]) == "recovered_with_uncertainty"),
                ),
                context=context,
                epistemic=EpistemicInput(
                    open_contradictions,
                    stale_facts,
                    0,
                    missing_evidence,
                    uncertain_actions,
                    0,
                ),
                failures=failures,
                resources=ResourceInput(
                    current_resources,
                    percentile_resources,
                    int(self._metric_value(operational_rows, "queue_depth", "sentience") or 0),
                    self._metric_value(operational_rows, "audio_underrun_rate", "audio"),
                ),
                observed_at=now,
            )
        )

        missing = [
            str(row["capability_id"])
            for row in requirements
            if bool(row["required"]) and str(row["lifecycle_state"] or "UNKNOWN") != str(row["minimum_state"])
        ]
        active_profile = next(
            (str(row["active_profile"]) for row in capabilities if row["active_profile"]),
            "hal-full",
        )
        evidence_handles = [f"event:{identity['evidence_event_id']}"]
        evidence_handles.extend(
            f"event:{row['evidence_event_id']}"
            for row in capabilities
            if row["evidence_event_id"]
        )
        evidence_handles = list(dict.fromkeys(evidence_handles))[:16]

        embodiment_names = {
            "microphone": "hearing",
            "speech": "speech",
            "display": "display",
            "terminal": "terminal",
            "filesystem_read": "filesystem",
            "network": "network",
            "browser": "browser",
        }
        embodiment = {
            embodiment_names[str(row["capability_id"])]: str(row["lifecycle_state"] or "UNKNOWN").lower()
            for row in capabilities
            if str(row["capability_id"]) in embodiment_names
        }
        dimensions = {
            "cognitive_capacity": snapshot.cognitive_capacity,
            "continuity_integrity": snapshot.continuity_integrity,
            "sensory_coverage": snapshot.sensory_coverage,
            "agency_reach": snapshot.agency_reach,
            "context_pressure": snapshot.context_pressure,
            "epistemic_uncertainty": snapshot.epistemic_uncertainty,
            "anomaly_pressure": snapshot.anomaly_pressure,
            "failure_diversity": snapshot.failure_diversity,
            "resource_pressure": snapshot.resource_pressure,
        }
        continuity_data: dict[str, Any] = {
            "state": "verified" if chain_valid and database_valid else "integrity_degraded",
        }
        if boot and boot["shutdown_clean"] is not None:
            continuity_data["last_shutdown_clean"] = bool(boot["shutdown_clean"])
        if task and str(task["state"]) == "interrupted":
            continuity_data["interrupted_task"] = str(task["task_id"])
        data: dict[str, Any] = {
            "identity": {
                "name": str(identity["canonical_name"]),
                "role": str(identity["role"]),
                "instance_id": str(identity["instance_id"]),
                "lineage_verified": bool(identity["lineage_verified"]),
                "incarnation_id": str(identity["incarnation_id"]),
            },
            "continuity": continuity_data,
            "cognition": {
                "nominal_profile": "hal-full",
                "active_profile": active_profile,
                "active_model_class": active_model_class or "unknown",
                "required_capabilities_satisfied": not missing,
            },
            "embodiment": embodiment,
            "attention": {
                "task_id": selected_task_id,
                "state": str(task["state"]) if task else "idle",
                "missing_requirements": missing,
            },
            "obligations": {
                "commitments_open": open_commitments,
                "contradictions_open": open_contradictions,
            },
            "degradation": (
                {
                    "active": True,
                    "episode_id": str(degradation["episode_id"]),
                    "state": str(degradation["state"]).lower(),
                    "severity": str(degradation["severity"]),
                    "lost_capabilities": json.loads(str(degradation["lost_capabilities_json"])),
                }
                if degradation
                else {"active": False}
            ),
            "interoception": {name: _dimension(value) for name, value in dimensions.items()},
        }
        if self.settings.interoception.emit_language_on_threshold_crossing:
            cues = self.language_gate.update(snapshot)
            if cues:
                data["interoception_language"] = {
                    "statements": list(cues),
                    "authority": False,
                    "formula_version": snapshot.formula_version,
                }
        data["evidence_handles"] = evidence_handles

        # Reserve a bounded slice for task-relevant memory by compacting
        # optional exact-plane diagnostics first. Identity, task, capability,
        # obligation, and degradation authority fields are never candidates.
        memory_reserve = min(160, requested // 4) if query.strip() and requested >= 256 else 0
        exact_budget = max(64, requested - memory_reserve)
        truncated = self._fit(
            data, exact_budget, required_capabilities=set(missing)
        )
        provisional = _json(data)
        remaining = requested - estimate_tokens(provisional) - 16
        if query.strip() and remaining >= 32:
            result = self.retriever.search(
                MemoryQuery(
                    query=query,
                    task_id=selected_task_id,
                    token_budget=remaining,
                    max_results=4,
                    max_depth=1,
                )
            )
            memory_handles: list[str] = []
            data["relevant_memory"] = []
            for item in result.all_items:
                data["relevant_memory"].append(
                    {
                        "reference": item.reference,
                        "kind": item.kind,
                        "statement": item.text,
                        "exact": item.exact,
                        "confidence": item.confidence,
                        "provenance": list(item.provenance),
                    }
                )
                memory_handles.append(item.reference)
                evidence_handles.extend(item.evidence_refs)
            # Retrieved memory IDs and their downward evidence are the most
            # useful expansion surface. Capability event handles follow them.
            data["evidence_handles"] = list(
                dict.fromkeys(memory_handles + list(evidence_handles))
            )[:16]

        if not data.get("relevant_memory"):
            data.pop("relevant_memory", None)
        truncated = (
            self._fit(data, requested, required_capabilities=set(missing)) or truncated
        )
        rendered = _json(data)
        tokens = estimate_tokens(rendered)
        if tokens > requested:
            raise ValueError("token budget is too small for the exact core self capsule")
        handles = tuple(str(item) for item in data.get("evidence_handles", ()))
        return SelfCapsule(data, rendered, tokens, len(rendered.encode("utf-8")), truncated, handles)

    @staticmethod
    def _metric_value(rows, name: str, scope: str) -> float | None:
        for row in rows:
            if str(row["metric_name"]) == name and str(row["scope"]) == scope:
                return float(row["value"])
        return None

    @staticmethod
    def _quantile(rows, name: str, scope: str) -> QuantileSketch | None:
        for row in rows:
            if str(row["metric_name"]) == name and str(row["scope"]) == scope:
                try:
                    return QuantileSketch.deserialize(bytes(row["blob"]))
                except (ValueError, RuntimeError):
                    return None
        return None

    @classmethod
    def _quantile_p95(cls, rows, name: str, scope: str) -> float | None:
        sketch = cls._quantile(rows, name, scope)
        summary = sketch.summary() if sketch else None
        return summary.p95 if summary and summary.known else None

    @classmethod
    def _quantile_rank(cls, rows, name: str, scope: str, value: float) -> float | None:
        sketch = cls._quantile(rows, name, scope)
        return sketch.rank(value) if sketch else None

    @staticmethod
    def _fit(data: dict[str, Any], budget: int, *, required_capabilities: set[str]) -> bool:
        truncated = False

        def oversized() -> bool:
            return estimate_tokens(_json(data)) > budget

        if oversized() and data.pop("interoception_language", None) is not None:
            truncated = True
        while data.get("relevant_memory") and oversized():
            data["relevant_memory"].pop()
            truncated = True
        if not data.get("relevant_memory"):
            data.pop("relevant_memory", None)
        while len(data["evidence_handles"]) > 1 and oversized():
            data["evidence_handles"].pop()
            truncated = True
        if oversized():
            data["embodiment"] = {
                key: value
                for key, value in data["embodiment"].items()
                if value != "unknown" or key in required_capabilities
            }
            truncated = True
        if oversized():
            interoception = data.get("interoception", {})
            # `_fit` is intentionally repeatable: the compiler first reserves
            # memory space, then performs a final whole-capsule fit. Do not try
            # to compact an already wrapped/compacted representation as if it
            # still contained every original dimension.
            if "values" not in interoception:
                preferred = (
                    "cognitive_capacity",
                    "continuity_integrity",
                    "context_pressure",
                    "anomaly_pressure",
                    "failure_diversity",
                )
                data["interoception"] = {
                    name: _dimension(
                        _from_dimension_dict(interoception[name]), compact=True
                    )
                    for name in preferred
                    if name in interoception
                    and isinstance(interoception[name], dict)
                }
            truncated = True
        if oversized():
            # Unknown dimensions contain no claim and are therefore omitted instead
            # of manufacturing a zero merely to fill the capsule.
            interoception = data.get("interoception", {})
            values = (
                interoception.get("values", {})
                if isinstance(interoception, dict)
                else {}
            )
            target = values if isinstance(values, dict) and "values" in interoception else interoception
            filtered = {
                name: value
                for name, value in target.items()
                if isinstance(value, dict) and value.get("value") is not None
            }
            if isinstance(interoception, dict) and "values" in interoception:
                data["interoception"] = {
                    "fresh_at": interoception.get("fresh_at", ""),
                    "values": filtered,
                }
            else:
                data["interoception"] = filtered
            truncated = True
        interoception = data.get("interoception", {})
        target = (
            interoception.get("values", {})
            if isinstance(interoception, dict) and "values" in interoception
            else interoception
        )
        for optional in ("failure_diversity", "anomaly_pressure", "context_pressure"):
            if oversized() and optional in target:
                target.pop(optional)
                truncated = True
        if oversized():
            # The exact core still wins. A compact shared freshness field keeps the
            # surviving values attributable without spending a timestamp per value.
            interoception = data.get("interoception", {})
            if "values" not in interoception:
                values = interoception
                fresh_at = next(
                    (
                        str(value.get("fresh_at"))
                        for value in values.values()
                        if isinstance(value, dict) and value.get("fresh_at")
                    ),
                    "",
                )
                for value in values.values():
                    if isinstance(value, dict):
                        value.pop("fresh_at", None)
                data["interoception"] = {"fresh_at": fresh_at, "values": values}
            truncated = True
        return truncated


def _from_dimension_dict(value: dict[str, Any]) -> MachineDimension:
    """Retain typed budget semantics while compacting an already computed value."""
    return MachineDimension(
        value.get("value"),
        float(value.get("confidence", 0.0)),
        str(value.get("fresh_at", "")),
        tuple(str(item) for item in value.get("sources", ())),
        bool(value.get("approximate", False)),
        value.get("baseline"),
        value.get("lower"),
        value.get("upper"),
    )
