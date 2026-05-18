# NNRP/1-preview3 Python SDK Implementation Todo

## 0. Scope

1. This directory tracks the preview3 Python SDK rollout as a Rust-backed binding and host-facing control/session layer.
2. Preview3 wire semantics, state machines, cache/schema behavior, and conformance baselines are owned by `nnrp-doc` plus `nnrp-rs`, not by this repository.
3. The Rust-backed path replaces the prior helper surface in place for `NNRP/1`; avoid adding parallel Python type families while it is staged.

## 1. Shard Map

1. `01-foundation-and-contract.md`: consume the frozen preview3 contract, `NNRP/1` surface-replacement policy, and Python binding foundation.
2. `02-connection-session-flow-control.md`: ownership and dependency map for the `02a/02b/02c` connection/session shards.
3. `02a-connection-session-lifecycle.md`: connection bootstrap, session-open/close, and multi-session Python host shape.
4. `02b-scheduling-credits-and-diagnostics.md`: priority, lifecycle state, credit surfaces, and downgrade diagnostics.
5. `02c-control-events-and-recovery.md`: `FLOW_UPDATE`/`RESULT_HINT`, async event/result pumps, and recovery helpers.
6. `03-cache-schema-profile-registry.md`: cache lease wrappers, schema/profile registry, token/tensor public-layer implications.
7. `04-implementation-surface.md`: ownership and dependency map for the `04a/04b/04c` implementation-surface shards.
8. `04a-rust-binding-adoption.md`: Rust FFI consumption, handle wrappers, and error mapping.
9. `04b-python-host-api-surface.md`: Python-facing preview3 host APIs built on top of Rust-backed handles.
10. `04c-async-runtime-integration.md`: async delivery, callback/poll integration, and Python runtime-facing integration glue.
11. `05-validation-and-docs.md`: conformance, perf smoke checks, and surface-replacement docs.

## 2. PR Rules

1. One shard per PR by default; keep foundation, FFI integration, and Python host-surface changes reviewable as separate PRs.
2. `main` should accept reviewed PRs only after GitHub publication.
3. If an item needs to change preview3 protocol semantics, update `nnrp-doc` first instead of hard-coding Python-side behavior.
4. Treat `02a/02b/02c` as semantic-host-surface work and `04a/04b/04c` as binding/runtime-integration work; do not merge them into one PR unless the boundary itself changes.

## 3. Protocol Coverage Check

1. FFI handles, callback/polling model, buffer ownership, and error families are tracked in `01` and `04`.
2. `SESSION_OPEN` / `SESSION_OPEN_ACK`, explicit session close, multi-session routing, and recovery-object semantics are tracked in `01` and `02`.
3. Priority classes, operation states, cancel scope, and `FLOW_UPDATE` 32B semantics are tracked in `01` and `02`.
4. Cache lease/version/dependency rules, schema descriptor 32B, typed payload descriptor 24B, and `descriptor_flags` are tracked in `01` and `03`.
5. `tensor` / `token` first-round standard profiles plus `structured_event` / `tool_delta` ownership boundaries are tracked in `01`, `03`, and `04`.
6. Rust conformance-first enum/message/error baselines and Python binding validation are tracked in `01` and `05`.