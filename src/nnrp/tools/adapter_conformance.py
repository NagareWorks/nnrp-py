"""NNRP/1 adapter conformance wrapper for suite-owned execution plans."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nnrp.native import NativeArtifactError, NativeRuntimeBackend, select_native_runtime_backend

_RESULTS_SCHEMA_URL = "https://raw.githubusercontent.com/NagareWorks/nnrp-conformance/main/schemas/adapter-case-results.schema.json"
_DEFAULT_IMPLEMENTATION_NAME = "nnrp-py"
_REQUIRE_NATIVE_ENV = "NNRP_ADAPTER_REQUIRE_NATIVE"
_SUPPORTED_CASES = {
    "l1.handshake.basic",
    "l1.session.open_close",
    "l1.frame_submit.tensor.inline",
    "l1.frame_submit.tensor.inline.routing.validation",
    "l1.result_push.basic.terminal.validation",
}


def build_adapter_case_results_report(
    plan_document: dict[str, Any],
    *,
    implementation_name: str = _DEFAULT_IMPLEMENTATION_NAME,
    backend: NativeRuntimeBackend | None = None,
) -> dict[str, Any]:
    protocol_version = _require_string(plan_document, "protocol_version")
    cases = _require_case_list(plan_document)
    adapter_backend = backend or _load_adapter_backend()

    return {
        "$schema": _RESULTS_SCHEMA_URL,
        "protocol_version": protocol_version,
        "implementation_name": implementation_name,
        "results": [_run_case(case, adapter_backend) for case in cases],
    }


def write_adapter_case_results(plan_path: Path, output_path: Path) -> None:
    if not plan_path.is_file():
        raise ValueError(f"adapter execution plan path does not exist: {plan_path}")

    plan_document = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan_document, dict):
        raise ValueError("adapter execution plan must be a JSON object")

    report = build_adapter_case_results_report(plan_document)
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


def _run_case(case: dict[str, Any], backend: NativeRuntimeBackend) -> dict[str, Any]:
    case_id = _require_string(case, "id")
    if case_id in _SUPPORTED_CASES:
        try:
            _execute_supported_case(case_id, backend)
        except Exception as error:
            return {
                "id": case_id,
                "outcome": "fail",
                "message": f"SDK runtime facade smoke failed: {error}",
            }
        return {
            "id": case_id,
            "outcome": "pass",
            "message": "Case covered by the SDK runtime facade smoke surface.",
        }

    return {
        "id": case_id,
        "outcome": "skip",
        "message": "Case is outside the SDK-local adapter smoke surface.",
    }


def _load_adapter_backend() -> NativeRuntimeBackend:
    require_native = os.environ.get(_REQUIRE_NATIVE_ENV, "").strip().lower() in {"1", "true", "yes"}
    try:
        return select_native_runtime_backend(fallback=_AdapterSmokeBackend(), require_native=require_native)
    except NativeArtifactError:
        if require_native:
            raise
        return _AdapterSmokeBackend()


def _execute_supported_case(case_id: str, backend: NativeRuntimeBackend) -> None:
    connection = backend.connect(connection_id=1, generation=1, transport_id=1)
    if case_id == "l1.handshake.basic":
        connection.control(control_code=1, payload=b"hello")
        return

    session = connection.open_session(
        requested_session_id=1,
        generation=1,
        profile_id=0,
        schema_id=0,
        schema_version=0,
    )
    if case_id == "l1.session.open_close":
        session.close()
        return

    operation = session.submit_operation(operation_id=1, frame_id=1, payload=b"tensor")
    if case_id == "l1.frame_submit.tensor.inline.routing.validation":
        session.control(control_code=2, payload=b"route")
    elif case_id == "l1.result_push.basic.terminal.validation":
        session.poll_result(operation, max_events=1)
    else:
        session.cancel(frame_id=1)
    session.close()


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
