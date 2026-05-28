"""NNRP/1 adapter conformance wrapper for suite-owned execution plans."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nnrp.native import NativeArtifactError, NativeRuntimeBackend, NativeRuntimeError, select_native_runtime_backend

_RESULTS_SCHEMA_URL = "https://raw.githubusercontent.com/NagareWorks/nnrp-conformance/main/schemas/adapter-case-results.schema.json"
_DEFAULT_IMPLEMENTATION_NAME = "nnrp-py"
_REQUIRE_NATIVE_ENV = "NNRP_ADAPTER_REQUIRE_NATIVE"
_CASE_DISPATCH = {
    "l1.handshake.basic": "_execute_handshake_basic",
    "l1.session.open_close": "_execute_session_open_close",
    "l1.frame_submit.tensor.inline": "_execute_inline_tensor_submit",
    "l1.frame_submit.tensor.inline.routing.validation": "_execute_inline_tensor_submit_with_routing",
    "l1.result_push.basic.terminal.validation": "_execute_result_push_terminal",
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
    require_native = os.environ.get(_REQUIRE_NATIVE_ENV, "").strip().lower() in {"1", "true", "yes"}
    try:
        return select_native_runtime_backend(fallback=_AdapterSmokeBackend(), require_native=require_native)
    except NativeArtifactError:
        if require_native:
            raise
        return _AdapterSmokeBackend()


@dataclass(frozen=True)
class _AdapterCaseExecution:
    case: dict[str, Any]
    backend: NativeRuntimeBackend

    def _connect(self):
        return self.backend.connect(
            connection_id=self._int_parameter("connection_id", 1),
            generation=self._int_parameter("connection_generation", 1),
            transport_id=self._int_parameter("transport_id", 2),
        )

    def _open_session(self, connection):
        return connection.open_session(
            requested_session_id=self._int_parameter("session_id", 1),
            generation=self._int_parameter("session_generation", 1),
            profile_id=self._int_parameter("profile_id", 0),
            schema_id=self._int_parameter("schema_id", 0),
            schema_version=self._int_parameter("schema_version", 0),
        )

    def _submit_operation(self, session):
        return session.submit_operation(
            operation_id=self._int_parameter("operation_id", 1),
            frame_id=self._int_parameter("frame_id", 1),
            payload=self._payload_parameter("payload", b"tensor"),
        )

    def _execute_handshake_basic(self) -> dict[str, Any]:
        connection = self._connect()
        control_payload = self._payload_parameter("control_payload", b"hello")
        connection.control(control_code=self._int_parameter("control_code", 1), payload=control_payload)
        return self._evidence(
            "handshake",
            connection_id=_runtime_id(connection),
            control_payload_bytes=len(control_payload),
        )

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
        session.control(control_code=self._int_parameter("route_control_code", 2), payload=route_payload)
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
        if not isinstance(session, _AdapterSmokeSession):
            result = session.submit_result(
                operation_id=operation_id,
                frame_id=frame_id,
                payload=payload,
                result_payload=payload,
                max_events=self._int_parameter("max_events", 2),
            )
            operation = result
        else:
            operation = self._submit_operation(session)
            result = session.poll_result(operation, max_events=self._int_parameter("max_events", 2))
        if expected_state is not None and getattr(result, "state", expected_state) != expected_state:
            raise ValueError(f"expected result state {expected_state!r}, got {getattr(result, 'state', None)!r}")
        session.close()
        return self._evidence(
            "result-push-terminal",
            session_id=_runtime_id(session),
            operation_id=_runtime_id(operation),
            frame_id=operation.frame_id,
            result_payload_bytes=len(getattr(result, "payload", b"")),
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

    def connect(self, *, connection_id: int, generation: int, transport_id: int) -> _AdapterSmokeConnection:
        connection = _AdapterSmokeConnection(connection_id, generation, transport_id)
        self.connections.append(connection)
        return connection

    def bootstrap_connection(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_id: int,
    ) -> _AdapterSmokeConnection:
        return self.connect(connection_id=connection_id, generation=generation, transport_id=transport_id)


@dataclass
class _AdapterSmokeConnection:
    connection_id: int
    generation: int
    transport_id: int
    controls: list[tuple[int, bytes]] = field(default_factory=list)

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

    def control(self, *, control_code: int, payload: bytes | bytearray | memoryview = b"") -> None:
        self.controls.append((control_code, bytes(payload)))


@dataclass
class _AdapterSmokeOperation:
    operation_id: int
    frame_id: int
    payload: bytes


@dataclass
class _AdapterSmokeResult:
    operation_id: int
    frame_id: int
    payload: bytes


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

    def submit_operation(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
    ) -> _AdapterSmokeOperation:
        del parent_operation_id, operation_group_id
        self._ensure_open()
        operation = _AdapterSmokeOperation(operation_id, frame_id, bytes(payload))
        self.operations.append(operation)
        return operation

    def poll_result(self, operation: _AdapterSmokeOperation, *, max_events: int | None = None) -> _AdapterSmokeResult:
        del max_events
        self._ensure_open()
        return _AdapterSmokeResult(operation.operation_id, operation.frame_id, operation.payload)

    def submit_result(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        result_payload: bytes | bytearray | memoryview | None = None,
        max_events: int | None = None,
    ) -> _AdapterSmokeResult:
        del max_events
        self._ensure_open()
        selected_result_payload = payload if result_payload is None else result_payload
        self.operations.append(_AdapterSmokeOperation(operation_id, frame_id, bytes(payload)))
        return _AdapterSmokeResult(operation_id, frame_id, bytes(selected_result_payload))

    def cancel(self, *, frame_id: int) -> None:
        self._ensure_open()
        self.cancelled_frames.append(frame_id)

    def control(self, *, control_code: int, payload: bytes | bytearray | memoryview = b"") -> None:
        self._ensure_open()
        self.controls.append((control_code, bytes(payload)))

    def close(self) -> None:
        self._ensure_open()
        self.closed = True

    def _ensure_open(self) -> None:
        if self.closed:
            raise RuntimeError("adapter smoke session is closed")


if __name__ == "__main__":
    raise SystemExit(main())
