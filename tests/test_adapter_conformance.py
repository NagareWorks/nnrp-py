import json
from pathlib import Path

import pytest

from nnrp.client import SubmitIdentity, SubmitPolicy, SubmitRequest, TokenChunk, TokenSubmitInput
from nnrp.native import FFI_STATUS_INVALID_ARGUMENT, NativeInvalidArgumentError, NativeStatus
from nnrp.tools import adapter_conformance
from nnrp.tools.adapter_conformance import build_adapter_case_results_report, main, write_adapter_case_results

_FROZEN_PARAMETERS = {
    "l0.header.fixed_shape.golden": {
        "header_hex": "4e4e525001001028210000003000000000100000070000000b0000000200000015cd5b0700000000"
    },
    "l0.body_region.prelude.golden": {
        "metadata_hex": "1800000018000000180000000e00000010000000050000000000000000000000"
    },
    "l0.control.client_hello.golden": {
        "metadata_hex": (
            "0101010001000000010000000300000003000000210000000300000001000700"
            "0100020040000000000001007017640002000000000000006000000000000000"
        )
    },
    "l0.control.session_patch_ack.golden": {
        "metadata_hex": (
            "010003001100000044000000000000000200000028230000680105000300000000000000010000000300000010000000"
        )
    },
    "l0.flow_update.packet.golden": {
        "packet_hex": (
            "4e4e525001001728000000002000000000000000150000000000000000000600"
            "0d00000000000000010402000000010000000000000000000000000028000000"
            "0500000003000000"
        )
    },
    "l0.result_hint.packet.golden": {
        "packet_hex": (
            "4e4e525001001828000000001000000000000000150000002f01000000000700"
            "0e000000000000000300000003000000030000003c000000"
        )
    },
    "l0.frame_submit.metadata.golden": {
        "metadata_hex": (
            "80026801200020005400020001020000640070170700000000000000c0000000"
            "00000000000000000807060504030201000000000205ff000300000029000000"
            "1100000002000000"
        )
    },
    "l0.result_push.metadata.golden": {
        "metadata_hex": (
            "0000050001005400020000004b0302004e030000000000001000000000000000"
            "000000000000000000000000010100002900000035001f000300000003000000"
        )
    },
    "l0.object_reference.block.golden": {"metadata_hex": "020000000700000044332211000000008877665500000000"},
    "l0.typed_payload.descriptor.golden": {"descriptor_hex": "10000300040000000700000000000000"},
    "l0.typed_payload.frame_regions.golden": {
        "descriptor_region_hex": (
            "0200010000000000030000000000000004000200030000000200000000000000"
            "08000300050000000500000000000000100004000a0000000300000000000000"
        ),
        "payload_hex": "746f6b6175766964656f657674",
    },
    "l0.typed_payload.descriptor.current.golden": {
        "descriptor_hex": "020002020110000003000000020000000800000018000000"
    },
}


def _case(case_id: str) -> dict[str, object]:
    return {"id": case_id, "parameters": _FROZEN_PARAMETERS.get(case_id, {})}


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
    assert [result["outcome"] for result in report["results"]] == ["pass", "pass", "fail"]


def test_build_adapter_case_results_report_executes_preview4_common_header_golden(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "artifacts": {"evidence_dir": str(evidence_dir)},
            "cases": [_case("l0.header.fixed_shape.golden")],
        }
    )

    result = report["results"][0]
    assert result["outcome"] == "pass"
    evidence = json.loads((evidence_dir / "l0-header-fixed_shape-golden.json").read_text())
    assert evidence["header_hex"] == _FROZEN_PARAMETERS["l0.header.fixed_shape.golden"]["header_hex"]
    assert evidence["session_id"] == 7
    assert evidence["frame_id"] == 11
    assert evidence["view_id"] == 2
    assert evidence["route_id"] == 0
    assert evidence["trace_id"] == 123456789


def test_build_adapter_case_results_report_executes_current_typed_payload_golden(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "artifacts": {"evidence_dir": str(evidence_dir)},
            "cases": [_case("l0.typed_payload.descriptor.current.golden")],
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
        "l0.control.client_hello.golden",
        "l0.control.session_patch_ack.golden",
        "l0.flow_update.packet.golden",
        "l0.result_hint.packet.golden",
        "l0.frame_submit.metadata.golden",
        "l0.result_push.metadata.golden",
        "l0.body_region.prelude.golden",
        "l0.object_reference.block.golden",
        "l0.typed_payload.descriptor.golden",
        "l0.typed_payload.frame_regions.golden",
        "l1.typed_payload.region.pack",
        "l1.flow_update.metadata.validation",
        "l1.result_hint.metadata.validation",
        "l1.cache.lifecycle.roundtrip",
        "l1.transport_probe.metadata.roundtrip",
        "l1.frame_submit.message.parse_emit",
        "l1.result_push.message.parse_emit",
        "l1.result_push.object_reference.resolve",
        "l3.transport.probe.selection",
        "l3.transport.tcp.session_smoke",
        "l3.transport.quic.session_smoke",
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
            "cases": [_case(case_id) for case_id in case_ids],
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
            "cases": [_case("l0.header.fixed_shape.golden")],
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
            "cases": [_case("l0.header.fixed_shape.golden")],
        }
    )

    assert report["results"][0]["outcome"] == "fail"
    assert "caller-controlled wire fields" in report["results"][0]["message"]


