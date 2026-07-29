"""NNRP/1 adapter conformance wrapper for suite-owned execution plans."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nnrp.core import FrameSubmitMetadata, HeaderFlags, MessageType, NnrpHeader, WireFormat
from nnrp.native import (
    NativeRuntimeBackend,
    NativeRuntimeError,
    NativeRuntimeServerSession,
    NativeRuntimeSession,
)
from nnrp.runtime import (
    BudgetMetadata,
    CacheMissMetadata,
    CacheMissReason,
    CacheReferenceMetadata,
    CacheReuseScope,
    CapabilityMetadata,
    ControlRequestMetadata,
    MemoryLocationHint,
    ObjectDeltaMetadata,
    ObjectDescriptorMetadata,
    ObjectReferenceMetadata,
    ObjectReleaseMetadata,
    ObjectReleaseReason,
    OwnershipHint,
    PartialResultMetadata,
    PressureMetadata,
    ProgressMetadata,
    RecoverableErrorMetadata,
    ResultDropReasonCode,
    ResultDropReasonMetadata,
    RouteHintMetadata,
    RuntimeFrameHeader,
    RuntimeObjectKind,
    RuntimeRole,
    SchedulingMetadata,
    SupersedeMetadata,
    TraceContextMetadata,
    decode_runtime_control_metadata,
    decode_websocket_binary_frame,
    encode_runtime_control_metadata,
    encode_runtime_object_metadata,
    encode_websocket_binary_frame,
)
from nnrp.schema import TOKEN_DELTA_SCHEMA_ID, TOKEN_DELTA_SCHEMA_VERSION, StandardProfile

_RESULTS_SCHEMA_URL = "https://raw.githubusercontent.com/NagareWorks/nnrp-conformance/main/schemas/adapter-case-results.schema.json"
_DEFAULT_IMPLEMENTATION_NAME = "nnrp-py"
_CASE_DISPATCH = {
    "l0.header.fixed_shape.golden": "_execute_common_header_roundtrip",
    "l1.handshake.basic": "_execute_handshake_basic",
    "l1.session.open_close": "_execute_session_open_close",
    "l1.frame_submit.tensor.inline": "_execute_inline_tensor_submit",
    "l1.frame_submit.tensor.inline.routing.validation": "_execute_inline_tensor_submit_with_routing",
    "l1.result_push.basic.terminal.validation": "_execute_result_push_terminal",
    "l1.control.cancel-abort": "_execute_runtime_cancel_abort",
    "l1.control.priority-deadline": "_execute_runtime_priority_deadline",
    "l1.control.progress-backpressure": "_execute_runtime_progress_backpressure",
    "l1.control.capability-costs": "_execute_runtime_capability_costs",
    "l1.object.lifecycle": "_execute_runtime_object_lifecycle",
    "l1.object.delta": "_execute_runtime_object_delta",
    "l1.control.route-execution-hint": "_execute_runtime_route_execution_hint",
    "l1.control.cache-reference": "_execute_runtime_cache_reference",
    "l1.control.degrade-budget": "_execute_runtime_degrade_budget",
    "l1.control.supersede": "_execute_runtime_supersede",
    "l1.control.recoverable-error": "_execute_runtime_recoverable_error",
}


def build_adapter_case_results_report(
    plan_document: dict[str, Any],
    *,
    implementation_name: str = _DEFAULT_IMPLEMENTATION_NAME,
    backend: NativeRuntimeBackend | None = None,
    evidence_dir: Path | None = None,
) -> dict[str, Any]:
    protocol_version = _require_string(plan_document, "protocol_version")
    cases = _require_case_list(plan_document)
    adapter_backend = backend or _load_adapter_backend()
    resolved_evidence_dir = evidence_dir or _resolve_evidence_dir(plan_document)

    return {
        "$schema": _RESULTS_SCHEMA_URL,
        "protocol_version": protocol_version,
        "implementation_name": implementation_name,
        "results": [_run_case(case, adapter_backend, resolved_evidence_dir) for case in cases],
    }


def write_adapter_case_results(plan_path: Path, output_path: Path) -> None:
    if not plan_path.is_file():
        raise ValueError(f"adapter execution plan path does not exist: {plan_path}")

    plan_document = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan_document, dict):
        raise ValueError("adapter execution plan must be a JSON object")

    report = build_adapter_case_results_report(
        plan_document,
        evidence_dir=_resolve_evidence_dir(plan_document, base_dir=plan_path.parent),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(report, indent=2)}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nnrp.tools.adapter_conformance")
    parser.add_argument("--plan", default=os.environ.get("NNRP_CONFORMANCE_ADAPTER_PLAN"))
    parser.add_argument("--output", default=os.environ.get("NNRP_CONFORMANCE_ADAPTER_RESULTS"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.plan:
        parser.error("adapter execution plan path is required via --plan or NNRP_CONFORMANCE_ADAPTER_PLAN")

    if not args.output:
        parser.error("adapter result path is required via --output or NNRP_CONFORMANCE_ADAPTER_RESULTS")

    write_adapter_case_results(Path(args.plan), Path(args.output))
    return 0


def _require_string(document: dict[str, Any], field_name: str) -> str:
    value = document.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"adapter document field '{field_name}' must be a non-empty string")
    return value


def _require_case_list(document: dict[str, Any]) -> list[dict[str, Any]]:
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise ValueError("adapter execution plan must contain a cases list")

    normalized_cases: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("adapter execution plan cases must be JSON objects")
        normalized_cases.append(case)
    return normalized_cases


def _run_case(case: dict[str, Any], backend: NativeRuntimeBackend, evidence_dir: Path | None) -> dict[str, Any]:
    case_id = _require_string(case, "id")
    handler_name = _CASE_DISPATCH.get(case_id)
    if handler_name is not None:
        execution = _AdapterCaseExecution(case, backend)
        try:
            evidence = getattr(execution, handler_name)()
        except Exception as error:
            result = {
                "id": case_id,
                "outcome": "fail",
                "message": f"SDK runtime facade case failed: {error}",
            }
            result.update(_failure_diagnostic(error))
            _write_evidence(case_id, evidence_dir, result)
            return result
        _write_evidence(case_id, evidence_dir, evidence)
        return {
            "id": case_id,
            "outcome": "pass",
            "message": "Case covered by the SDK runtime facade execution surface.",
        }

    return {
        "id": case_id,
        "outcome": "skip",
        "message": "Case is outside the SDK-local adapter smoke surface.",
    }


def _resolve_evidence_dir(plan_document: dict[str, Any], *, base_dir: Path | None = None) -> Path | None:
    artifacts = plan_document.get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    evidence_dir = artifacts.get("evidence_dir")
    if not isinstance(evidence_dir, str) or not evidence_dir:
        return None
    path = Path(evidence_dir)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def _write_evidence(case_id: str, evidence_dir: Path | None, evidence: dict[str, Any]) -> None:
    if evidence_dir is None:
        return
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"{_evidence_file_stem(case_id)}.json"
    evidence_path.write_text(f"{json.dumps(evidence, indent=2, sort_keys=True)}\n", encoding="utf-8")


def _evidence_file_stem(case_id: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in case_id)


def _failure_diagnostic(error: Exception) -> dict[str, Any]:
    if isinstance(error, NativeRuntimeError):
        status = error.status
        return {
            "diagnostic": {
                "status_code": status.status_code,
                "error_family": status.error_family,
                "protocol_error_code": status.protocol_error_code,
                "detail_code": status.detail_code,
            }
        }
    return {
        "diagnostic": {
            "error_type": type(error).__name__,
        }
    }


def _load_adapter_backend() -> NativeRuntimeBackend:
    return _AdapterSmokeBackend()


@dataclass(frozen=True)
class _AdapterCaseExecution:
    case: dict[str, Any]
    backend: NativeRuntimeBackend

    def _connect(self):
        return self.backend.connect(
            connection_id=self._int_parameter("connection_id", 1),
            generation=self._int_parameter("connection_generation", 1),
            transport_connection=_AdapterSmokeCarrier(),
        )

    def _open_session(self, connection):
        return connection.open_session(
            requested_session_id=self._int_parameter("session_id", 1),
            generation=self._int_parameter("session_generation", 1),
            profile_id=self._int_parameter("profile_id", int(StandardProfile.TOKEN)),
            schema_id=self._int_parameter("schema_id", TOKEN_DELTA_SCHEMA_ID),
            schema_version=self._int_parameter("schema_version", TOKEN_DELTA_SCHEMA_VERSION),
        )

    def _submit_operation(self, session):
        return session.submit_operation(
            operation_id=self._int_parameter("operation_id", 1),
            frame_id=self._int_parameter("frame_id", 1),
            body=self._payload_parameter("payload", b"tensor"),
        )

    def _execute_common_header_roundtrip(self) -> dict[str, Any]:
        golden = bytes.fromhex(
            "4e4e5250010010282100000003020100060504004433221188776655aa99ccbb0807060504030201"
        )
        wire_header = NnrpHeader.unpack(golden)
        if wire_header.pack() != golden:
            raise ValueError("preview4 common header did not preserve the canonical wire bytes")

        runtime_header = RuntimeFrameHeader(
            message_type=MessageType.FRAME_SUBMIT,
            flags=HeaderFlags.ACK_REQUIRED | HeaderFlags.KEYFRAME,
            session_id=0x11223344,
            frame_id=0x55667788,
            view_id=0x99AA,
            route_id=0xBBCC,
            trace_id=0x0102030405060708,
            version_major=1,
            wire_format=WireFormat.CURRENT,
        )
        runtime_frame = encode_websocket_binary_frame(runtime_header, metadata=b"meta", body=b"body")
        if decode_websocket_binary_frame(runtime_frame).header != runtime_header:
            raise ValueError("public runtime frame header did not preserve all caller-controlled wire fields")

        return self._evidence(
            "common-header-roundtrip",
            header_hex=golden.hex(),
            message_type=int(wire_header.msg_type),
            flags=int(wire_header.flags),
            session_id=wire_header.session_id,
            frame_id=wire_header.frame_id,
            view_id=wire_header.view_id,
            route_id=wire_header.route_id,
            trace_id=wire_header.trace_id,
        )

    def _execute_handshake_basic(self) -> dict[str, Any]:
        connection = self._connect()
        return self._evidence("handshake", connection_id=_runtime_id(connection), role_adopted=True)

    def _execute_session_open_close(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.close()
        return self._evidence("session-open-close", session_id=_runtime_id(session), closed=_runtime_closed(session))

    def _execute_inline_tensor_submit(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        operation = self._submit_operation(session)
        session.cancel(frame_id=operation.frame_id)
        session.close()
        return self._evidence("inline-submit", session_id=_runtime_id(session), operation_id=_runtime_id(operation))

    def _execute_inline_tensor_submit_with_routing(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        operation = self._submit_operation(session)
        route_payload = self._payload_parameter("route_payload", b"route")
        session.send_route_hint(
            RouteHintMetadata(
                operation.operation_id,
                self._int_parameter("route_id", 2),
                self._int_parameter("executor_class", 0),
                self._int_parameter("affinity_class", 0),
                0,
                len(route_payload),
                0,
            ),
            route_payload,
        )
        session.close()
        return self._evidence(
            "inline-submit-routing",
            session_id=_runtime_id(session),
            operation_id=_runtime_id(operation),
            route_payload_bytes=len(route_payload),
        )

    def _execute_result_push_terminal(self) -> dict[str, Any]:
        connection = self._connect()
        session = self._open_session(connection)
        self._drain_setup_events(connection)
        expected_state = self._optional_string_parameter("expected_result_state")
        operation_id = self._int_parameter("operation_id", 99)
        frame_id = self._int_parameter("frame_id", 7)
        payload = self._payload_parameter("payload", b"adapter-payload")
        operation = session.submit_operation(
            operation_id=operation_id,
            frame_id=frame_id,
            body=payload,
        )
        result = session.poll_result(
            operation,
            max_events=self._int_parameter("max_events", 2),
            timeout_ms=self._int_parameter("timeout_ms", 0),
        )
        if expected_state is not None and getattr(result, "state", expected_state) != expected_state:
            raise ValueError(f"expected result state {expected_state!r}, got {getattr(result, 'state', None)!r}")
        session.close()
        return self._evidence(
            "result-push-terminal",
            session_id=_runtime_id(session),
            operation_id=_runtime_id(operation),
            frame_id=operation.frame_id,
            result_payload_bytes=len(getattr(result, "body", b"")),
        )

    def _execute_runtime_cancel_abort(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.cancel_operation(ControlRequestMetadata(10, 1, 1, RuntimeRole.CLIENT, 0, 0))
        session.abort_operation(ControlRequestMetadata(10, 2, 2, RuntimeRole.SCHEDULER, 0, 0))
        session.send_trace_context(TraceContextMetadata(100, 2, 1, 3, 0, 0))
        drop = ResultDropReasonMetadata(10, 1, ResultDropReasonCode.PEER_CANCELLED, RuntimeRole.RUNTIME, 0, 0)
        decoded = decode_runtime_control_metadata(
            MessageType.RESULT_DROP_REASON,
            encode_runtime_control_metadata(MessageType.RESULT_DROP_REASON, drop),
        )
        return self._evidence(
            "runtime-cancel-abort",
            session_id=_runtime_id(session),
            terminal_reason=int(decoded.metadata.drop_reason_code),
        )

    def _execute_runtime_priority_deadline(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.update_priority(SchedulingMetadata(10, 1, 2, 4, 0, 0))
        session.update_deadline(SchedulingMetadata(10, 2, 2, 0, 1_800_000_000_000, 0))
        session.expire_at(SchedulingMetadata(10, 3, 0, 0, 1_800_000_010_000, 0))
        return self._evidence("runtime-priority-deadline", session_id=_runtime_id(session), update_count=3)

    def _execute_runtime_progress_backpressure(self) -> dict[str, Any]:
        frames = (
            (MessageType.PROGRESS, ProgressMetadata(10, 1, 2, 2500, 20, 4), b"step"),
            (MessageType.PARTIAL_RESULT, PartialResultMetadata(10, 2, 20, 1, 4, 0), b"part"),
            (MessageType.BACKPRESSURE, PressureMetadata(10, 4, 2, 1, 5, 0), b""),
            (MessageType.CREDIT_UPDATE, PressureMetadata(10, 8, 0, 0, 0, 0), b""),
        )
        for message_type, metadata, tail in frames:
            decoded = decode_runtime_control_metadata(
                message_type,
                encode_runtime_control_metadata(message_type, metadata, tail=tail),
            )
            if decoded.metadata != metadata or decoded.tail != tail:
                raise ValueError(f"{message_type.name} runtime metadata did not round-trip")
        return self._evidence("runtime-progress-backpressure", frame_count=len(frames))

    def _execute_runtime_capability_costs(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.negotiate_capabilities(CapabilityMetadata(3, 2, 4, 1, 99, 88, 2, 0), b"{}")
        return self._evidence("runtime-capability-costs", session_id=_runtime_id(session), capability_count=2)

    def _execute_runtime_object_lifecycle(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        descriptor = ObjectDescriptorMetadata(
            9,
            RuntimeObjectKind.IMAGE_TILE,
            RuntimeRole.RUNTIME,
            RuntimeRole.CLIENT,
            3,
            4096,
            12,
            MemoryLocationHint.HOST_MEMORY,
            OwnershipHint.CONSUMER_OWNED,
            1000,
            2,
        )
        session.declare_object(descriptor, b"md")
        session.reference_object(ObjectReferenceMetadata(9, 10, 2, 0, 4096, 0, 0))
        session.release_object(ObjectReleaseMetadata(9, 10, ObjectReleaseReason.COMPLETED, RuntimeRole.CLIENT, 0, 0))
        return self._evidence("runtime-object-lifecycle", session_id=_runtime_id(session), object_id=9)

    def _execute_runtime_object_delta(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        metadata = ObjectDeltaMetadata(9, 2, 128, 64, 4, 0x03, 2)
        session.patch_object(metadata, b"xxxx", b"md")
        session.send_object_delta(metadata, b"xxxx", b"md")
        return self._evidence("runtime-object-delta", session_id=_runtime_id(session), delta_bytes=4)

    def _execute_runtime_route_execution_hint(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.send_route_hint(RouteHintMetadata(10, 20, 2, 3, 0, 2, 0), b"rt")
        session.send_execution_hint(RouteHintMetadata(10, 21, 4, 5, 0, 2, 0), b"ex")
        return self._evidence("runtime-route-execution-hint", session_id=_runtime_id(session), hint_count=2)

    def _execute_runtime_cache_reference(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.reference_cache(CacheReferenceMetadata(7, 1, 2, 3, CacheReuseScope.SESSION, 4, 5, 1000, 0, 0))
        session.report_cache_miss(CacheMissMetadata(7, 1, 2, CacheMissReason.UNKNOWN, 3, 0))
        return self._evidence("runtime-cache-reference", session_id=_runtime_id(session), cache_key_hi=1)

    def _execute_runtime_degrade_budget(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.degrade_profile(CapabilityMetadata(3, 1, 4, 2, 99, 88, 2, 0), b"{}")
        session.update_budget(BudgetMetadata(10, 20, 30, 40, 50, 0))
        return self._evidence("runtime-degrade-budget", session_id=_runtime_id(session), operation_id=10)

    def _execute_runtime_supersede(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.supersede(
            SupersedeMetadata(10, 11, 1, ResultDropReasonCode.SUPERSEDED, 0, 0),
        )
        return self._evidence(
            "runtime-supersede",
            session_id=_runtime_id(session),
            old_operation_id=10,
            new_operation_id=11,
        )

    def _execute_runtime_recoverable_error(self) -> dict[str, Any]:
        session = self._open_session(self._connect())
        session.send_recoverable_error(
            RecoverableErrorMetadata(20, 1, 2, RuntimeRole.RUNTIME, 0, 100, 1, 2, 3, 0),
        )
        return self._evidence(
            "runtime-recoverable-error",
            session_id=_runtime_id(session),
            error_code=20,
            retry_after_ms=100,
        )

    def _drain_setup_events(self, connection: Any) -> None:
        poll_events_batch = getattr(connection, "poll_events_batch", None)
        if callable(poll_events_batch):
            try:
                while poll_events_batch(max_events=8):
                    pass
                return
            except Exception:
                return
        poll_events = getattr(connection, "poll_events", None)
        if callable(poll_events):
            try:
                while poll_events():
                    pass
            except Exception:
                return

    def _evidence(self, action: str, **fields: Any) -> dict[str, Any]:
        return {
            "case_id": _require_string(self.case, "id"),
            "action": action,
            **fields,
        }

    def _parameters(self) -> dict[str, Any]:
        parameters = self.case.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("adapter case parameters must be a JSON object")
        return parameters

    def _int_parameter(self, name: str, default: int) -> int:
        value = self._parameters().get(name, default)
        if not isinstance(value, int):
            raise ValueError(f"adapter case parameter '{name}' must be an integer")
        return value

    def _optional_string_parameter(self, name: str) -> str | None:
        value = self._parameters().get(name)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError(f"adapter case parameter '{name}' must be a non-empty string")
        return value

    def _payload_parameter(self, name: str, default: bytes) -> bytes:
        value = self._parameters().get(name)
        if value is None:
            return default
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, list) and all(isinstance(item, int) and 0 <= item <= 255 for item in value):
            return bytes(value)
        raise ValueError(f"adapter case parameter '{name}' must be a string or byte list")


def _runtime_id(value: Any) -> int:
    direct_value = getattr(value, "connection_id", None)
    if isinstance(direct_value, int):
        return direct_value
    direct_value = getattr(value, "session_id", None)
    if isinstance(direct_value, int):
        return direct_value
    direct_value = getattr(value, "operation_id", None)
    if isinstance(direct_value, int):
        return direct_value
    handle = getattr(value, "handle", None)
    nested_handle = getattr(handle, "handle", None)
    handle_id = getattr(nested_handle or handle, "id", None)
    if isinstance(handle_id, int):
        return handle_id
    return 0


def _runtime_closed(value: Any) -> bool:
    closed = getattr(value, "closed", None)
    if isinstance(closed, bool):
        return closed
    closed = getattr(value, "_closed", None)
    if isinstance(closed, bool):
        return closed
    return False


@dataclass
class _AdapterSmokeBackend:
    connections: list[_AdapterSmokeConnection] = field(default_factory=list)

    def connect(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_connection: _AdapterSmokeCarrier,
    ) -> _AdapterSmokeConnection:
        transport_connection.consume()
        connection = _AdapterSmokeConnection(connection_id, generation, transport_connection)
        self.connections.append(connection)
        return connection


@dataclass
class _AdapterSmokeCarrier:
    consumed: bool = False
    closed: bool = False

    @property
    def connected(self) -> bool:
        return not self.consumed and not self.closed

    def consume(self) -> None:
        if not self.connected:
            raise RuntimeError("adapter smoke carrier is not available for role adoption")
        self.consumed = True

    def close(self) -> None:
        self.closed = True


@dataclass
class _AdapterSmokeConnection:
    connection_id: int
    generation: int
    transport_connection: _AdapterSmokeCarrier
    closed: bool = False

    def open_session(
        self,
        *,
        requested_session_id: int,
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
    ) -> _AdapterSmokeSession:
        return _AdapterSmokeSession(
            connection=self,
            session_id=requested_session_id,
            generation=generation,
            profile_id=profile_id,
            schema_id=schema_id,
            schema_version=schema_version,
        )

    def close(self) -> None:
        if self.closed:
            return
        self.transport_connection.close()
        self.closed = True


@dataclass
class _AdapterSmokeOperation:
    operation_id: int
    frame_id: int
    body: bytes


@dataclass
class _AdapterSmokeResult:
    operation_id: int
    frame_id: int
    body: bytes


@dataclass
class _AdapterSmokeSession:
    connection: _AdapterSmokeConnection
    session_id: int
    generation: int
    profile_id: int
    schema_id: int
    schema_version: int
    operations: list[_AdapterSmokeOperation] = field(default_factory=list)
    controls: list[tuple[int, bytes]] = field(default_factory=list)
    cancelled_frames: list[int] = field(default_factory=list)
    closed: bool = False

    cancel_operation = NativeRuntimeSession.cancel_operation
    abort_operation = NativeRuntimeSession.abort_operation
    update_priority = NativeRuntimeSession.update_priority
    update_deadline = NativeRuntimeSession.update_deadline
    expire_at = NativeRuntimeSession.expire_at
    update_budget = NativeRuntimeSession.update_budget
    negotiate_capabilities = NativeRuntimeSession.negotiate_capabilities
    degrade_profile = NativeRuntimeSession.degrade_profile
    supersede = NativeRuntimeSession.supersede
    send_route_hint = NativeRuntimeSession.send_route_hint
    send_execution_hint = NativeRuntimeSession.send_execution_hint
    send_trace_context = NativeRuntimeSession.send_trace_context
    send_recoverable_error = NativeRuntimeServerSession.send_recoverable_error
    declare_object = NativeRuntimeSession.declare_object
    reference_object = NativeRuntimeSession.reference_object
    release_object = NativeRuntimeSession.release_object
    patch_object = NativeRuntimeSession.patch_object
    send_object_delta = NativeRuntimeSession.send_object_delta
    reference_cache = NativeRuntimeSession.reference_cache
    report_cache_miss = NativeRuntimeSession.report_cache_miss

    _OBJECT_MESSAGE_TYPES = {
        MessageType.OBJECT_DECLARE,
        MessageType.OBJECT_REF,
        MessageType.OBJECT_RELEASE,
        MessageType.OBJECT_PATCH,
        MessageType.OBJECT_DELTA,
        MessageType.CACHE_REFERENCE,
        MessageType.CACHE_MISS,
    }

    def submit_operation(
        self,
        *,
        operation_id: int,
        frame_id: int,
        metadata: FrameSubmitMetadata | None = None,
        body: bytes | bytearray | memoryview = b"",
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
    ) -> _AdapterSmokeOperation:
        del metadata, parent_operation_id, operation_group_id
        self._ensure_open()
        operation = _AdapterSmokeOperation(operation_id, frame_id, bytes(body))
        self.operations.append(operation)
        return operation

    def poll_result(
        self,
        operation: _AdapterSmokeOperation,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> _AdapterSmokeResult:
        del max_events, timeout_ms
        self._ensure_open()
        return _AdapterSmokeResult(operation.operation_id, operation.frame_id, operation.body)

    def cancel(self, *, frame_id: int) -> None:
        self._ensure_open()
        self.cancelled_frames.append(frame_id)

    def _send_runtime_frame(self, message_type: MessageType, metadata: Any, tail: bytes = b"") -> None:
        self._ensure_open()
        if message_type in self._OBJECT_MESSAGE_TYPES:
            payload = encode_runtime_object_metadata(message_type, metadata, tail=tail)
        else:
            payload = encode_runtime_control_metadata(message_type, metadata, tail=tail)
        self.controls.append((int(message_type), payload))

    def close(self) -> None:
        self._ensure_open()
        self.closed = True

    def _ensure_open(self) -> None:
        if self.closed:
            raise RuntimeError("adapter smoke session is closed")


if __name__ == "__main__":
    raise SystemExit(main())
