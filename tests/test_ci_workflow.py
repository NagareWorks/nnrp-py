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


def test_ci_runs_wire_conformance_plan_and_result_validation() -> None:
    workflow = _read_ci_workflow()

    assert "wire-conformance:" in workflow
    assert "wire-plan" in workflow
    assert "python-path }} -m nnrp.tools.wire_conformance" in workflow
    assert "run-plan" in workflow
    assert "validate-wire-results" in workflow
    assert "- wire-conformance" in workflow