def test_metadata_roundtrip_helper_rejects_reencoding_drift() -> None:
    class DriftingMetadata:
        @classmethod
        def unpack(cls, _payload: bytes) -> "DriftingMetadata":
            return cls()

        def pack(self) -> bytes:
            return b"drift"

    execution = adapter_conformance._AdapterCaseExecution(
        {"id": "l1.handshake.basic", "parameters": {"metadata_hex": "00"}},
        adapter_conformance._AdapterSmokeBackend(),
        {},
    )

    with pytest.raises(ValueError, match="did not preserve the frozen wire bytes"):
        execution._round_trip_metadata("drift", DriftingMetadata, "metadata_hex")


def test_packet_roundtrip_helper_rejects_message_type_and_reencoding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Metadata:
        @classmethod
        def unpack(cls, _payload: bytes) -> "Metadata":
            return cls()

        def pack(self) -> bytes:
            return b"meta"

    class Packet:
        def __init__(self, message_type: adapter_conformance.MessageType, encoded: bytes) -> None:
            self.header = type("Header", (), {"msg_type": message_type})()
            self.metadata = b"meta"
            self._encoded = encoded

        def pack(self) -> bytes:
            return self._encoded

    execution = adapter_conformance._AdapterCaseExecution(
        {"id": "l1.handshake.basic", "parameters": {"packet_hex": "00"}},
        adapter_conformance._AdapterSmokeBackend(),
        {},
    )

    monkeypatch.setattr(
        adapter_conformance.NnrpPacket,
        "unpack",
        lambda _payload: Packet(adapter_conformance.MessageType.CLIENT_HELLO, b"\x00"),
    )
    with pytest.raises(ValueError, match="expected FLOW_UPDATE"):
        execution._round_trip_packet(
            "wrong-type",
            adapter_conformance.MessageType.FLOW_UPDATE,
            Metadata,
        )

    monkeypatch.setattr(
        adapter_conformance.NnrpPacket,
        "unpack",
        lambda _payload: Packet(adapter_conformance.MessageType.FLOW_UPDATE, b"drift"),
    )
    with pytest.raises(ValueError, match="did not preserve the frozen wire bytes"):
        execution._round_trip_packet(
            "drift",
            adapter_conformance.MessageType.FLOW_UPDATE,
            Metadata,
        )


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"header_hex": _FROZEN_PARAMETERS["l0.header.fixed_shape.golden"]["header_hex"], "extra": "00"},
    ],
)
def test_frozen_golden_case_rejects_missing_or_extra_parameters(parameters: dict[str, str]) -> None:
    report = build_adapter_case_results_report(
        {
            "protocol_version": "nnrp-1-preview4",
            "cases": [{"id": "l0.header.fixed_shape.golden", "parameters": parameters}],
        }
    )

    assert report["results"][0]["outcome"] == "fail"
    assert "parameters must contain exactly" in report["results"][0]["message"]


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


@pytest.mark.parametrize(
    ("frozen_parameters", "message"),
    [
        ({}, "missing parameters"),
        ({"l0.frame_submit.metadata.golden": {}}, "missing frozen metadata_hex"),
        (
            {"l0.frame_submit.metadata.golden": {"metadata_hex": "not-hex"}},
            "invalid frozen metadata_hex",
        ),
    ],
)
def test_frozen_cross_case_parameter_lookup_rejects_missing_or_invalid_values(
    frozen_parameters: dict[str, object],
    message: str,
) -> None:
    execution = adapter_conformance._AdapterCaseExecution(
        {"id": "l1.frame_submit.message.parse_emit"},
        adapter_conformance._AdapterSmokeBackend(),
        frozen_parameters,
    )

    with pytest.raises(ValueError, match=message):
        execution._frozen_case_hex("l0.frame_submit.metadata.golden", "metadata_hex")


def test_adapter_parameter_helpers_preserve_string_payload_and_reject_unknown_smoke_transport() -> None:
    execution = adapter_conformance._AdapterCaseExecution(
        {"id": "l1.frame_submit.tensor.inline", "parameters": {"payload": "token"}},
        adapter_conformance._AdapterSmokeBackend(),
        {},
    )

    assert execution._payload_parameter("payload", b"default") == b"token"
    with pytest.raises(ValueError, match="unsupported native session-smoke transport"):
        with adapter_conformance._open_native_transport_session("udp"):
            pass


def test_result_payload_size_uses_raw_body_for_tensor_only_results() -> None:
    class Metadata:
        payload_kind_bitmap = adapter_conformance.PayloadKind.TENSOR

    class Result:
        body = b"tensor"
        metadata = Metadata()

    assert adapter_conformance._result_payload_size(Result()) == len(b"tensor")


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
    assert (
        adapter_conformance._resolve_evidence_dir(
            {"artifacts": {"evidence_dir": "evidence"}},
            base_dir=tmp_path,
        )
        == tmp_path / "evidence"
    )


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
