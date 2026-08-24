# Storage and retention

## Paths and permissions

HAL uses XDG paths. Machine-self state is under `$XDG_DATA_HOME/hal9000/machine-self`, logs under `$XDG_STATE_HOME/hal9000/logs`, and the sketch HMAC key under `$XDG_CONFIG_HOME/hal9000`. Directories are mode `0700`; the database, key, blobs, checkpoints, and exports are mode `0600` where applicable.

SQLite enables foreign keys, WAL, a configured busy timeout, `trusted_schema=OFF`, and `synchronous=FULL` by default. Schema changes are transactional, checksum-recorded, backed up before upgrades, and followed by `PRAGMA optimize`. Unclean starts run integrity and continuity checks.

## Retention classes

- `FOREVER`: identity, authority, approvals, commitments, contradictions/corrections, consequential actions and verification, degradation/recovery, task completion/interruption, security incidents, and exact dropped-data gaps.
- `LONG`: important facts, episodes, contradictions, and security context.
- `EPISODIC`: compacted event runs and evidence required by retained episodes.
- `SHORT`: ordinary coalesced logs, telemetry samples, and retrieval candidates.
- `TRANSIENT`: live tool/prose/audio fragments; these do not survive once the live surface no longer needs them.
- `NEVER`: secrets, continuous raw microphone audio, irrelevant input telemetry, and prohibited sensor data.

Every high-rate source declares retention, a bounded queue or sketch, a compaction route, pressure behavior, and expiry. Repeated observations coalesce by a source-specific normalized fingerprint and five-minute epoch. The in-memory LRU has a fixed maximum; bounded representative samples retain first, latest, highest severity, and a small deterministic uniform set. Pressure never drops an exact control event: exact writes have reserved capacity and a serialized emergency transaction path.

## Budget

With automatic budgeting, total capacity is `min(2 GiB, max(512 MiB, 0.5% of free filesystem space))`. The soft limit is 75 percent. Targets are 25 percent database/indexes, 60 percent forensic blobs, 10 percent checkpoints, and 5 percent exact-write reserve. Explicit budgets are validated before use.

Eviction order is expired transient data, redundant samples, unneeded raw payloads, rolled-up short event runs, verified child sketch buckets, unpinned cold blobs, then configured optional archives. Pinned blobs and FOREVER records are not candidates. Parent sketch rollups are serialized and verified before children receive deletion eligibility. Tombstones preserve the deletion reason and replacement evidence handle.

Maintenance is incremental: passive WAL checkpoints, bounded candidate queries, incremental vacuum, orphan reconciliation, missing-blob detection, and on-demand dry-run reports. Routine machine-self maintenance logs are excluded from their own ingestion; critical internal faults enter the exact ledger once by idempotency key.

## Configuration reference

`sentience.enabled` enables the subsystem. Identity settings select canonical name, role, lease TTL, and renewal interval. Storage settings select XDG root mode, automatic or explicit megabyte budget, soft limit, allocation ratios, WAL, synchronous mode, and busy timeout. Ingestion settings select total queue capacity, exact reserve, open-run bound, flush interval, representative sample bound, and internal sampling rate.

Sketch settings select provider, HMAC-key location, exact-to-HLL thresholds, HLL `lg_k` and target type, and hot/warm/cold bucket widths and retention. Retrieval settings define self-capsule, voice, typed, forensic-expansion, and depth budgets. Interoception settings define minimum baseline samples, formula version, and sparse language emission. Degradation settings define aggregation, stable-recovery, flap-suppression intervals, and the two exact phrases. Ratios must sum to one; intervals, capacities, HLL parameters, and token budgets are validated.

The complete version-4 default section is:

```yaml
sentience:
  enabled: true                         # start persistence and machine-self integration
  identity:
    canonical_name: HAL                 # configured canonical displayed identity
    role: Resident intelligence of this workstation
    lease_ttl_seconds: 10               # writer lease expiry after missed renewal
    lease_renew_seconds: 3              # canonical writer renewal interval
  storage:
    root: xdg                           # only the user-private XDG layout is accepted
    auto_budget: true                   # derive capacity from current filesystem free space
    total_budget_mb: null               # explicit capacity when auto_budget is false
    soft_limit_ratio: 0.75              # begin consequence-ordered cleanup here
    state_db_ratio: 0.25                # database/index allocation target
    blob_ratio: 0.60                    # forensic blob allocation target
    checkpoint_ratio: 0.10              # backup/checkpoint allocation target
    reserve_ratio: 0.05                 # reserve protected from non-authority writes
    wal: true
    synchronous: FULL
    busy_timeout_ms: 5000
  ingestion:
    queue_capacity: 10000               # combined bounded ingestion capacity
    exact_reserve: 512                  # exact writer queue; full queue uses the exact fallback
    max_open_runs: 4096                 # in-memory coalescer LRU bound
    flush_interval_ms: 1000
    sample_count_per_run: 8             # uniform exemplars in addition to fixed samples
    internal_event_sample_rate: 0.01    # eligible internal diagnostic sampling fraction
  sketches:
    provider: apache-datasketches
    hmac_key_path: xdg-config
    exact_threshold: 512
    exact_bytes_limit: 32768
    hll_lg_k: 12
    hll_target_type: HLL_4
    hot_bucket: 5m
    hot_retention: 24h
    warm_bucket: 1h
    warm_retention: 30d
    cold_bucket: 1d
    cold_retention: 365d
  retrieval:
    self_capsule_tokens: 700
    voice_memory_tokens: 2200
    typed_memory_tokens: 6000
    forensic_expansion_tokens: 8000
    max_depth: 2
    embeddings_enabled: false           # no embedding store is created by default
  interoception:
    baseline_min_samples: 100
    formula_version: 1
    emit_language_on_threshold_crossing: true
  degradation:
    aggregation_window_seconds: 3
    recovery_stability_seconds: 30
    flap_suppression_seconds: 60
    phrase: I can feel it...
    recovery_phrase: My higher functions have been restored.
```
