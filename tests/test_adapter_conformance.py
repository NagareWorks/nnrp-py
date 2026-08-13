import json
from pathlib import Path

import pytest

from nnrp.client import SubmitIdentity, SubmitPolicy, SubmitRequest, TokenChunk, TokenSubmitInput
from nnrp.native import FFI_STATUS_INVALID_ARGUMENT, NativeInvalidArgumentError, NativeStatus
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


def _submit_request(operation_id: int, frame_id: int, body: bytes) -> SubmitRequest:
    return SubmitRequest.token(
        TokenSubmitInput(
            identity=SubmitIdentity(operation_id=operation_id, frame_id=frame_id),
            policy=SubmitPolicy(),
            chunks=(TokenChunk(body),),
        )
    )


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


def test_build_adapter_case_results_report_executes_preview4_common_header_golden(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "artifacts": {"evidence_dir": str(evidence_dir)},
            "cases": [{"id": "l0.header.fixed_shape.golden"}],
        }
    )

    result = report["results"][0]
    assert result["outcome"] == "pass"
    evidence = json.loads((evidence_dir / "l0-header-fixed_shape-golden.json").read_text())
    assert evidence["header_hex"] == (
        "4e4e5250010010282100000003020100060504004433221188776655aa99ccbb0807060504030201"
    )
    assert evidence["session_id"] == 0x11223344
    assert evidence["frame_id"] == 0x55667788
    assert evidence["view_id"] == 0x99AA
    assert evidence["route_id"] == 0xBBCC
    assert evidence["trace_id"] == 0x0102030405060708


def test_build_adapter_case_results_report_executes_current_typed_payload_golden(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "artifacts": {"evidence_dir": str(evidence_dir)},
            "cases": [{"id": "l0.typed_payload.descriptor.current.golden"}],
        }
    )

    assert report["results"][0]["outcome"] == "pass"
    evidence = json.loads(
        (evidence_dir / "l0-typed_payload-descriptor-current-golden.json").read_text(encoding="utf-8")
    )
    assert evidence == {
        "action": "typed-payload-descriptor-golden",
        "case_id": "l0.typed_payload.descriptor.current.golden",
        "descriptor_flags": 2,
        "descriptor_hex": "020002020110000003000000020000000800000018000000",
        "length": 24,
        "offset": 8,
        "payload_kind": 2,
        "profile_id": 2,
        "schema_id": 0x1001,
        "schema_version": 3,
        "stream_semantics": 2,
    }


def test_build_adapter_case_results_report_executes_all_selected_preview4_cases() -> None:
    case_ids = [
        "l0.header.fixed_shape.golden",
        "l0.body_region.prelude.golden",
        "l0.typed_payload.descriptor.golden",
        "l0.typed_payload.frame_regions.golden",
        "l1.typed_payload.region.pack",
        "l0.typed_payload.descriptor.current.golden",
        "l1.control.cancel-abort",
        "l1.control.priority-deadline",
        "l1.control.progress-backpressure",
        "l1.control.capability-costs",
        "l1.object.lifecycle",
        "l1.object.delta",
        "l1.control.route-execution-hint",
        "l1.control.cache-reference",
        "l1.control.degrade-budget",
    ]
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "cases": [{"id": case_id} for case_id in case_ids],
        }
    )

    assert [result["id"] for result in report["results"]] == case_ids
    assert [result["outcome"] for result in report["results"]] == ["pass"] * len(case_ids)


