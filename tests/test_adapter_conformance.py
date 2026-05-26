import json
from pathlib import Path

import pytest

from nnrp.native import NativeArtifactError
from nnrp.tools import adapter_conformance
from nnrp.tools.adapter_conformance import build_adapter_case_results_report, main, write_adapter_case_results


def _write_plan(tmp_path: Path) -> Path:
    plan_path = tmp_path / "adapter-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "$schema": "../../schemas/adapter-execution-plan.schema.json",
                "protocol_version": "nnrp-1",
                "suite_version": "nnrp-1-bootstrap",
                "implementation_name": "nnrp-py",
                "artifacts": {
                    "results_path": "artifacts/adapter-results.json",
                    "evidence_dir": "artifacts/evidence",
                },
                "cases": [
                    {
                        "id": "l1.handshake.basic",
                        "layer": "L1",
                        "status": "mandatory",
                        "feature": "handshake",
                        "required_capabilities": ["control.client_hello"],
                        "description": "Basic handshake path.",
                    },
                    {
                        "id": "l1.session.open_close",
                        "layer": "L1",
                        "status": "mandatory",
                        "feature": "session_lifecycle",
                        "required_capabilities": ["control.session_open"],
                        "description": "Open and close a session.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan_path


def test_build_adapter_case_results_report_executes_supported_cases() -> None:
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1",
            "cases": [
                {"id": "l1.handshake.basic"},
                {"id": "l1.session.open_close"},
                {"id": "l1.cache.unimplemented"},
            ],
        }
    )

    assert report["implementation_name"] == "nnrp-py"
    assert [result["id"] for result in report["results"]] == [
        "l1.handshake.basic",
        "l1.session.open_close",
        "l1.cache.unimplemented",
    ]
    assert [result["outcome"] for result in report["results"]] == ["pass", "pass", "skip"]


def test_build_adapter_case_results_report_marks_runtime_smoke_failures() -> None:
    class RejectingBackend:
        def connect(self, *, connection_id: int, generation: int, transport_id: int) -> object:
            raise RuntimeError("boom")

        def bootstrap_connection(self, *, connection_id: int, generation: int, transport_id: int) -> object:
            raise RuntimeError("boom")

    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1",
            "cases": [{"id": "l1.handshake.basic"}],
        },
        backend=RejectingBackend(),
    )

    assert report["results"][0]["outcome"] == "fail"
    assert "boom" in report["results"][0]["message"]


def test_build_adapter_case_results_report_executes_all_supported_smoke_paths() -> None:
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1",
            "cases": [
                {"id": "l1.frame_submit.tensor.inline"},
                {"id": "l1.frame_submit.tensor.inline.routing.validation"},
                {"id": "l1.result_push.basic.terminal.validation"},
            ],
        }
    )

    assert [result["outcome"] for result in report["results"]] == ["pass", "pass", "pass"]


def test_adapter_backend_loader_can_require_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_native_backend(*args: object, **kwargs: object) -> object:
        raise NativeArtifactError("missing native")

    monkeypatch.setattr(adapter_conformance, "select_native_runtime_backend", reject_native_backend)
    monkeypatch.setenv("NNRP_ADAPTER_REQUIRE_NATIVE", "true")

    with pytest.raises(NativeArtifactError, match="missing native"):
        adapter_conformance._load_adapter_backend()


def test_adapter_smoke_backend_bootstrap_and_closed_session_guards() -> None:
    backend = adapter_conformance._AdapterSmokeBackend()
    connection = backend.bootstrap_connection(connection_id=7, generation=2, transport_id=1)
    session = connection.open_session(
        requested_session_id=8,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    operation = session.submit_operation(
        operation_id=9,
        frame_id=10,
        payload=memoryview(b"payload"),
        parent_operation_id=1,
        operation_group_id=2,
    )
    result = session.poll_result(operation, max_events=1)
    session.control(control_code=11, payload=bytearray(b"control"))
    session.cancel(frame_id=10)
    session.close()

    assert result.payload == b"payload"
    assert session.controls == [(11, b"control")]
    assert session.cancelled_frames == [10]
    with pytest.raises(RuntimeError, match="closed"):
        session.cancel(frame_id=10)


def test_main_reads_paths_from_environment_and_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_path = _write_plan(tmp_path)
    output_path = tmp_path / "artifacts" / "adapter-results.json"
    monkeypatch.setenv("NNRP_CONFORMANCE_ADAPTER_PLAN", str(plan_path))
    monkeypatch.setenv("NNRP_CONFORMANCE_ADAPTER_RESULTS", str(output_path))

    assert main([]) == 0

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["protocol_version"] == "nnrp-1"
    assert len(report["results"]) == 2
    assert report["results"][0]["outcome"] == "pass"


def test_main_accepts_explicit_cli_paths_and_creates_parent_directory(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)
    output_path = tmp_path / "nested" / "artifacts" / "adapter-results.json"

    assert main(["--plan", str(plan_path), "--output", str(output_path)]) == 0

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["implementation_name"] == "nnrp-py"
    assert [result["id"] for result in report["results"]] == [
        "l1.handshake.basic",
        "l1.session.open_close",
    ]


def test_main_uses_argparse_error_when_required_paths_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NNRP_CONFORMANCE_ADAPTER_PLAN", raising=False)
    monkeypatch.delenv("NNRP_CONFORMANCE_ADAPTER_RESULTS", raising=False)

    with pytest.raises(SystemExit, match="2"):
        main([])


def test_write_adapter_case_results_rejects_missing_plan_path(tmp_path: Path) -> None:
    output_path = tmp_path / "artifacts" / "adapter-results.json"

    with pytest.raises(ValueError, match="adapter execution plan path does not exist"):
        write_adapter_case_results(tmp_path / "missing-plan.json", output_path)


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ([], "must be a JSON object"),
        ({"protocol_version": "nnrp-1"}, "cases list"),
        (
            {
                "protocol_version": "nnrp-1",
                "cases": ["l1.handshake.basic"],
            },
            "JSON objects",
        ),
    ],
)
def test_write_adapter_case_results_rejects_invalid_plan_shapes(
    tmp_path: Path,
    document: object,
    match: str,
) -> None:
    plan_path = tmp_path / "adapter-plan.json"
    output_path = tmp_path / "artifacts" / "adapter-results.json"
    plan_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        write_adapter_case_results(plan_path, output_path)
