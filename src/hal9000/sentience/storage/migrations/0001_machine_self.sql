CREATE TABLE IF NOT EXISTS identity_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    canonical_name TEXT NOT NULL,
    instance_id TEXT NOT NULL UNIQUE,
    lineage_id TEXT NOT NULL,
    lineage_verified INTEGER NOT NULL CHECK (lineage_verified IN (0, 1)),
    incarnation_id TEXT NOT NULL,
    integrity_state TEXT NOT NULL DEFAULT 'unverified',
    updated_at TEXT NOT NULL,
    evidence_event_id TEXT
);

CREATE TABLE IF NOT EXISTS instance_leases (
    instance_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('writer', 'observer')),
    acquired_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    process_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS boot_sessions (
    boot_id TEXT PRIMARY KEY,
    incarnation_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    started_monotonic_ns INTEGER NOT NULL,
    ended_at TEXT,
    ended_monotonic_ns INTEGER,
    shutdown_clean INTEGER,
    recovery_state TEXT NOT NULL DEFAULT 'starting',
    checkpoint_sequence INTEGER,
    process_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS exact_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT UNIQUE,
    schema_version INTEGER NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    monotonic_ns INTEGER NOT NULL,
    boot_id TEXT NOT NULL,
    source TEXT NOT NULL,
    type TEXT NOT NULL,
    subject TEXT NOT NULL,
    severity TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    task_id TEXT,
    origin TEXT NOT NULL,
    observed INTEGER NOT NULL CHECK (observed IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    retention_class TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    internal INTEGER NOT NULL DEFAULT 0 CHECK (internal IN (0, 1)),
    previous_hash TEXT,
    event_hash TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS exact_events_type_sequence ON exact_events(type, sequence);
CREATE INDEX IF NOT EXISTS exact_events_task_sequence ON exact_events(task_id, sequence);
CREATE INDEX IF NOT EXISTS exact_events_subject_sequence ON exact_events(subject, sequence);
CREATE INDEX IF NOT EXISTS exact_events_boot_monotonic ON exact_events(boot_id, monotonic_ns);

CREATE TABLE IF NOT EXISTS dead_letters (
    dead_letter_id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    redacted_payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    UNIQUE(source, reason, payload_digest)
);

CREATE TABLE IF NOT EXISTS capability_definitions (
    capability_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL,
    nominal_requirement TEXT NOT NULL CHECK (nominal_requirement IN ('required', 'optional', 'disabled')),
    weight REAL NOT NULL DEFAULT 1.0,
    material_class TEXT NOT NULL DEFAULT 'peripheral',
    configured INTEGER NOT NULL DEFAULT 1 CHECK (configured IN (0, 1)),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS capability_current (
    capability_id TEXT PRIMARY KEY REFERENCES capability_definitions(capability_id) ON DELETE RESTRICT,
    lifecycle_state TEXT NOT NULL,
    health REAL,
    permission_scope TEXT NOT NULL DEFAULT 'none',
    trust_state TEXT NOT NULL DEFAULT 'unverified',
    confidence REAL NOT NULL DEFAULT 1.0,
    observed_at TEXT NOT NULL,
    freshness_deadline TEXT,
    evidence_event_id TEXT,
    replacement_capability TEXT,
    active_profile TEXT,
    current_task_impact TEXT NOT NULL DEFAULT 'none'
);

CREATE TABLE IF NOT EXISTS capability_edges (
    parent_capability TEXT NOT NULL REFERENCES capability_definitions(capability_id) ON DELETE CASCADE,
    required_capability TEXT NOT NULL REFERENCES capability_definitions(capability_id) ON DELETE CASCADE,
    edge_kind TEXT NOT NULL DEFAULT 'requires',
    minimum_state TEXT NOT NULL DEFAULT 'READY',
    PRIMARY KEY(parent_capability, required_capability, edge_kind)
);

CREATE TABLE IF NOT EXISTS capability_transitions (
    transition_id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL REFERENCES capability_definitions(capability_id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    expected INTEGER NOT NULL CHECK (expected IN (0, 1)),
    material INTEGER NOT NULL CHECK (material IN (0, 1)),
    occurred_at TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    task_impact TEXT NOT NULL DEFAULT 'none'
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    interrupted_at TEXT,
    risk_level TEXT NOT NULL DEFAULT 'ordinary',
    current_checkpoint_id TEXT,
    parent_task_id TEXT REFERENCES tasks(task_id),
    exact_completion_event_id TEXT,
    unresolved_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS task_capability_requirements (
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    capability_id TEXT NOT NULL REFERENCES capability_definitions(capability_id) ON DELETE RESTRICT,
    minimum_state TEXT NOT NULL DEFAULT 'READY',
    required INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),
    unsafe_if_lost INTEGER NOT NULL DEFAULT 0 CHECK (unsafe_if_lost IN (0, 1)),
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(task_id, capability_id)
);

CREATE TABLE IF NOT EXISTS task_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    state_json TEXT NOT NULL,
    unresolved_json TEXT NOT NULL DEFAULT '[]',
    pending_actions_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(task_id, sequence)
);

CREATE TABLE IF NOT EXISTS commitments (
    commitment_id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(task_id),
    statement TEXT NOT NULL,
    trigger_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    due_at TEXT,
    resolved_at TEXT,
    evidence_event_id TEXT NOT NULL,
    resolution_event_id TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    hermes_request_id TEXT NOT NULL UNIQUE,
    task_id TEXT REFERENCES tasks(task_id),
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    choice TEXT,
    scope TEXT NOT NULL DEFAULT 'once',
    description TEXT NOT NULL,
    request_event_id TEXT NOT NULL,
    decision_event_id TEXT
);

CREATE TABLE IF NOT EXISTS consequential_actions (
    action_id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(task_id),
    tool_call_id TEXT UNIQUE,
    action_type TEXT NOT NULL,
    target TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    event_id TEXT NOT NULL,
    approval_id TEXT REFERENCES approvals(approval_id),
    result_summary TEXT,
    payload_ref TEXT,
    uncertainty_reason TEXT
);

CREATE TABLE IF NOT EXISTS action_verifications (
    verification_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES consequential_actions(action_id) ON DELETE CASCADE,
    verified_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    statement TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload_ref TEXT
);

CREATE TABLE IF NOT EXISTS semantic_facts (
    fact_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    statement TEXT NOT NULL,
    source_type TEXT NOT NULL,
    exact INTEGER NOT NULL CHECK (exact IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    state TEXT NOT NULL DEFAULT 'active',
    stale_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    consolidation_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS fact_evidence (
    fact_id TEXT NOT NULL REFERENCES semantic_facts(fact_id) ON DELETE CASCADE,
    evidence_ref TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'supports',
    PRIMARY KEY(fact_id, evidence_ref, relation)
);

CREATE TABLE IF NOT EXISTS contradictions (
    contradiction_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    statement_a TEXT NOT NULL,
    statement_b TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    user_correction INTEGER NOT NULL DEFAULT 0 CHECK (user_correction IN (0, 1)),
    confidence REAL NOT NULL DEFAULT 1.0,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    resolution TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    task_id TEXT REFERENCES tasks(task_id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    state TEXT NOT NULL,
    observations_json TEXT NOT NULL DEFAULT '[]',
    inferences_json TEXT NOT NULL DEFAULT '[]',
    actions_json TEXT NOT NULL DEFAULT '[]',
    outcome_json TEXT,
    unresolved_json TEXT NOT NULL DEFAULT '[]',
    event_run_refs_json TEXT NOT NULL DEFAULT '[]',
    exact_event_refs_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    summary_model TEXT,
    summary_prompt_version TEXT,
    compaction_version INTEGER NOT NULL,
    input_watermark_start INTEGER NOT NULL,
    input_watermark_end INTEGER NOT NULL,
    UNIQUE(kind, subject, compaction_version, input_watermark_start, input_watermark_end)
);

CREATE TABLE IF NOT EXISTS episode_evidence (
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    evidence_ref TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'supports',
    PRIMARY KEY(episode_id, evidence_ref, relation)
);

CREATE TABLE IF NOT EXISTS event_runs (
    run_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    source TEXT NOT NULL,
    type TEXT NOT NULL,
    subject TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    count INTEGER NOT NULL CHECK (count > 0),
    severity_max TEXT NOT NULL,
    task_id TEXT,
    coalescing_epoch TEXT NOT NULL,
    first_payload_ref TEXT,
    last_payload_ref TEXT,
    normalized_template TEXT,
    retention_class TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    compacted INTEGER NOT NULL DEFAULT 0 CHECK (compacted IN (0, 1)),
    UNIQUE(source, type, subject, fingerprint, task_id, coalescing_epoch)
);
CREATE INDEX IF NOT EXISTS event_runs_lookup ON event_runs(source, type, subject, last_seen);

CREATE TABLE IF NOT EXISTS event_run_samples (
    sample_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES event_runs(run_id) ON DELETE CASCADE,
    sample_kind TEXT NOT NULL,
    payload_ref TEXT,
    redacted_text TEXT,
    observed_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    UNIQUE(run_id, sample_kind, ordinal)
);

CREATE TABLE IF NOT EXISTS sketch_buckets (
    bucket_id TEXT PRIMARY KEY,
    metric_name TEXT NOT NULL,
    scope TEXT NOT NULL,
    bucket_start TEXT NOT NULL,
    bucket_end TEXT NOT NULL,
    bucket_width_seconds INTEGER NOT NULL,
    mode TEXT NOT NULL,
    sketch_kind TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    key_version INTEGER NOT NULL,
    library TEXT NOT NULL,
    library_version TEXT NOT NULL,
    serialization_version INTEGER NOT NULL,
    item_updates INTEGER NOT NULL,
    estimate REAL,
    lower_bound REAL,
    upper_bound REAL,
    sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
    rollup_state TEXT NOT NULL DEFAULT 'none',
    last_updated_at TEXT NOT NULL,
    checksum TEXT NOT NULL,
    blob BLOB NOT NULL,
    UNIQUE(metric_name, scope, bucket_start, bucket_width_seconds, key_version)
);

CREATE TABLE IF NOT EXISTS sketch_rollups (
    parent_bucket_id TEXT NOT NULL REFERENCES sketch_buckets(bucket_id) ON DELETE RESTRICT,
    child_bucket_id TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    verification_json TEXT NOT NULL,
    PRIMARY KEY(parent_bucket_id, child_bucket_id)
);

CREATE TABLE IF NOT EXISTS payload_refs (
    digest TEXT PRIMARY KEY,
    compressed_size INTEGER NOT NULL,
    uncompressed_size INTEGER NOT NULL,
    compression TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    retention_class TEXT NOT NULL,
    refcount INTEGER NOT NULL DEFAULT 0 CHECK (refcount >= 0),
    created_at TEXT NOT NULL,
    last_accessed_at TEXT NOT NULL,
    integrity_checksum TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    relative_path TEXT NOT NULL UNIQUE,
    missing INTEGER NOT NULL DEFAULT 0 CHECK (missing IN (0, 1))
);

CREATE TABLE IF NOT EXISTS payload_links (
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    digest TEXT NOT NULL REFERENCES payload_refs(digest) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_type, owner_id, relation, digest)
);

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    projection_name TEXT NOT NULL,
    projection_version INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    state_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    clean_shutdown INTEGER NOT NULL DEFAULT 0 CHECK (clean_shutdown IN (0, 1)),
    UNIQUE(projection_name, projection_version, sequence)
);

CREATE TABLE IF NOT EXISTS degradation_episodes (
    episode_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    aggregation_closed_at TEXT,
    nominal_profile TEXT NOT NULL,
    active_profile TEXT NOT NULL,
    severity TEXT NOT NULL,
    lost_capabilities_json TEXT NOT NULL,
    affected_tasks_json TEXT NOT NULL,
    fallback_model TEXT,
    cause_event_ids_json TEXT NOT NULL,
    phrase_outbox_id TEXT,
    phrase_emitted INTEGER NOT NULL DEFAULT 0 CHECK (phrase_emitted IN (0, 1)),
    recovery_started_at TEXT,
    recovered_at TEXT,
    recovery_phrase_outbox_id TEXT,
    recovery_phrase_emitted INTEGER NOT NULL DEFAULT 0 CHECK (recovery_phrase_emitted IN (0, 1)),
    conclusions_requiring_revalidation_json TEXT NOT NULL DEFAULT '[]',
    last_transition_event_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revalidation_items (
    revalidation_id TEXT PRIMARY KEY,
    degradation_episode_id TEXT NOT NULL REFERENCES degradation_episodes(episode_id),
    claim_reference TEXT NOT NULL,
    reason TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    result_event_id TEXT,
    UNIQUE(degradation_episode_id, claim_reference)
);

CREATE TABLE IF NOT EXISTS outbox (
    outbox_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    claimed_at TEXT,
    claim_owner TEXT,
    emitted_at TEXT,
    delivery_channel TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(emitted_at, available_at, created_at);

CREATE TABLE IF NOT EXISTS retrieval_documents (
    document_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL UNIQUE,
    source_table TEXT NOT NULL,
    source_id TEXT NOT NULL,
    document_kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    exact INTEGER NOT NULL DEFAULT 1 CHECK (exact IN (0, 1)),
    stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    contradicted INTEGER NOT NULL DEFAULT 0 CHECK (contradicted IN (0, 1)),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_table, source_id)
);
CREATE INDEX IF NOT EXISTS retrieval_documents_filter ON retrieval_documents(document_kind, subject, task_id, updated_at);

-- BEGIN OPTIONAL_FTS5
CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(
    title,
    body,
    subject,
    content='retrieval_documents',
    content_rowid='document_rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS retrieval_documents_ai AFTER INSERT ON retrieval_documents BEGIN
    INSERT INTO retrieval_fts(rowid, title, body, subject)
    VALUES (new.document_rowid, new.title, new.body, new.subject);
END;
CREATE TRIGGER IF NOT EXISTS retrieval_documents_ad AFTER DELETE ON retrieval_documents BEGIN
    INSERT INTO retrieval_fts(retrieval_fts, rowid, title, body, subject)
    VALUES ('delete', old.document_rowid, old.title, old.body, old.subject);
END;
CREATE TRIGGER IF NOT EXISTS retrieval_documents_au AFTER UPDATE ON retrieval_documents BEGIN
    INSERT INTO retrieval_fts(retrieval_fts, rowid, title, body, subject)
    VALUES ('delete', old.document_rowid, old.title, old.body, old.subject);
    INSERT INTO retrieval_fts(rowid, title, body, subject)
    VALUES (new.document_rowid, new.title, new.body, new.subject);
END;
-- END OPTIONAL_FTS5

CREATE TABLE IF NOT EXISTS retrieval_audits (
    audit_id TEXT PRIMARY KEY,
    query_category TEXT NOT NULL,
    result_ids_json TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    token_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    sampled INTEGER NOT NULL DEFAULT 1 CHECK (sampled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS baseline_versions (
    baseline_id TEXT PRIMARY KEY,
    metric_name TEXT NOT NULL,
    scope TEXT NOT NULL,
    version INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    sample_count INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    excluded_incident_ids_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(metric_name, scope, version)
);

CREATE TABLE IF NOT EXISTS retention_tombstones (
    tombstone_id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    retention_class TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    replacement_reference TEXT,
    bytes_reclaimed INTEGER NOT NULL DEFAULT 0,
    UNIQUE(object_type, object_id)
);

CREATE TABLE IF NOT EXISTS compaction_jobs (
    job_id TEXT PRIMARY KEY,
    job_kind TEXT NOT NULL,
    algorithm_version INTEGER NOT NULL,
    input_watermark_start INTEGER NOT NULL,
    input_watermark_end INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    output_refs_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    UNIQUE(job_kind, algorithm_version, input_watermark_start, input_watermark_end)
);
