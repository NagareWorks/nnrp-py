from pathlib import Path

CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
WIRE_E2E_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_wire_e2e.ps1"


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
        "control.supersede",
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
        "control.recoverable_error",
        "object.lifecycle",
        "object.delta",
        "object.cost",
        "object.ownership",
        "cache.reference",
    ):
        assert f'"{capability}"' in manifest


def test_ci_runs_independent_process_wire_conformance() -> None:
    workflow = _read_ci_workflow()

    assert "wire-conformance:" in workflow
    assert "NNRP_RS_SOURCE_COMMIT: a00b77af4e13fc5a21473f6c90a288bd3d248fc7" in workflow
    assert "Checkout pinned nnrp-rs source" in workflow
    assert "ref: ${{ env.NNRP_RS_SOURCE_COMMIT }}" in workflow
    for transport in ("tcp", "quic", "ipc", "websocket"):
        assert f"--transport-scope {transport}" in workflow
    assert "python scripts/package_native_artifacts.py" in workflow
    assert "prepare_native_artifacts.py --clean nnrp-rs-source/artifacts/native" in workflow
    assert "gh release download" not in workflow
    assert "./scripts/run_wire_e2e.ps1" in workflow
    assert "Run independent-process Preview4 wire E2E" in workflow
    assert "run-plan" not in workflow
    assert "- wire-conformance" in workflow


def test_ci_wire_conformance_declares_preview4_modes_transports_and_capabilities() -> None:
    script = WIRE_E2E_SCRIPT.read_text(encoding="utf-8")

    reset_index = script.index("Remove-Item -LiteralPath $modeDirectory -Recurse -Force")
    create_index = script.index("New-Item -ItemType Directory -Force -Path $modeDirectory")
    assert reset_index < create_index

    for mode in ("suite_as_client", "suite_as_server", "suite_as_proxy"):
        assert f'"{mode}"' in script

    for transport in (
        "tcp=127.0.0.1:$tcpPort",
        "quic=127.0.0.1:$quicPort",
        "ipc=$ipcEndpoint",
        "websocket=wss://localhost:$webSocketPort/nnrp",
    ):
        assert f'"--transport", "{transport}"' in script

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
        assert f'"{capability}"' in script

    assert '"wire-plan"' in script
    assert '"wire-run"' in script
    assert '"validate-wire-results"' in script
    assert 'outcome -ne "passed"' in script
    assert "Expected six Preview4 wire scenarios" in script
    assert '$mode -in @("suite_as_client", "suite_as_server")' in script
    assert 'transport = "tcp"' in script
