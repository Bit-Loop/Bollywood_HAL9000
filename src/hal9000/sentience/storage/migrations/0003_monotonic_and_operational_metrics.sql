ALTER TABLE degradation_episodes ADD COLUMN timer_boot_id TEXT;
ALTER TABLE degradation_episodes ADD COLUMN started_monotonic_ns INTEGER;
ALTER TABLE degradation_episodes ADD COLUMN recovery_started_monotonic_ns INTEGER;
ALTER TABLE degradation_episodes ADD COLUMN recovered_monotonic_ns INTEGER;

CREATE TABLE IF NOT EXISTS operational_metrics_current (
    metric_name TEXT NOT NULL,
    scope TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    monotonic_ns INTEGER NOT NULL,
    boot_id TEXT NOT NULL,
    exact INTEGER NOT NULL CHECK (exact IN (0, 1)),
    source_event_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(metric_name, scope)
);
CREATE INDEX IF NOT EXISTS operational_metrics_freshness
    ON operational_metrics_current(observed_at, metric_name);
