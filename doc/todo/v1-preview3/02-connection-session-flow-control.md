# Python Preview3 Connection, Session, And Flow Control

## Scope

1. `02` owns the Python host-visible semantics and public shape of preview3 connection/session flow control.
2. `02` does not own the Rust ABI, handle layout, callback/polling primitive definition, or Python runtime integration glue; those belong to `04` and `nnrp-rs`.
3. `02` depends on `nnrp-rs` shard `02` for frozen state-machine and enum semantics, and on `nnrp-rs` shard `04` only for already-frozen binding primitives.

## Sub-Shards

1. `02a-connection-session-lifecycle.md`: bootstrap, session-open/close, and multi-session Python host shape.
2. `02b-scheduling-credits-and-diagnostics.md`: priority, lifecycle state, credit surfaces, and downgrade diagnostics.
3. `02c-control-events-and-recovery.md`: async event/result pumps, `FLOW_UPDATE`/`RESULT_HINT`, and recovery helpers.

## Dependency Gates

1. `02a` may start once `nnrp-rs/02` has frozen connection/session metadata and state-machine concepts; it must not wait on asyncio integration details.
2. `02b` may start once `nnrp-rs/02` has frozen priority/lifecycle/credit enums; it must not redesign scheduling semantics in Python.
3. `02c` may start once `nnrp-rs/02` has frozen control-event semantics and `nnrp-rs/04` has exposed stable delivery primitives; it does not own polling/callback plumbing.
4. `04b` consumes `02a/02b/02c`; `02` defines Python host semantics first, while `04` wires them onto the Rust-backed implementation surface.