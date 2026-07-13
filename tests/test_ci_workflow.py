from pathlib import Path

CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _read_ci_workflow() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def test_ci_runs_unit_tests_with_incremental_coverage_gate() -> None:
    workflow = _read_ci_workflow()

    assert "Test with coverage" in workflow
    assert "pytest @pytestArgs" in workflow
    assert "--cov=src/nnrp" in workflow
    assert "--cov=scripts" in workflow
    assert "--cov-report=xml:artifacts/coverage/coverage.xml" in workflow
    assert "--cov-fail-under=90" in workflow
    assert "check_incremental_coverage.py" in workflow
    assert "--threshold 90" in workflow


def test_ci_runs_adapter_conformance_as_required_job() -> None:
    workflow = _read_ci_workflow()

    assert "conformance:" in workflow
    assert "Run suite-owned conformance action" in workflow
    assert "python-path }} -m nnrp.tools.adapter_conformance" in workflow
    assert "- conformance" in workflow


def test_adapter_conformance_manifest_claims_preview4_runtime_capabilities() -> None:
    manifest_path = CI_WORKFLOW.parents[2] / "conformance" / "nnrp-1-preview4.capabilities.json"
    manifest = manifest_path.read_text(encoding="utf-8")

    assert '"protocol_version": "nnrp-1-preview4"' in manifest
    for capability in (
        "control.cancel_abort",
        "control.priority_update",
        "control.deadline_expire",
        "control.progress_partial",
        "control.credit_backpressure",
        "control.capability_costs",
        "control.route_execution_hint",
        "control.trace_context",
        "control.result_drop_reason",
        "control.degrade_profile",
        "control.budget_update",
        "object.lifecycle",
        "object.delta",
        "object.cost",
        "object.ownership",
        "cache.reference",
    ):
        assert f'"{capability}"' in manifest


def test_ci_runs_wire_conformance_plan_and_result_validation() -> None:
    workflow = _read_ci_workflow()

    assert "wire-conformance:" in workflow
    assert "nnrp-conformance-runner/wire-conformance/nnrp-1-preview4/manifest.json" in workflow
    assert "wire-plan" in workflow
    assert "python-path }} -m nnrp.tools.wire_conformance" in workflow
    assert "run-plan" in workflow
    assert "validate-wire-results" in workflow
    assert "- wire-conformance" in workflow


def test_ci_wire_conformance_declares_preview4_modes_transports_and_capabilities() -> None:
    workflow = _read_ci_workflow()

    for mode in ("suite_as_client", "suite_as_server", "suite_as_proxy"):
        assert f"for mode in {mode}" in workflow or mode in workflow

    for transport in (
        "tcp=127.0.0.1:19091",
        "quic=quic+tls://127.0.0.1:19092",
        "ipc=unix:///tmp/nnrp.sock",
        "websocket=wss://127.0.0.1:19093/nnrp",
    ):
        assert f"--transport {transport}" in workflow

    for capability in (
        "control.cancel_abort",
        "control.result_drop_reason",
        "control.trace_context",
        "control.priority_update",
        "control.deadline_expire",
        "control.progress_partial",
        "control.credit_backpressure",
        "object.lifecycle",
        "control.capability_costs",
        "control.route_execution_hint",
        "cache.reference",
        "control.degrade_profile",
        "control.budget_update",
    ):
        assert f"--capability {capability}" in workflow
