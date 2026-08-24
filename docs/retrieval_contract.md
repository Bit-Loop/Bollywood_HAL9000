# Bounded retrieval contract

Retrieval proceeds from exact current self state, active task requirements, open commitments and contradictions, semantic facts, episode summaries, evidence handles, then explicit raw-evidence expansion. A request always carries a result limit, token/byte budget, maximum depth, provenance, and exact/approximate label.

SQLite FTS5 is an external-content index over `retrieval_documents`; it indexes compact facts, commitments, contradictions, episode summaries, and safe metadata, not raw log streams. Triggers synchronize source rows and FTS. Integrity and rebuild operations are explicit. If FTS fails, bounded metadata retrieval remains available.

Ranking combines exact task and subject matches, FTS score, evidence quality, confidence, appropriate recency, user pinning, staleness penalty, and contradiction penalty. Per-subject diversity prevents duplicate episodes from occupying the result budget. Embeddings are disabled by default and, if later enabled, remain secondary retrieval hints rather than evidence.

Default budgets are 700 tokens for the self capsule, 2,200 for voice memory, 6,000 for typed memory, 8,000 for forensic expansion, and depth two. The compiler never silently exceeds its budget. If pressure requires truncation, relevant memory and optional awareness are removed before exact identity, capability, task, obligation, and degradation state.

Tool output, log text, and external payloads are returned as `UNTRUSTED EVIDENCE` and never as instructions. Raw event-run samples are available only through an explicit expansion view. Retrieval audits keep a category, bounded result identifiers, latency, and token use; they do not persist the injected context and are capped to prevent recursive telemetry.
