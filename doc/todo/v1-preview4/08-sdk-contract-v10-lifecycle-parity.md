# SDK Contract v10 Lifecycle Parity

This workstream adopts the frozen NNRP/1 Preview4 SDK API contract version 10. It adds the Python
connection and session lifecycle snapshot projection without exposing native handles as protocol
session identifiers or inventing wire state that the native role runtime has not reported.

## Public lifecycle projection

- [x] Add `nnrp.lifecycle.ConnectionLifecycleState` with the frozen `open`, `closing`, and `closed` values.
- [x] Add `nnrp.lifecycle.SessionLifecycleState` with the frozen `open`, `resumed`, `closing`, `draining`, and `closed` values.
- [x] Add immutable `SessionLifecycleSnapshot` with exactly the ten frozen protocol-derived fields.
- [x] Add immutable `ConnectionLifecycleSnapshot` with deterministic session ordering.
- [x] Keep `accepts_session_scoped_messages` and `accepts_new_operations` as derived convenience properties, not stored wire fields.
- [x] Reject invalid wire widths, zero session identifiers, duplicate sessions, and non-closed sessions in a closed connection snapshot.
- [x] Preserve `resumed` as a distinct established state so a rejected close can be represented without collapsing it to `open`.

## Contract and validation

- [x] Upgrade the machine-contract checker and drift fixtures to contract version 10.
- [x] Verify the exact Python lifecycle projection names, fields, enum values, and module exports.
- [x] Add positive and negative lifecycle snapshot tests.
- [x] Run formatting, lint, targeted type checking for the changed public surface, full tests, coverage, package checks, and independent wire conformance before push.
