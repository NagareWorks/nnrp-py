# Python Preview3 Host API Surface

- [ ] Replace Python-owned preview3 packet/session entry points with Rust-backed orchestration.
- [ ] Add host-facing submit/result/control helpers that compose Rust preview3 handles rather than rebuilding packet logic in Python.
- [ ] Add Python-facing operation identifiers, parent/group relationships, and cancellation scopes on top of Rust lifecycle primitives.
- [ ] Preserve the distinction among `partial`, `degraded`, `stale_reuse`, `cancelled`, `failed`, and `completed` lifecycle states on the Python API surface.