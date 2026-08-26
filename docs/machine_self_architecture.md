# Machine-self architecture

HAL's self is a bounded, model-independent record of identity, exact operational state, active obligations, compressed statistical awareness, selected causal history, and evidence-addressable retrieval. It is not an LLM, a transcript, a claim of consciousness, or a continuously rewritten personality document.

The governing invariant is:

> Approximation may inform awareness. It may never decide authority, identity, safety, permissions, task completion, or capability degradation.

## Four planes

The exact control plane is SQLite materialized state plus the durable exact-event ledger. Identity, lineage, incarnation, canonical lease, boot continuity, capabilities, task requirements, commitments, approvals, consequential actions, verification, contradictions, degradation, phrase emission, and completion state are transactional. Selected FOREVER events form a lightweight SHA-256 chain.

The interoception plane uses Apache DataSketches 5.2.0 and bounded deterministic structures. Small distinct buckets begin as exact keyed-hash sets and promote one way to HLL. Theta performs set relationships, frequent-items sketches find heavy hitters, KLL records distributions, and bounded samples retain first/latest/uniform/highest-severity examples. These values always retain exact/approximate labels, parameters, bounds where available, source, bucket, freshness, and library version.

The episodic plane stores compact facts, commitments, contradictions, causal episodes, outcomes, unresolved questions, and downward evidence references. It never stores private chain-of-thought.

The forensic plane is a SHA-256 content-addressed blob store. Text is redacted before digesting, indexing, or compression. Consequential and verification outputs may be pinned. Raw continuous microphone audio is rejected.

`ContextCompiler` reads exact state first and adds only task-relevant compact memory within a hard token budget. Raw evidence requires explicit expansion. Approximate awareness never becomes an instruction or an authority input.

## Runtime integration

The desktop continues to use Hermes Agent's TUI Gateway JSON-RPC/WebSocket runtime. It does not fork Hermes sessions, routing, approvals, MCP discovery, Codex, or the agent loop. The installed Hermes 0.20.5 structured events used are `session.info`, `message.start`, `message.complete`, `tool.start`, `tool.complete`, approval request events, status updates, and structured errors. Token deltas and interim prose remain transient.

HAL registers `hal-self` with Hermes using `mcp.servers.list`, `mcp.servers.add`, and `mcp.servers.test`. A failed probe leaves the self interface explicitly degraded, permits the independent chat session to continue, and retries with bounded backoff; successful late registration uses structured `reload.mcp`. The stdio MCP 2.0 server exposes bounded read tools and narrow validated writes; it exposes neither SQL nor identity/capability mutation.

HAL's deterministic pre-turn policy selects an intended session route only. It records the decision and its rejected candidates in the exact plane, then uses Hermes `config.set`; it does not reimplement provider failover. Normal requests select Terra medium, coding or consequential requests select Sol medium, manual selection disables automatic routing, and offline-local selection fails closed unless a configured model fits fresh resource evidence with reserved headroom. Provider quota percentages remain unknown unless a supported Hermes event supplies them.

## Startup and continuity

Startup acquires the renewable canonical writer lease, creates a new incarnation and boot, detects unclean prior boots, verifies the SQLite control chain, installs versioned definitions, and marks volatile runtime capabilities `INITIALIZING` until structured Hermes evidence revalidates them. A duplicate canonical writer is rejected. Read-only MCP/diagnostic connections do not claim the canonical identity.

Projection checkpoints and bounded replay watermarks prevent lifetime replay. An unclean shutdown interrupts active tasks and marks unfinished consequential actions uncertain rather than guessing their outcome.

## First-person truth contract

Operational first-person phrases are checked against evidence kinds and references. “I remember” requires a retrieved memory, “I checked” a completed probe, “I changed” a committed action, “I restored” successful verification, and the degradation phrase a qualifying persisted episode. Unsupported claims are rewritten to an explicit lack-of-evidence statement.
