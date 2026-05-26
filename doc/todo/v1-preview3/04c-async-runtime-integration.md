# Python Preview3 Async Runtime Integration

- [x] Adapt Rust callback/polling result delivery into Python async-friendly primitives.
- [x] Choose one default Python delivery model for preview3: async iterator, callback registration, or explicit polling.
- [x] Keep backend selection behind an internal interface so tests can run against pure-Python fixtures and native artifacts.
- [ ] Avoid per-frame Python object churn on the hot submit/result path; batch or borrow native buffers where the ABI allows it.
- [x] Define cancellation behavior when a Python task is cancelled while a native operation is active.
- [ ] Expose structured event, tool delta, and workflow-state updates through Python-native async iterators or callbacks backed by Rust result pumps.
- [ ] Keep any remaining pure-Python codec code limited to preview2 fixture inspection or other non-hot-path tooling.
  - [x] Keep native runtime backend selection separate from the pure-Python fallback/test fixtures.
  - [ ] Audit preview3 runtime call sites and move remaining Python-owned codec helpers behind fixture/diagnostic paths.
- [x] Run the pre-migration benchmark suite and record the baseline in `doc/benchmarks/rs-native-artifacts-migration.md`.
- [ ] Run the same benchmark suite after native migration and record the deltas in `doc/benchmarks/rs-native-artifacts-migration.md`.
