"""Live Python target for the external preview4 wire-conformance runner."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nnrp.client.native import NativeClientOptions, NativeClientProviderRoute, connect_native_client_connection
from nnrp.client.transport import SubmitIdentity, SubmitPolicy, SubmitRequest, TokenChunk, TokenSubmitInput
from nnrp.core import (
    BudgetPolicy,
    MessageType,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    TransportPolicy,
)
from nnrp.native import (
    NativeRuntimeError,
    NativeRuntimeServerSession,
    NativeTransportClientSecurity,
    NativeTransportServerSecurity,
    NativeWouldBlockError,
)
from nnrp.runtime import (
    CacheMissMetadata,
    CacheMissReason,
    CacheReferenceMetadata,
    NativeRuntimeEvent,
    PartialResultMetadata,
    PressureMetadata,
    ProgressMetadata,
    ResultDropReasonCode,
    ResultDropReasonMetadata,
    RuntimeRole,
    SchedulingMetadata,
    TraceContextMetadata,
)
from nnrp.server.native import (
    NativeServerAcceptOptions,
    NativeServerBootstrapOptions,
    NativeServerProviderRoute,
    listen_native_server,
)

_APPLICATION_ENDPOINT = "nnrp://wire-conformance.local"
_REQUEST_BODY = b"wire-external-request"
_RESPONSE_BODY = b"wire-external-result"
_TRACE_BODY = b"trace"
_PROGRESS_BODY = b"stage"
_PARTIAL_BODY = b"partial"
_DEFAULT_TIMEOUT_SECONDS = 10.0


def _token_submit_request(operation_id: int, frame_id: int, payload: bytes) -> SubmitRequest:
    return SubmitRequest.token(
        TokenSubmitInput(
            identity=SubmitIdentity(operation_id=operation_id, frame_id=frame_id),
            policy=SubmitPolicy(),
            chunks=(TokenChunk(payload),),
        )
    )


@dataclass(frozen=True, slots=True)
class LiveWireTransport:
    name: str
    endpoint: str
    server_security: NativeTransportServerSecurity | None = None
    client_security: NativeTransportClientSecurity | None = None


@dataclass(frozen=True, slots=True)
class LiveWireScenario:
    id: str
    mode: str
    transport: LiveWireTransport


def run_live_wire_target(
    plan_path: Path,
    target_path: Path,
    *,
    mode: str,
    ready_path: Path | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    plan = _read_json_object(plan_path, description="wire execution plan")
    target = _read_json_object(target_path, description="wire target manifest")
    scenarios = _live_scenarios(plan, target, target_path=target_path, mode=mode)
    if mode in {"suite_as_client", "suite_as_proxy"}:
        _run_server_scenarios(scenarios, ready_path=ready_path, timeout_seconds=timeout_seconds)
        return
    if mode == "suite_as_server":
        if ready_path is not None:
            _write_ready_file(ready_path)
        for scenario in scenarios:
            _run_progress_client(scenario, timeout_seconds=timeout_seconds)
        return
    raise ValueError(f"unsupported wire conformance mode: {mode}")


def _run_server_scenarios(
    scenarios: Sequence[LiveWireScenario],
    *,
    ready_path: Path | None,
    timeout_seconds: float,
) -> None:
    scenario_groups = _group_server_scenarios(scenarios)
    ready_events = [threading.Event() for _ in scenario_groups]
    errors: list[BaseException] = []
    error_lock = threading.Lock()

    def worker(group: Sequence[LiveWireScenario], ready_event: threading.Event) -> None:
        try:
            _run_server_scenario_group(group, ready_event=ready_event, timeout_seconds=timeout_seconds)
        except BaseException as error:
            with error_lock:
                scenario_ids = ", ".join(scenario.id for scenario in group)
                errors.append(RuntimeError(f"live wire scenario group failed: {scenario_ids}"))
                errors[-1].__cause__ = error
            ready_event.set()

    threads = [
        threading.Thread(
            target=worker,
            args=(group, ready_event),
            name=_server_thread_name(group),
            daemon=False,
        )
        for group, ready_event in zip(scenario_groups, ready_events, strict=True)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_seconds
    for ready_event in ready_events:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not ready_event.wait(remaining):
            raise TimeoutError("wire target listeners did not become ready before the deadline")
    if errors:
        raise errors[0]
    if ready_path is not None:
        _write_ready_file(ready_path)
    deadline = time.monotonic() + timeout_seconds * len(scenarios)
    for thread in threads:
        remaining = deadline - time.monotonic()
        thread.join(max(0.0, remaining))
        if thread.is_alive():
            raise TimeoutError(f"wire target scenario did not complete: {thread.name}")
    if errors:
        raise errors[0]


def _group_server_scenarios(scenarios: Sequence[LiveWireScenario]) -> list[tuple[LiveWireScenario, ...]]:
    groups: list[tuple[LiveWireTransport, list[LiveWireScenario]]] = []
    for scenario in scenarios:
        for transport, group in groups:
            if scenario.transport == transport:
                group.append(scenario)
                break
        else:
            groups.append((scenario.transport, [scenario]))
    return [tuple(group) for _transport, group in groups]


def _server_thread_name(group: Sequence[LiveWireScenario]) -> str:
    first = group[0]
    return f"wire-{first.transport.name}-{first.id}"


def _run_server_scenario_group(
    scenarios: Sequence[LiveWireScenario],
    *,
    ready_event: threading.Event,
    timeout_seconds: float,
) -> None:
    if not scenarios:
        raise ValueError("live wire server scenario group cannot be empty")
    transport = scenarios[0].transport
    if any(scenario.transport != transport for scenario in scenarios[1:]):
        raise ValueError("live wire server scenario group must share one transport endpoint")
    with listen_native_server(
        NativeServerBootstrapOptions(
            endpoint=_APPLICATION_ENDPOINT,
            provider_routes={
                transport.name: NativeServerProviderRoute(
                    provider_endpoint=transport.endpoint,
                    security=transport.server_security,
                )
            },
            transport_policy=TransportPolicy[f"FORCE_{transport.name.upper()}"],
        )
    ) as server:
        ready_event.set()
        for scenario in scenarios:
            session = asyncio.run(
                server.accept(NativeServerAcceptOptions(timeout_ms=max(1, int(timeout_seconds * 1000))))
            )
            _dispatch_server_scenario(scenario, session, timeout_seconds=timeout_seconds)


def _dispatch_server_scenario(
    scenario: LiveWireScenario,
    session: Any,
    *,
    timeout_seconds: float,
) -> None:
    if scenario.id in {
        "wire.control.cancel-abort.client",
        "wire.control.cancel-abort.ipc-client",
    }:
        _handle_cancel_server(session, timeout_seconds=timeout_seconds)
    elif scenario.id == "wire.control.deadline-before-submit.client":
        _handle_deadline_before_submit_server(session, timeout_seconds=timeout_seconds)
    elif scenario.id == "wire.control.priority-deadline.proxy":
        _handle_priority_server(session, timeout_seconds=timeout_seconds)
    elif scenario.id == "wire.control.capability-route-cache.client":
        _handle_cache_server(session, timeout_seconds=timeout_seconds)
    else:
        raise ValueError(f"unsupported live server scenario: {scenario.id}")


def _handle_cancel_server(session: Any, *, timeout_seconds: float) -> None:
    operation = asyncio.run(session.receive_submit(timeout=timeout_seconds))
    _await_server_runtime_frames(session, [MessageType.CANCEL], timeout_seconds=timeout_seconds)
    session.send_trace_context(
        TraceContextMetadata(
            trace_id=0x1234,
            span_id=0x5678,
            parent_span_id=0,
            stage_code=1,
            flags=0,
            body_bytes=len(_TRACE_BODY),
        ),
        _TRACE_BODY,
    )
    asyncio.run(operation.send_result_drop(_drop_reason(operation.operation_id)))
    _finish_peer_close(session, timeout_seconds=timeout_seconds)


def _handle_deadline_before_submit_server(
    session: Any,
    *,
    timeout_seconds: float,
) -> None:
    deadline_event = asyncio.run(session.next_event(timeout=timeout_seconds))
    deadline = deadline_event.as_runtime()
    if deadline is None or deadline.header.message_type is not MessageType.DEADLINE:
        raise RuntimeError("deadline-before-submit target expected DEADLINE before FRAME_SUBMIT")
    metadata = deadline.metadata.value
    if not isinstance(metadata, SchedulingMetadata):
        raise RuntimeError("deadline-before-submit target expected scheduling metadata")
    submit_event = asyncio.run(session.next_event(timeout=timeout_seconds))
    operation = submit_event.as_submit()
    if operation is None:
        raise RuntimeError("deadline-before-submit target expected FRAME_SUBMIT after DEADLINE")
    if operation.operation_id != metadata.operation_id or operation.frame_id != deadline.header.frame_id:
        raise RuntimeError("deadline-before-submit target received mismatched submit correlation")
    asyncio.run(operation.send_result(_canonical_result(), _RESPONSE_BODY))
    _finish_peer_close(session, timeout_seconds=timeout_seconds)


def _handle_priority_server(session: Any, *, timeout_seconds: float) -> None:
    operation = asyncio.run(session.receive_submit(timeout=timeout_seconds))
    _await_server_runtime_frames(
        session,
        [MessageType.PRIORITY_UPDATE, MessageType.EXPIRE_AT],
        timeout_seconds=timeout_seconds,
    )
    asyncio.run(operation.send_result_drop(_drop_reason(operation.operation_id)))
    _finish_peer_close(session, timeout_seconds=timeout_seconds)


def _handle_cache_server(session: Any, *, timeout_seconds: float) -> None:
    operation = asyncio.run(session.receive_submit(timeout=timeout_seconds))
    frames = _await_server_runtime_frames(
        session,
        [MessageType.CAPABILITY_NEGOTIATION, MessageType.ROUTE_HINT, MessageType.CACHE_REFERENCE],
        timeout_seconds=timeout_seconds,
    )
    cache_reference = frames[-1].metadata.value
    if not isinstance(cache_reference, CacheReferenceMetadata):
        raise RuntimeError("CACHE_REFERENCE event did not contain CacheReferenceMetadata")
    session.report_cache_miss(
        CacheMissMetadata(
            cache_namespace=cache_reference.cache_namespace,
            cache_key_hi=cache_reference.cache_key_hi,
            cache_key_lo=cache_reference.cache_key_lo,
            miss_reason=CacheMissReason.NOT_FOUND,
            profile_id=cache_reference.profile_id,
            diagnostic_bytes=0,
        )
    )
    asyncio.run(operation.send_result(_canonical_result(), _RESPONSE_BODY))
    _finish_peer_close(session, timeout_seconds=timeout_seconds)


def _run_progress_client(scenario: LiveWireScenario, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    with _open_connection(scenario.transport, deadline=deadline) as connection:
        session = asyncio.run(connection.open_session())
        operation = session.submit_operation(_token_submit_request(301, 1, _REQUEST_BODY))
        frames = _await_runtime_frames(
            session,
            [MessageType.PROGRESS, MessageType.CREDIT_UPDATE, MessageType.PARTIAL_RESULT],
            timeout_seconds=max(0.001, deadline - time.monotonic()),
        )
        _validate_progress_frames(frames)
        result = _poll_result(session, operation, deadline=deadline)
        runtime_event = result.event.as_runtime()
        if runtime_event is None:
            raise RuntimeError("wire target expected a wire RESULT_PUSH terminal event")
        if runtime_event.tail.body != _RESPONSE_BODY:
            raise RuntimeError(f"wire target expected canonical result body, got {runtime_event.tail.body!r}")
        connection.close()


@contextmanager
def _open_connection(transport: LiveWireTransport, *, deadline: float) -> Any:
    last_error: NativeRuntimeError | None = None
    while time.monotonic() < deadline:
        context = connect_native_client_connection(
            NativeClientOptions(
                endpoint=_APPLICATION_ENDPOINT,
                provider_routes={
                    transport.name: NativeClientProviderRoute(
                        provider_endpoint=transport.endpoint,
                        security=transport.client_security,
                    )
                },
                transport_policy=TransportPolicy[f"FORCE_{transport.name.upper()}"],
            )
        )
        try:
            connection = context.__enter__()
        except NativeRuntimeError as error:
            last_error = error
            time.sleep(0.025)
            continue
        try:
            yield connection
        except BaseException as error:
            context.__exit__(type(error), error, error.__traceback__)
            raise
        else:
            context.__exit__(None, None, None)
        return
    raise TimeoutError(f"wire target could not connect to {transport.endpoint}") from last_error


def _await_runtime_frames(
    session: Any,
    expected_types: Sequence[MessageType],
    *,
    timeout_seconds: float,
) -> list[NativeRuntimeEvent]:
    deadline = time.monotonic() + timeout_seconds
    observed: list[NativeRuntimeEvent] = []
    while len(observed) < len(expected_types) and time.monotonic() < deadline:
        frames = session.poll_runtime_frames(max_events=1, timeout_ms=25)
        if not frames:
            time.sleep(0.005)
            continue
        observed.extend(frames)
    observed_types = [frame.header.message_type for frame in observed]
    if observed_types != list(expected_types):
        names = ", ".join(frame.name for frame in observed_types)
        expected = ", ".join(frame.name for frame in expected_types)
        raise RuntimeError(f"wire target expected runtime frames [{expected}], got [{names}]")
    return observed


def _await_server_runtime_frames(
    session: Any,
    expected_types: Sequence[MessageType],
    *,
    timeout_seconds: float,
) -> list[NativeRuntimeEvent]:
    deadline = time.monotonic() + timeout_seconds
    observed: list[NativeRuntimeEvent] = []
    while len(observed) < len(expected_types):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("wire target did not receive the expected runtime frames before the deadline")
        event = asyncio.run(session.next_event(timeout=remaining))
        runtime = event.as_runtime()
        if runtime is not None:
            observed.append(runtime)
    observed_types = [frame.header.message_type for frame in observed]
    if observed_types != list(expected_types):
        names = ", ".join(frame.name for frame in observed_types)
        expected = ", ".join(frame.name for frame in expected_types)
        raise RuntimeError(f"wire target expected runtime frames [{expected}], got [{names}]")
    return observed


def _poll_result(session: Any, operation: Any, *, deadline: float) -> Any:
    last_error: NativeWouldBlockError | None = None
    while time.monotonic() < deadline:
        try:
            return session.poll_result(operation, max_events=16, timeout_ms=25)
        except NativeWouldBlockError as error:
            last_error = error
            time.sleep(0.005)
    raise TimeoutError("wire target did not receive a result before the deadline") from last_error


def _await_peer_close(session: NativeRuntimeServerSession, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        events = session.poll_events(max_events=16, timeout_ms=25)
        if any(
            event.as_runtime() is not None and event.as_runtime().header.message_type is MessageType.SESSION_CLOSE
            for event in events
        ):
            return
    raise TimeoutError("wire target did not receive SESSION_CLOSE before the deadline")


def _finish_peer_close(session: NativeRuntimeServerSession, *, timeout_seconds: float) -> None:
    _await_peer_close(session, timeout_seconds=timeout_seconds)
    session.close()


def _validate_progress_frames(frames: Sequence[NativeRuntimeEvent]) -> None:
    progress, credit, partial = frames
    if progress.metadata.value != ProgressMetadata(301, 1, 1, 2_500, 0, len(_PROGRESS_BODY)):
        raise RuntimeError("wire target received non-canonical PROGRESS metadata")
    if progress.tail.body != _PROGRESS_BODY:
        raise RuntimeError("wire target received non-canonical PROGRESS body")
    if credit.metadata.value != PressureMetadata(1, 1, 0, 0, 0, 0):
        raise RuntimeError("wire target received non-canonical CREDIT_UPDATE metadata")
    if partial.metadata.value != PartialResultMetadata(301, 1, 0, 0, len(_PARTIAL_BODY), 0):
        raise RuntimeError("wire target received non-canonical PARTIAL_RESULT metadata")
    if partial.tail.body != _PARTIAL_BODY:
        raise RuntimeError("wire target received non-canonical PARTIAL_RESULT body")


def _drop_reason(operation_id: int) -> ResultDropReasonMetadata:
    return ResultDropReasonMetadata(
        operation_id=operation_id,
        result_sequence=1,
        drop_reason_code=ResultDropReasonCode.DEADLINE_EXPIRED,
        source_role=RuntimeRole.SERVER,
        flags=0,
        diagnostic_bytes=0,
    )


def _canonical_result() -> ResultPushMetadata:
    return ResultPushMetadata(
        status_code=200,
        result_flags=ResultFlags.NONE,
        section_count=0,
        tile_count=0,
        active_profile_id=2,
        reserved0=0,
        inference_ms=1,
        queue_ms=0,
        server_total_ms=1,
        reserved1=0,
        tile_base_id=0,
        tile_index_bytes=0,
        result_class=ResultClass.COMPLETE,
        applied_budget_policy=BudgetPolicy.NONE,
        reused_frame_id=0,
        covered_tile_count=0,
        dropped_tile_count=0,
        payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
        payload_frame_count=1,
    )


def _live_scenarios(
    plan: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    target_path: Path,
    mode: str,
) -> list[LiveWireScenario]:
    transports = _target_transports(target, target_path=target_path)
    raw_scenarios = plan.get("scenarios")
    if not isinstance(raw_scenarios, Sequence) or isinstance(raw_scenarios, str):
        raise ValueError("wire execution plan must contain scenarios")
    scenarios: list[LiveWireScenario] = []
    for raw in raw_scenarios:
        if not isinstance(raw, Mapping):
            raise ValueError("wire execution plan scenarios must be objects")
        if raw.get("mode") != mode:
            continue
        scenario_id = _required_string(raw, "id")
        transport_name = _required_string(raw, "transport")
        try:
            transport = transports[transport_name]
        except KeyError as error:
            raise ValueError(f"wire target does not declare transport: {transport_name}") from error
        scenarios.append(LiveWireScenario(scenario_id, mode, transport))
    if not scenarios:
        raise ValueError(f"wire execution plan contains no scenarios for mode: {mode}")
    return scenarios


def _target_transports(
    target: Mapping[str, Any],
    *,
    target_path: Path,
) -> dict[str, LiveWireTransport]:
    wire = target.get("wire_conformance")
    if not isinstance(wire, Mapping):
        raise ValueError("wire target manifest must contain wire_conformance")
    raw_transports = wire.get("transports")
    if not isinstance(raw_transports, Sequence) or isinstance(raw_transports, str):
        raise ValueError("wire target manifest must contain transports")
    transports: dict[str, LiveWireTransport] = {}
    for raw in raw_transports:
        if not isinstance(raw, Mapping):
            raise ValueError("wire target transports must be objects")
        name = _required_string(raw, "name")
        if name in transports:
            raise ValueError(f"wire target contains duplicate transport: {name}")
        endpoint = _provider_endpoint(name, _required_string(raw, "endpoint"))
        security = raw.get("security")
        server_security = None
        client_security = None
        if security is not None:
            if not isinstance(security, Mapping):
                raise ValueError(f"wire target {name} security must be an object")
            base = target_path.parent
            certificate = _read_security_material(base, security, "certificate_der_path", transport=name)
            private_key = _read_security_material(
                base,
                security,
                "private_key_pkcs8_der_path",
                transport=name,
            )
            trust = _read_security_material(
                base,
                security,
                "trusted_certificate_der_path",
                transport=name,
            )
            server_security = NativeTransportServerSecurity(certificate, private_key)
            client_security = NativeTransportClientSecurity(_required_string(security, "server_name"), trust)
        transports[name] = LiveWireTransport(
            name,
            endpoint,
            server_security=server_security,
            client_security=client_security,
        )
    return transports


def _read_security_material(
    base: Path,
    security: Mapping[str, Any],
    field: str,
    *,
    transport: str,
) -> bytes:
    path = base / _required_string(security, field)
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise ValueError(f"wire target {transport} {field} was not found: {path}") from error
    except OSError as error:
        raise ValueError(f"wire target {transport} {field} could not be read: {path}") from error


def _provider_endpoint(transport: str, endpoint: str) -> str:
    if transport == "tcp" and "://" not in endpoint:
        return f"tcp://{endpoint}"
    if transport == "quic" and "://" not in endpoint:
        return f"quic://{endpoint}"
    return endpoint


def _read_json_object(path: Path, *, description: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"{description} was not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{description} is invalid JSON: {path}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{description} must be a JSON object: {path}")
    return document


def _required_string(document: Mapping[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _write_ready_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ready\n", encoding="ascii")
