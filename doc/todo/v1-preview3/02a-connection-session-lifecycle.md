# Python Preview3 Connection And Session Lifecycle

- [x] Add Python-facing preview3 connection bootstrap helpers on top of Rust connection handles.
- [x] Add explicit preview3 session-open helpers rather than assuming one active session per connection.
- [x] Keep multiple opened session handles addressable from one native connection facade.
- [x] Add higher-level multi-session routing helpers so hosts do not build private registries.
- [x] Add explicit session-close helpers separate from connection close.
- [x] Add closed-session guards for submit, result polling, cancel, control, and repeated close calls.
- [x] Replace preview2 single-session helper call sites with the preview3 connection/session model in place.
  - [x] Inventory public client helper call sites that still assume one session per connection.
  - [x] Redirect client bootstrap examples/tests to `connect_native_client_connection` where packet-level transport smoke is not required.
  - [x] Redirect submit/result examples/tests to native session operations where raw packet coverage is not being tested.
  - [x] Move old single-session transport helpers to tooling/smoke docs if they remain useful for adapter bring-up.
  - [x] Update package exports only after replacement call sites are green.
