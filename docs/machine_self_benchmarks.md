# Machine-self load and storage benchmark

This is the measured result of the production implementations in
`scripts/benchmark_machine_self.py`, not an extrapolation or a mocked adapter.
The run used Python 3.14.7, Apache DataSketches 5.2.0, SQLite 3.53.4 with FTS5,
and the default hybrid distinct parameters on this workstation.

Command:

```bash
.venv/bin/python scripts/benchmark_machine_self.py --count 1000000
```

Measured on 2026-08-25 US/Central (2026-08-26 UTC):

| Workload | Result |
|---|---|
| One million identical low-value observations | 10.500887 s; 95,230.05 events/s; 10.775 us p95 normalized batch latency; one `event_runs` row; 11 bounded samples; all 1,000,000 occurrences represented; database growth 0 bytes after its allocated pages; blob growth 0 bytes; resident-memory growth 356,352 bytes. |
| One million distinct identifiers | 1.369279 s; 730,311.20 updates/s; 1.396 us p95 update latency; promoted to HLL; estimate 990,840.67; 95 percent bounds 965,601.50 to 1,016,844.33; relative error 0.9159 percent; serialized size 2,725 bytes; resident-memory growth 20,480 bytes; zero exact member rows retained. |
| Twelve-bucket roll-up, 120,000 updates, 65,000 true distinct values | 0.340644 s; parent estimate 65,455.31; bounds 63,374.98 to 67,607.10; relative error 0.7005 percent; parent-to-direct-reference difference 0.1907 percent; parent size 2,731 bytes; 12 verified roll-up links; children became expiry-eligible only after the parent committed and verified. |
| One million latency samples | 0.098867 s; 10.115 million updates/s; 0.110 us p95 update latency; KLL retained 614 values; p50 49,869, p95 94,815, p99 98,879; serialized size 2,943 bytes; resident-memory growth 0 bytes. |
| One million repeated-error updates | 0.159992 s; 6.250 million updates/s; 0.236 us p95 update latency; serialized size 9,135 bytes; dominant item true count 600,000 with estimate/bounds 599,805 to 600,000; secondary true count 250,000 with estimate/bounds 249,805 to 250,000. |

Whole-process elapsed time was 12.503449 seconds and peak resident memory
(`VmHWM`) was 50,331,648 bytes. The coalescing benchmark reports database file
growth after schema/page allocation; it also verifies row count, represented
count, sample count, and forensic-blob bytes directly.

The distinct benchmark used `exact_threshold=512`,
`exact_bytes_limit=32768`, `hll_lg_k=12`, and target type `HLL_4`. These are
configuration defaults, not authority thresholds. Approximate measurements do
not participate in capability, task, permission, identity, or degradation
decisions.