@pytest.mark.parametrize(
    ("descriptor", "message"),
    [
        (b"short", "must be 16 bytes"),
        (bytes.fromhex("10010300040000000700000000000000"), "reserved fields"),
        (bytes.fromhex("03000300040000000700000000000000"), "payload_kind is invalid"),
    ],
)
def test_baseline_descriptor_rejects_malformed_wire_values(descriptor: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        adapter_conformance._BaselineTypedPayloadDescriptor.unpack(descriptor)


@pytest.mark.parametrize(
    ("descriptors", "payload", "message"),
    [
        (b"short", b"", "multiple of 16"),
        (bytes.fromhex("02000100010000000100000000000000"), b"x", "contiguous"),
        (bytes.fromhex("02000100000000000200000000000000"), b"x", "exceeds payload"),
        (bytes.fromhex("02000100000000000100000000000000"), b"xy", "exactly covered"),
    ],
)
def test_baseline_descriptor_region_rejects_invalid_coverage(
    descriptors: bytes,
    payload: bytes,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        adapter_conformance._parse_baseline_typed_payload_region(descriptors, payload)


def test_preview4_common_header_case_rejects_wire_reencoding_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    class DriftingHeader:
        @classmethod
        def unpack(cls, _payload: bytes) -> "DriftingHeader":
            return cls()

        @staticmethod
        def pack() -> bytes:
            return b"drift"

    monkeypatch.setattr(adapter_conformance, "NnrpHeader", DriftingHeader)
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "cases": [{"id": "l0.header.fixed_shape.golden"}],
        }
    )

    assert report["results"][0]["outcome"] == "fail"
    assert "canonical wire bytes" in report["results"][0]["message"]


def test_preview4_common_header_case_rejects_runtime_projection_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    class DriftingRuntimeFrame:
        header = None

    monkeypatch.setattr(adapter_conformance, "decode_websocket_binary_frame", lambda _frame: DriftingRuntimeFrame())
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "cases": [{"id": "l0.header.fixed_shape.golden"}],
        }
    )

    assert report["results"][0]["outcome"] == "fail"
    assert "caller-controlled wire fields" in report["results"][0]["message"]


def test_build_adapter_case_results_report_marks_runtime_smoke_failures() -> None:
    class RejectingBackend:
        def connect(self, *, connection_id: int, generation: int, transport_connection: object) -> object:
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


def test_build_adapter_case_results_report_executes_all_preview4_runtime_cases() -> None:
    case_ids = [
        "l1.control.cancel-abort",
        "l1.control.priority-deadline",
        "l1.control.progress-backpressure",
        "l1.control.capability-costs",
        "l1.object.lifecycle",
        "l1.object.delta",
        "l1.control.route-execution-hint",
        "l1.control.cache-reference",
        "l1.control.degrade-budget",
        "l1.control.supersede",
        "l1.control.recoverable-error",
    ]

    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "cases": [{"id": case_id} for case_id in case_ids],
        }
    )

    assert [result["id"] for result in report["results"]] == case_ids
    assert [result["outcome"] for result in report["results"]] == ["pass"] * len(case_ids)


def test_build_adapter_case_results_report_uses_case_parameters_and_writes_evidence(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"

    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1",
            "artifacts": {"evidence_dir": str(evidence_dir)},
            "cases": [
                {
                    "id": "l1.result_push.basic.terminal.validation",
                    "parameters": {
                        "connection_id": 7,
                        "session_id": 8,
                        "operation_id": 9,
                        "frame_id": 10,
                        "payload": [1, 2, 3],
                        "max_events": 2,
                    },
                },
            ],
        }
    )

    assert report["results"][0]["outcome"] == "pass"
    evidence = json.loads((evidence_dir / "l1-result_push-basic-terminal-validation.json").read_text())
    assert evidence["case_id"] == "l1.result_push.basic.terminal.validation"
    assert evidence["session_id"] == 8
    assert evidence["operation_id"] == 9
    assert evidence["frame_id"] == 10
    assert evidence["result_payload_bytes"] == 3


def test_adapter_case_parameter_validation_failures_are_reported() -> None:
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1",
            "cases": [
                {
                    "id": "l1.handshake.basic",
                    "parameters": {"connection_id": "bad"},
                },
                {
                    "id": "l1.result_push.basic.terminal.validation",
                    "parameters": {"expected_result_state": ""},
                },
                {
                    "id": "l1.frame_submit.tensor.inline",
                    "parameters": {"payload": [256]},
                },
            ],
        }
    )

    assert [result["outcome"] for result in report["results"]] == ["fail", "fail", "fail"]
    assert [result["diagnostic"]["error_type"] for result in report["results"]] == [
        "ValueError",
        "ValueError",
        "ValueError",
    ]


