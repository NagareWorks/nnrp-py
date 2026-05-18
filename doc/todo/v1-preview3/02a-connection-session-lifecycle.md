# Python Preview3 Connection And Session Lifecycle

- [ ] Add Python-facing preview3 connection bootstrap helpers on top of Rust connection handles.
- [ ] Add explicit preview3 session-open helpers rather than assuming one active session per connection.
- [ ] Model multi-session connection state on top of Rust handles instead of Python-managed registries.
- [ ] Add explicit session-close helpers separate from connection close.
- [ ] Replace preview2 single-session helpers with the preview3 connection/session model in place.