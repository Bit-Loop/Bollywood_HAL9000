CREATE TABLE IF NOT EXISTS model_provider_health (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    state TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    freshness_deadline TEXT,
    cooldown_until TEXT,
    evidence_event_id TEXT REFERENCES exact_events(event_id),
    detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(provider, model)
);

CREATE TABLE IF NOT EXISTS model_route_decisions (
    decision_id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(task_id),
    session_id TEXT,
    intent_class TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    selected_profile TEXT NOT NULL DEFAULT '',
    selected_provider TEXT NOT NULL,
    selected_model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    user_override INTEGER NOT NULL CHECK (user_override IN (0, 1)),
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    reason TEXT NOT NULL,
    rejected_candidates_json TEXT NOT NULL DEFAULT '[]',
    evidence_event_id TEXT NOT NULL REFERENCES exact_events(event_id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS model_route_decisions_task_time
    ON model_route_decisions(task_id, created_at DESC);
