# Wire Conformance

Python exposes a suite-owned wire conformance wrapper through `nnrp.tools.wire_conformance`.
The wrapper does not generate protocol cases locally. It declares the Python target surface, accepts a suite-owned wire
execution plan, and emits schema-valid result JSON for the conformance runner.

Wire conformance stays separate from adapter conformance:

1. Adapter conformance exercises Python host APIs and smoke paths.
2. Wire conformance describes endpoint-level behavior that the suite can test as a client, server, or proxy.
3. Live wire execution must run against explicit endpoints; when no live endpoint is enabled, Python writes explicit
   skipped results instead of silently claiming protocol coverage.

## Target Manifest

Generate a target manifest with the transports and modes that the current Python environment exposes:

```powershell
python -m nnrp.tools.wire_conformance manifest `
  --target-name nnrp-py-dev `
  --suite-version 0.1.0 `
  --mode suite_as_client `
  --mode suite_as_server `
  --transport tcp=127.0.0.1:19091 `
  --transport websocket=wss://localhost/nnrp `
  --capability control.cancel_abort `
  --capability control.progress_partial `
  --capability control.trace_context `
  --max-frame-bytes 16777216 `
  --max-in-flight 256 `
  --output artifacts/wire-target.json
```

Transport arguments use `name=endpoint` form. Supported names are `tcp`, `quic`, `ipc`, and `websocket`. TLS is inferred
for `websocket=wss://...` and `quic=quic+tls://...`.

The generated manifest declares:

| Field | Meaning |
| --- | --- |
| `target_name` | Human-readable target name reported to the conformance runner. |
| `protocol_version` | Always `nnrp-1-preview4` for this workstream. |
| `wire_conformance.modes` | `suite_as_client`, `suite_as_server`, and/or `suite_as_proxy`. |
| `wire_conformance.transports` | Explicit endpoint locators for the suite to dial or bind. |
| `wire_conformance.capabilities` | Frozen feature names advertised by this Python target. |
| `wire_conformance.limits` | Frame and in-flight request limits used by the suite planner. |

## Running A Plan

The suite owns the wire execution plan. Python can run the plan wrapper in dry-run form:

```powershell
python -m nnrp.tools.wire_conformance run-plan `
  --plan artifacts/wire-plan.json `
  --mode suite_as_client `
  --output artifacts/wire-results.json `
  --evidence-dir artifacts/wire-evidence
```

When live endpoint execution is not enabled, each selected scenario is emitted as an explicit skipped result. This keeps
CI honest: a skipped live endpoint remains visible to the suite instead of being treated as a passed adapter smoke test.

Use `--skip-message` to make the reason precise in controlled CI jobs:

```powershell
python -m nnrp.tools.wire_conformance run-plan `
  --plan artifacts/wire-plan.json `
  --mode suite_as_server `
  --output artifacts/wire-results.json `
  --skip-message "native websocket provider is not installed on this runner"
```

## Result Validation

Wire result reports are validated against the selected plan:

1. Every selected scenario must produce exactly one result.
2. Passed cases must include the expected observed frames.
3. Skipped cases must include a message.
4. Evidence paths, when present, are written as JSONL files under the requested evidence directory.

This keeps Python aligned with `nnrp-conformance` without letting SDK-local fixtures replace the suite-owned wire-level
client/server/proxy tests.
