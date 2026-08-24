from __future__ import annotations

import asyncio

import pytest

from hal9000.config import AppConfig, ConfigStore
from hal9000.paths import AppPaths
from hal9000.sentience.hermes.self_mcp_server import SelfMcpApi, build_server
from hal9000.sentience.models import RetentionClass, Sensitivity
from hal9000.sentience.service import MachineSelfService


def test_real_mcp_server_exposes_only_bounded_narrow_tools(tmp_path) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig()
    ConfigStore(paths).save(config)
    machine = MachineSelfService(paths, config, tmp_path)
    machine.start()
    api = SelfMcpApi(paths)
    try:
        server = build_server(api)
        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        assert names == {
            "hal.self_status",
            "hal.memory_search",
            "hal.memory_expand",
            "hal.claim_evidence",
            "hal.commitments",
            "hal.degradation_status",
            "hal.storage_status",
            "hal.commitment_create",
            "hal.fact_propose",
            "hal.fact_correct",
            "hal.user_pin_evidence",
        }
        assert "sql" not in " ".join(names).lower()
        status = api.self_status(token_budget=300)
        assert status["budget"]["used_tokens"] <= 300
        assert status["capsule"]["identity"]["name"] == "HAL"
        storage = api.storage_status()
        assert storage["total_bytes"] <= storage["budget_bytes"]

        prepared = machine.prepare_prompt("Pin this evidence", session_id="pin")
        with machine.database.read_connection() as connection:
            user_event = connection.execute(
                "SELECT event_id FROM exact_events WHERE task_id=? "
                "AND origin='user_assertion' ORDER BY sequence DESC LIMIT 1",
                (prepared.task_id,),
            ).fetchone()
            non_user_event = connection.execute(
                "SELECT evidence_event_id FROM identity_state WHERE singleton=1"
            ).fetchone()
        evidence = machine.blobs.put_text(
            "operator-selected forensic evidence",
            mime_type="text/plain",
            sensitivity=Sensitivity.INTERNAL,
            retention_class=RetentionClass.SHORT,
        )
        expanded = api.claim_evidence(evidence.digest, token_budget=80)
        assert expanded["provenance"] == f"payload_refs:{evidence.digest}"
        assert expanded["exact"] is True
        assert expanded["used_tokens"] <= 80
        pinned = api.user_pin_evidence(evidence.digest, str(user_event[0]))
        assert pinned["pinned"] is True
        with api.database.read_connection() as connection:
            row = connection.execute(
                "SELECT pinned,retention_class FROM payload_refs WHERE digest=?",
                (evidence.digest,),
            ).fetchone()
        assert row["pinned"] == 1
        assert row["retention_class"] == "forever"
        with pytest.raises(PermissionError, match="user-assertion"):
            api.user_pin_evidence(evidence.digest, str(non_user_event[0]))
    finally:
        api.close()
        machine.stop()