def test_adapter_case_rejects_invalid_parameter_container() -> None:
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1",
            "cases": [
                {
                    "id": "l1.handshake.basic",
                    "parameters": [],
                },
            ],
        }
    )

    assert report["results"][0]["outcome"] == "fail"
    assert "parameters" in report["results"][0]["message"]


def test_adapter_result_state_validation_failure_is_reported() -> None:
    class StatefulResult(adapter_conformance._AdapterSmokeResult):
        state = "completed"

    class StatefulSession(adapter_conformance._AdapterSmokeSession):
        def poll_result(
            self,
            operation: adapter_conformance._AdapterSmokeOperation,
            *,
            max_events: int | None = None,
            timeout_ms: int = 0,
        ) -> StatefulResult:
            del max_events, timeout_ms
            return StatefulResult(operation.operation_id, operation.frame_id, operation.body)

    class StatefulConnection(adapter_conformance._AdapterSmokeConnection):
        def open_session(
            self,
            *,
            requested_session_id: int,
            generation: int,
            profile_id: int,
            schema_id: int,
            schema_version: int,
        ) -> StatefulSession:
            return StatefulSession(
                connection=self,
                session_id=requested_session_id,
                generation=generation,
                profile_id=profile_id,
                schema_id=schema_id,
                schema_version=schema_version,
            )

    class StatefulBackend(adapter_conformance._AdapterSmokeBackend):
        def connect(
            self,
            *,
            connection_id: int,
            generation: int,
            transport_connection: adapter_conformance._AdapterSmokeCarrier,
        ) -> StatefulConnection:
            transport_connection.consume()
            return StatefulConnection(connection_id, generation, transport_connection)

    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1",
            "cases": [
                {
                    "id": "l1.result_push.basic.terminal.validation",
                    "parameters": {"expected_result_state": "failed"},
                }
            ],
        },
        backend=StatefulBackend(),
    )

    assert report["results"][0]["outcome"] == "fail"
    assert "expected result state" in report["results"][0]["message"]


def test_adapter_result_terminal_uses_submit_then_role_event_poll() -> None:
    class NativeLikeResult:
        def __init__(self, operation_id: int, frame_id: int, body: bytes) -> None:
            self.operation_id = operation_id
            self.frame_id = frame_id
            self.body = body
            self.state = "completed"

    class NativeLikeSession:
        frame_id = 0

        def __init__(self) -> None:
            self.closed = False
            self.submitted: list[tuple[int, int, bytes]] = []
            self.polls: list[tuple[int | None, int]] = []

        def submit_operation(
            self,
            request: SubmitRequest,
        ) -> adapter_conformance._AdapterSmokeOperation:
            body = b"".join(adapter_conformance._typed_payload_bytes(request.metadata, request.body))
            self.submitted.append((request.operation_id, request.frame_id, body))
            return adapter_conformance._AdapterSmokeOperation(
                request.operation_id,
                request.frame_id,
                body,
            )

        def poll_result(
            self,
            operation: adapter_conformance._AdapterSmokeOperation,
            *,
            max_events: int | None = None,
            timeout_ms: int = 0,
        ) -> NativeLikeResult:
            self.polls.append((max_events, timeout_ms))
            return NativeLikeResult(operation.operation_id, operation.frame_id, operation.body)

        def close(self) -> None:
            self.closed = True

    class NativeLikeConnection:
        def __init__(self) -> None:
            self.session = NativeLikeSession()
            self.batch_polls = 0

        def open_session(self, **_kwargs):
            return self.session

        def poll_events_batch(self, *, max_events: int):
            self.batch_polls += 1
            assert max_events == 8
            return ()

    class NativeLikeBackend:
        def __init__(self) -> None:
            self.connection = NativeLikeConnection()

        def connect(self, *, connection_id: int, generation: int, transport_connection: object):
            assert (connection_id, generation) == (1, 1)
            return self.connection

    backend = NativeLikeBackend()

    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1",
            "cases": [
                {
                    "id": "l1.result_push.basic.terminal.validation",
                    "parameters": {
                        "operation_id": 9,
                        "frame_id": 10,
                        "payload": [1, 2, 3],
                        "max_events": 2,
                    },
                }
            ],
        },
        backend=backend,
    )

    assert report["results"][0]["outcome"] == "pass"
    assert backend.connection.batch_polls == 1
    assert backend.connection.session.submitted == [(9, 10, b"\x01\x02\x03")]
    assert backend.connection.session.polls == [(2, 0)]


