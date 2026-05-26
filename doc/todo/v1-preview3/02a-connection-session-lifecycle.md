# Python Preview3 Connection And Session Lifecycle

- [x] Add Python-facing preview3 connection bootstrap helpers on top of Rust connection handles.
- [x] Add explicit preview3 session-open helpers rather than assuming one active session per connection.
- [x] Keep multiple opened session handles addressable from one native connection facade.
- [ ] Add higher-level multi-session routing helpers so hosts do not build private registries.
- [x] Add explicit session-close helpers separate from connection close.
- [x] Add closed-session guards for submit, result polling, cancel, control, and repeated close calls.
- [ ] Replace preview2 single-session helper call sites with the preview3 connection/session model in place.
