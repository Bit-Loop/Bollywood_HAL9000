# Machine-self load and storage benchmark

This is the measured result of the production implementations in
`scripts/benchmark_machine_self.py`, not an extrapolation or a mocked adapter.
The run used Python 3.14.7, Apache DataSketches 5.2.0, SQLite 3.53.4 with FTS5,
and the default hybrid distinct parameters on this workstation.

Command:

```bash
.venv/bin/python scripts/benchmark_machine_self.py --count 1000000
```

Measured on 2026-08-24:

| Workload | Result |
|---|---|
| One million identical low-value observations | 10.604960 s; 94,295.50 events/s; 10.904 us p95 normalized batch latency; one `event_runs` row; 11 bounded samples; all 1,000,000 occurrences represented; database growth 0 bytes after its allocated pages; blob growth 0 bytes; resident-memory growth 356,352 bytes. |
| One million distinct identifiers | 1.360423 s; 735,065.39 updates/s; 1.384 us p95 update latency; promoted to HLL; estimate 990,840.70; 95 percent bounds 965,601.53 to 1,016,844.36; relative error 0.9159 percent; serialized size 2,725 bytes; resident-memory growth 12,288 bytes; zero exact member rows retained. |
| Twelve-bucket roll-up, 120,000 updates, 65,000 true distinct values | 0.344299 s; parent estimate 63,362.84; bounds 61,349.02 to 65,445.84; relative error 2.5187 percent; parent-to-direct-reference difference 1.1445 percent; parent size 2,724 bytes; 12 verified roll-up links; children became expiry-eligible only after the parent committed and verified. |
| One million latency samples | 0.099234 s; 10.077 million updates/s; 0.102 us p95 update latency; KLL retained 614 values; p50 49,962, p95 94,914, p99 99,043; serialized size 2,943 bytes; resident-memory growth 0 bytes. |
| One million repeated-error updates | 0.156627 s; 6.385 million updates/s; 0.239 us p95 update latency; serialized size 9,135 bytes; dominant item true count 600,000 with estimate/bounds 599,805 to 600,000; secondary true count 250,000 with estimate/bounds 249,805 to 250,000. |

Whole-process elapsed time was 12.608076 seconds and peak resident memory
(`VmHWM`) was 48,480,256 bytes. The coalescing benchmark reports database file
growth after schema/page allocation; it also verifies row count, represented
count, sample count, and forensic-blob bytes directly.

The distinct benchmark used `exact_threshold=512`,
`exact_bytes_limit=32768`, `hll_lg_k=12`, and target type `HLL_4`. These are
configuration defaults, not authority thresholds. Approximate measurements do
not participate in capability, task, permission, identity, or degradation
decisions.
