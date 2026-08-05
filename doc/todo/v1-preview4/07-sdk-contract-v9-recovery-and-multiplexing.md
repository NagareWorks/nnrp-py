# 07 - SDK Contract V9 Recovery And Multiplexing Parity

This workstream adopts the frozen NNRP/1 Preview4 SDK API contract version 9 without exposing
native handles, generations, recovery-token internals, or an earlier-preview compatibility path.

## ABI 4.4 Adoption

- [x] Require Rust FFI ABI `4.4.x` for Preview4 role APIs.
- [x] Add exact `NnrpU16Slice` and `NnrpU32Slice` ctypes layouts.
- [x] Add exact server admission-policy callback, sink, and decision layouts.
- [x] Expand the server bind request with every frozen `ServerSessionOptions` field.
- [x] Expand the session-open request with every frozen `ClientSessionOptions` field.
- [x] Replace the draft resume request with `NnrpSessionOpenRequest` plus the canonical ticket view.
- [x] Bind `nnrp_client_session_recovery_ticket` and preserve its owned native buffer lifetime.
- [x] Assert ABI sizes, offsets, function signatures, and native export availability in tests.

## Canonical Client API

- [x] Replace handle-bearing public options with `NativeClientOptions` and the exact
  `NativeClientSessionOptions` contract fields and defaults.
- [x] Keep application endpoints and provider-local locators in their frozen ownership domains.
- [x] Make one `NativeClientConnection` own many concurrent sessions.
- [x] Expose `open_session(options=None)` without caller-owned handles or generations.
- [x] Expose `resume_session(ticket, options=None)` using an opaque runtime-issued ticket.
- [x] Expose `NativeRuntimeSession.recovery_ticket()` through the public client session surface.
- [x] Keep CLIENT_HELLO / SERVER_HELLO_ACK automatic and prevent application bypass.

## Canonical Recovery Ticket

- [x] Add immutable `NativeSessionRecoveryTicket` with the exact four semantic fields.
- [x] Validate non-zero session id, non-empty token, optional non-zero operation id, and u32/u64 bounds.
- [x] Encode the exact little-endian 28-byte `NRTK` version 1 prefix plus opaque token.
- [x] Reject wrong magic/version, reserved flags, invalid identities, truncation, and trailing bytes.
- [x] Never expose a constructor path that fabricates a runtime-issued ticket from a fresh open.

## Canonical Server API

- [x] Add `NativeServerSessionOptions`, `NativeServerSessionPolicyDecision`, and the typed policy callback.
- [x] Make `NativeServerBootstrapOptions` own endpoint, provider routes, policy, and session defaults.
- [x] Restrict `NativeServerAcceptOptions` to timeout only.
- [x] Keep native server/session handles and generations internal.
- [x] Preserve policy diagnostic bytes for the complete callback invocation and reject invalid decisions.
- [x] Preserve many sessions per logical server and multiplexed carrier behavior.

## Contract, Conformance, And Release Gates

- [x] Upgrade the Python machine-contract checker and its drift tests to contract version 9.
- [x] Validate every Python v9 language projection, role operation, option field, and default.
- [x] Add unit tests for ticket persistence, options, policy decisions, multi-session ownership, and resume.
- [x] Run real Rust FFI client/server recovery and multiplexing E2E against the audited Rust commit.
- [x] Point wire-conformance CI at the audited Rust commit and run the full Preview4 selected case set.
- [x] Keep coarse role-level FFI calls; ticket retrieval and resume must not add per-frame FFI crossings.
- [x] Run lint, machine-contract parity, full tests, total coverage, incremental coverage, conformance,
  wire E2E, wheel build, and artifact inspection before committing.
- [x] Update Python SDK docs and release notes only after the implementation and tests match the frozen API.
