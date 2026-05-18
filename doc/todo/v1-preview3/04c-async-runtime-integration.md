# Python Preview3 Async Runtime Integration

- [ ] Adapt Rust callback/polling result delivery into Python async-friendly primitives.
- [ ] Expose structured event, tool delta, and workflow-state updates through Python-native async iterators or callbacks backed by Rust result pumps.
- [ ] Keep any remaining pure-Python codec code limited to preview2 fixture inspection or other non-hot-path tooling.