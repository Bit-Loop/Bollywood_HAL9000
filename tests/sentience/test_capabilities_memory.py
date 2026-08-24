from __future__ import annotations

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.capabilities.registry import CapabilityRegistry
from hal9000.sentience.capabilities.task_impact import ImpactLevel
from hal9000.sentience.identity.continuity import ContinuityService
from hal9000.sentience.identity.service import IdentityService
from hal9000.sentience.memory.commitments import CommitmentStore
from hal9000.sentience.memory.contradictions import ContradictionStore
from hal9000.sentience.memory.facts import FactStore
from hal9000.sentience.models import CapabilityLifecycle, EventOrigin
from hal9000.sentience.storage.database import SentienceDatabase


def stack_for(tmp_path):
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    database = SentienceDatabase.open(paths, AppConfig().sentience)
    identity = IdentityService(database).load_or_create()
    continuity = ContinuityService(database, identity.incarnation_id)
    boot = continuity.start_boot()
    return database, boot.boot_id


def test_exact_capability_graph_and_task_impact_are_never_sketch_derived(tmp_path) -> None:
    database, boot_id = stack_for(tmp_path)
    registry = CapabilityRegistry(database, boot_id)
    try:
        registry.install_defaults()
        registry.transition(
            "terminal",
            CapabilityLifecycle.READY,
            reason="local probe passed",
            evidence={"probe": "executable", "verified": True},
        )
        task = registry.create_task(
            "Modify system configuration",
            risk_level="consequential",
            requirements={"terminal": (CapabilityLifecycle.READY, True)},
        )
        transition = registry.transition(
            "terminal",
            CapabilityLifecycle.UNAVAILABLE,
            reason="Hermes tool inventory lost terminal",
            evidence={"session_info": "event-12"},
            task_id=task,
        )

        assert transition.task_impact is ImpactLevel.CRITICAL
        assert transition.material is True
        assert registry.unsatisfied_requirements(task) == ("terminal",)
        current = registry.current("terminal")
        assert current.state is CapabilityLifecycle.UNAVAILABLE
        assert current.evidence_event_id
        assert database.count("capability_transitions") == 2
    finally:
        database.close()


def test_facts_corrections_contradictions_and_commitments_keep_evidence(tmp_path) -> None:
    database, boot_id = stack_for(tmp_path)
    facts = FactStore(database, boot_id)
    contradictions = ContradictionStore(database, boot_id)
    commitments = CommitmentStore(database, boot_id)
    try:
        fact = facts.create(
            subject="docker.service",
            statement="Docker authentication failed with HTTP 401.",
            source_type=EventOrigin.OBSERVATION,
            exact=True,
            confidence=1.0,
            evidence_refs=("event:42",),
        )
        correction = contradictions.record_user_correction(
            subject="docker.service",
            previous_statement=fact.statement,
            corrected_statement="The 401 came from the registry, not Docker itself.",
            evidence_refs=("user:turn-9", "event:42"),
        )
        commitment = commitments.create(
            "Push the completed machine-self implementation.",
            trigger={"type": "task.complete", "task_id": "machine-self"},
            evidence_event_id="user:turn-current",
        )

        assert correction.user_correction is True
        assert correction.state == "open"
        assert commitments.open_count() == 1
        commitments.resolve(commitment.commitment_id, evidence_event_id="event:push-success")
        assert commitments.open_count() == 0
        with database.read_connection() as connection:
            evidence = connection.execute(
                "SELECT evidence_ref FROM fact_evidence WHERE fact_id=? ORDER BY evidence_ref",
                (fact.fact_id,),
            ).fetchall()
        assert [row[0] for row in evidence] == ["event:42"]
    finally:
        database.close()