def test_adapter_runtime_helpers_read_native_handle_shapes() -> None:
    class Handle:
        def __init__(self) -> None:
            self.id = 123

    class Wrapper:
        def __init__(self) -> None:
            self.handle = Handle()
            self._closed = True

    assert adapter_conformance._runtime_id(Wrapper()) == 123
    assert adapter_conformance._runtime_id(object()) == 0
    assert adapter_conformance._runtime_closed(Wrapper()) is True
    assert adapter_conformance._runtime_closed(object()) is False


def test_adapter_evidence_dir_resolution_ignores_invalid_shapes(tmp_path: Path) -> None:
    assert adapter_conformance._resolve_evidence_dir({}) is None
    assert adapter_conformance._resolve_evidence_dir({"artifacts": {"evidence_dir": ""}}) is None
    assert adapter_conformance._resolve_evidence_dir(
        {"artifacts": {"evidence_dir": "evidence"}},
        base_dir=tmp_path,
    ) == tmp_path / "evidence"


def test_adapter_case_failure_preserves_native_diagnostics() -> None:
    class RejectingOperationBackend:
        def connect(
            self,
            *,
            connection_id: int,
            generation: int,
            transport_connection: adapter_conformance._AdapterSmokeCarrier,
        ) -> object:
            transport_connection.consume()
            return adapter_conformance._AdapterSmokeConnection(connection_id, generation, transport_connection)

    class RejectingExecution(adapter_conformance._AdapterCaseExecution):
        def _submit_operation(self, session: object) -> object:
            raise NativeInvalidArgumentError(NativeStatus(FFI_STATUS_INVALID_ARGUMENT, 12, 34, 56))

    original_execution = adapter_conformance._AdapterCaseExecution
    adapter_conformance._AdapterCaseExecution = RejectingExecution
    try:
        report = build_adapter_case_results_report(
            {
                "protocol_version": "nnrp-1",
                "cases": [{"id": "l1.frame_submit.tensor.inline"}],
            },
            backend=RejectingOperationBackend(),
        )
    finally:
        adapter_conformance._AdapterCaseExecution = original_execution

    result = report["results"][0]
    assert result["outcome"] == "fail"
    assert result["diagnostic"] == {
        "status_code": FFI_STATUS_INVALID_ARGUMENT,
        "error_family": 12,
        "protocol_error_code": 34,
        "detail_code": 56,
    }


def test_adapter_smoke_backend_bootstrap_and_closed_session_guards() -> None:
    backend = adapter_conformance._AdapterSmokeBackend()
    carrier = adapter_conformance._AdapterSmokeCarrier()
    connection = backend.connect(connection_id=7, generation=2, transport_connection=carrier)
    session = connection.open_session(
        requested_session_id=8,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    request = _submit_request(9, 10, b"payload")
    operation = session.submit_operation(
        request,
        parent_operation_id=1,
        operation_group_id=2,
    )
    result = session.poll_result(operation, max_events=1)
    session.send_route_hint(adapter_conformance.RouteHintMetadata(9, 11, 0, 0, 0, 7, 0), b"control")
    session.cancel(frame_id=10)
    session.close()

    assert result.body == b"payload"
    assert session.controls == [
        (
            int(adapter_conformance.MessageType.ROUTE_HINT),
            adapter_conformance.encode_runtime_control_metadata(
                adapter_conformance.MessageType.ROUTE_HINT,
                adapter_conformance.RouteHintMetadata(9, 11, 0, 0, 0, 7, 0),
                tail=b"control",
            ),
        )
    ]
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
    assert (tmp_path / "artifacts" / "evidence" / "l1-handshake-basic.json").is_file()


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
