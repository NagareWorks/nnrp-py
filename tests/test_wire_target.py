from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import nnrp.tools.wire_target as wire_target
from nnrp.client import SubmitRequest
from nnrp.core import MessageType
from nnrp.native import (
    FFI_STATUS_WOULD_BLOCK,
    NativeRuntimeError,
    NativeServerEvent,
    NativeStatus,
    NativeWouldBlockError,
)
from nnrp.runtime import (
    CacheReferenceMetadata,
    CapabilityMetadata,
    InFlightPolicy,
    NativeRuntimeEvent,
    OperationLifecycleEvent,
    OperationState,
    PartialResultMetadata,
    PressureMetadata,
    ProgressMetadata,
    ResultDropReasonCode,
    RuntimeEventMetadata,
    RuntimeEventMetadataKind,
    RuntimeEventTail,
    RuntimeFrameHeader,
    SchedulingMetadata,
    SessionCloseMetadata,
    SessionCloseReason,
)


def _runtime_event(
    message_type: MessageType,
    metadata_kind: RuntimeEventMetadataKind,
    metadata: object,
    *,
    body: bytes = b"",
) -> NativeRuntimeEvent:
    return NativeRuntimeEvent(
        RuntimeFrameHeader(message_type),
        RuntimeEventMetadata(metadata_kind, metadata),
        RuntimeEventTail.with_body(body) if body else RuntimeEventTail.none(),
    )


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.poll_events_batches: list[list[SimpleNamespace]] = []
        self.poll_frames_batches: list[list[SimpleNamespace]] = []
        self.poll_results: list[object] = []
        self.next_events: list[NativeServerEvent | SimpleNamespace] = []

    async def receive_submit(self, timeout: float | None) -> _FakeServerOperation:
        self.calls.append(("receive_submit", timeout))
        return _FakeServerOperation(self.calls)

    async def next_event(self, timeout: float | None) -> NativeServerEvent | SimpleNamespace:
        self.calls.append(("next_event", timeout))
        return self.next_events.pop(0)

    def send_trace_context(self, metadata, body=b"", *, operation_id=None) -> None:
        self.calls.append(("trace", (metadata, body, operation_id)))

    def report_cache_miss(self, metadata) -> None:
        self.calls.append(("cache_miss", metadata))

    def negotiate_capabilities(self, metadata, body=b"") -> None:
        self.calls.append(("capabilities", (metadata, body)))

    def submit_operation(self, request: SubmitRequest):
        self.calls.append(("submit", request))
        return SimpleNamespace(operation_id=request.operation_id)

    def poll_runtime_frames(self, *, max_events: int, timeout_ms: int):
        self.calls.append(("poll_frames", (max_events, timeout_ms)))
        return self.poll_frames_batches.pop(0) if self.poll_frames_batches else []

    def poll_result(self, operation, *, max_events: int, timeout_ms: int):
        self.calls.append(("poll_result", (operation, max_events, timeout_ms)))
        result = self.poll_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def poll_events(self, *, max_events: int, timeout_ms: int):
        self.calls.append(("poll_events", (max_events, timeout_ms)))
        return self.poll_events_batches.pop(0) if self.poll_events_batches else []

    def close(self) -> None:
        self.calls.append(("close", None))


class _FakeServerOperation:
    operation_id = 401
    frame_id = 1

    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls

    async def send_result(self, metadata, body: bytes = b"") -> None:
        del metadata
        self._calls.append(("result", body))

    async def send_result_drop(self, metadata, diagnostic: bytes = b"") -> None:
        del diagnostic
        self._calls.append(("drop", metadata))


def _scenario(
    scenario_id: str,
    mode: str = "suite_as_client",
    *,
    transport: str = "tcp",
    endpoint: str = "tcp://127.0.0.1:19091",
) -> wire_target.LiveWireScenario:
    return wire_target.LiveWireScenario(
        scenario_id,
        mode,
        wire_target.LiveWireTransport(transport, endpoint),
    )


def test_run_live_wire_target_dispatches_all_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_path = tmp_path / "plan.json"
    target_path = tmp_path / "target.json"
    plan_path.write_text("{}", encoding="utf-8")
    target_path.write_text("{}", encoding="utf-8")
    scenarios = [_scenario("wire.control.cancel-abort.client")]
    monkeypatch.setattr(wire_target, "_live_scenarios", lambda *args, **kwargs: scenarios)
    server_calls: list[tuple[object, object, float]] = []
    monkeypatch.setattr(
        wire_target,
        "_run_server_scenarios",
        lambda selected, *, ready_path, timeout_seconds: server_calls.append((selected, ready_path, timeout_seconds)),
    )

    ready_path = tmp_path / "ready"
    wire_target.run_live_wire_target(
        plan_path,
        target_path,
        mode="suite_as_client",
        ready_path=ready_path,
        timeout_seconds=2.0,
    )
    wire_target.run_live_wire_target(plan_path, target_path, mode="suite_as_proxy")
    assert server_calls == [(scenarios, ready_path, 2.0), (scenarios, None, 10.0)]

    progress_calls: list[wire_target.LiveWireScenario] = []
    monkeypatch.setattr(wire_target, "_run_progress_client", lambda scenario, **kwargs: progress_calls.append(scenario))
    wire_target.run_live_wire_target(plan_path, target_path, mode="suite_as_server", ready_path=ready_path)
    assert progress_calls == scenarios
    assert ready_path.read_text(encoding="ascii") == "ready\n"

    with pytest.raises(ValueError, match="must be positive"):
        wire_target.run_live_wire_target(plan_path, target_path, mode="suite_as_client", timeout_seconds=0)
    with pytest.raises(ValueError, match="unsupported wire conformance mode"):
        wire_target.run_live_wire_target(plan_path, target_path, mode="unknown")


def test_run_server_scenarios_coordinates_threads_and_reports_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = [_scenario("one"), _scenario("two")]

    completed_groups: list[tuple[str, ...]] = []

    def complete(group, ready_event, **_kwargs) -> None:
        completed_groups.append(tuple(scenario.id for scenario in group))
        ready_event.set()

    monkeypatch.setattr(wire_target, "_run_server_scenario_group", complete)
    ready = tmp_path / "ready"
    wire_target._run_server_scenarios(scenarios, ready_path=ready, timeout_seconds=1.0)
    assert ready.exists()
    assert completed_groups == [("one", "two")]

    def fail(_group, ready_event, **_kwargs) -> None:
        del ready_event
        raise ValueError("broken target")

    monkeypatch.setattr(wire_target, "_run_server_scenario_group", fail)
    with pytest.raises(RuntimeError, match="live wire scenario group failed") as captured:
        wire_target._run_server_scenarios([scenarios[0]], ready_path=None, timeout_seconds=1.0)
    assert isinstance(captured.value.__cause__, ValueError)


@pytest.mark.parametrize(
    ("scenario_id", "handler_name"),
    [
        ("wire.control.cancel-abort.client", "_handle_cancel_server"),
        ("wire.control.cancel-abort.ipc-client", "_handle_cancel_server"),
        ("wire.control.deadline-before-submit.client", "_handle_deadline_before_submit_server"),
        ("wire.control.priority-deadline.proxy", "_handle_priority_server"),
        ("wire.control.capability-route-cache.client", "_handle_cache_server"),
    ],
)
def test_dispatch_server_scenario_dispatches_typed_handlers(
    scenario_id: str,
    handler_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object()
    called: list[object] = []
    for name in (
        "_handle_cancel_server",
        "_handle_deadline_before_submit_server",
        "_handle_priority_server",
        "_handle_cache_server",
    ):
        monkeypatch.setattr(wire_target, name, lambda value, **kwargs: called.append(value))
    wire_target._dispatch_server_scenario(_scenario(scenario_id), session, timeout_seconds=1.0)
    assert called == [session]

    with pytest.raises(ValueError, match="unsupported live server scenario"):
        wire_target._dispatch_server_scenario(_scenario("unknown"), session, timeout_seconds=1.0)


def test_run_server_scenario_group_reuses_one_listener_for_shared_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = [object(), object()]
    accepted: list[object] = []
    listened: list[object] = []

    async def accept(options: object) -> object:
        accepted.append(options)
        return sessions[len(accepted) - 1]

    server = SimpleNamespace(accept=accept, close=lambda: None)

    @contextmanager
    def listen(options):
        listened.append(options)
        yield server

    dispatched: list[tuple[str, object]] = []
    monkeypatch.setattr(wire_target, "listen_native_server", listen)
    monkeypatch.setattr(
        wire_target,
        "_dispatch_server_scenario",
        lambda scenario, session, **kwargs: dispatched.append((scenario.id, session)),
    )
    ready = threading.Event()
    scenarios = (_scenario("one"), _scenario("two"))

    wire_target._run_server_scenario_group(scenarios, ready_event=ready, timeout_seconds=1.0)

    assert ready.is_set()
    assert len(listened) == 1
    route = listened[0].provider_routes["tcp"]
    assert route.provider_endpoint == "tcp://127.0.0.1:19091"
    assert listened[0].transport_policy.name == "FORCE_TCP"
    assert len(accepted) == 2
    assert dispatched == [("one", sessions[0]), ("two", sessions[1])]


def test_run_server_scenario_group_rejects_empty_or_mixed_endpoints() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        wire_target._run_server_scenario_group((), ready_event=threading.Event(), timeout_seconds=1.0)

    with pytest.raises(ValueError, match="share one transport endpoint"):
        wire_target._run_server_scenario_group(
            (
                _scenario("one"),
                _scenario("two", endpoint="tcp://127.0.0.1:19092"),
            ),
            ready_event=threading.Event(),
            timeout_seconds=1.0,
        )


def test_group_server_scenarios_separates_transport_endpoints() -> None:
    scenarios = [
        _scenario("tcp-one"),
        _scenario("ipc", transport="ipc", endpoint="npipe://nnrp-wire"),
        _scenario("tcp-two"),
        _scenario("tcp-other", endpoint="tcp://127.0.0.1:19092"),
    ]

    groups = wire_target._group_server_scenarios(scenarios)

    assert [[scenario.id for scenario in group] for group in groups] == [
        ["tcp-one", "tcp-two"],
        ["ipc"],
        ["tcp-other"],
    ]
    assert [wire_target._server_thread_name(group) for group in groups] == [
        "wire-tcp-tcp-one",
        "wire-ipc-ipc",
        "wire-tcp-tcp-other",
    ]


def test_server_handlers_exchange_typed_control_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    awaited: list[list[MessageType]] = []
    closed: list[object] = []
    lifecycles: list[tuple[object, int, OperationState]] = []
    cache_reference = CacheReferenceMetadata(
        cache_namespace=1,
        cache_key_hi=2,
        cache_key_lo=3,
        profile_id=4,
        reuse_scope=0,
        lease_id=5,
        producer_trace_id=6,
        expiration_hint_ms=7,
        metadata_bytes=0,
        flags=0,
    )
    capability = CapabilityMetadata(2, 2, 7, 3, 100, 200, 0, 1)

    def await_frames(_session, expected_types, **_kwargs):
        awaited.append(list(expected_types))
        if expected_types == [MessageType.CAPABILITY_NEGOTIATION]:
            return [
                _runtime_event(
                    MessageType.CAPABILITY_NEGOTIATION,
                    RuntimeEventMetadataKind.CAPABILITY,
                    capability,
                )
            ]
        if MessageType.CACHE_REFERENCE in expected_types:
            return [
                SimpleNamespace(metadata=None),
                _runtime_event(
                    MessageType.CACHE_REFERENCE,
                    RuntimeEventMetadataKind.CACHE_REFERENCE,
                    cache_reference,
                ),
            ]
        return []

    monkeypatch.setattr(wire_target, "_await_server_runtime_frames", await_frames)
    monkeypatch.setattr(
        wire_target,
        "_await_server_lifecycle",
        lambda value, operation_id, state, **kwargs: lifecycles.append((value, operation_id, state)),
    )
    monkeypatch.setattr(wire_target, "_finish_peer_close", lambda value, **kwargs: closed.append(value))

    wire_target._handle_cancel_server(session, timeout_seconds=1.0)
    wire_target._handle_priority_server(session, timeout_seconds=1.0)
    wire_target._handle_cache_server(session, timeout_seconds=1.0)

    assert awaited == [
        [MessageType.CANCEL],
        [MessageType.PRIORITY_UPDATE, MessageType.EXPIRE_AT],
        [MessageType.CAPABILITY_NEGOTIATION],
        [MessageType.ROUTE_HINT, MessageType.CACHE_REFERENCE],
    ]
    trace_calls = [payload for name, payload in session.calls if name == "trace"]
    assert len(trace_calls) == 1
    assert trace_calls[0][2] is None
    assert sum(name == "drop" for name, _ in session.calls) == 2
    drop_reasons = [metadata.drop_reason_code for name, metadata in session.calls if name == "drop"]
    assert drop_reasons == [ResultDropReasonCode.PEER_CANCELLED, ResultDropReasonCode.SUPERSEDED]
    assert any(name == "cache_miss" for name, _ in session.calls)
    capability_calls = [payload for name, payload in session.calls if name == "capabilities"]
    assert capability_calls == [
        (
            CapabilityMetadata(2, 1, 7, 3, 100, 200, 26, 1),
            b"\x18\x00control.capability_costs",
        )
    ]
    assert any(name == "result" for name, _ in session.calls)
    assert lifecycles == [
        (session, 401, OperationState.CANCELLED),
        (session, 401, OperationState.SUPERSEDED),
        (session, 401, OperationState.COMPLETED),
    ]
    assert closed == [session, session, session]


def test_deadline_before_submit_handler_requires_order_and_exact_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    lifecycles: list[tuple[int, OperationState]] = []
    monkeypatch.setattr(
        wire_target,
        "_await_server_lifecycle",
        lambda _session, operation_id, state, **_kwargs: lifecycles.append((operation_id, state)),
    )
    monkeypatch.setattr(wire_target, "_finish_peer_close", lambda *_args, **_kwargs: None)
    operation = _FakeServerOperation(session.calls)
    deadline = SchedulingMetadata(401, 1, 0, 0, 123_456, 0)
    runtime_event = _runtime_event(
        MessageType.DEADLINE,
        RuntimeEventMetadataKind.SCHEDULING,
        deadline,
    )
    runtime_event = replace(runtime_event, header=replace(runtime_event.header, frame_id=1))
    submit_event = SimpleNamespace(as_runtime=lambda: None, as_submit=lambda: operation)
    session.next_events = [NativeServerEvent.runtime(runtime_event), submit_event]

    wire_target._handle_deadline_before_submit_server(session, timeout_seconds=1.0)

    assert ("result", wire_target._RESPONSE_BODY) in session.calls
    assert lifecycles == [(401, OperationState.COMPLETED)]

    session.next_events = [submit_event]
    with pytest.raises(RuntimeError, match="expected DEADLINE before FRAME_SUBMIT"):
        wire_target._handle_deadline_before_submit_server(session, timeout_seconds=1.0)

    mismatched = replace(runtime_event, header=replace(runtime_event.header, frame_id=2))
    session.next_events = [NativeServerEvent.runtime(mismatched), submit_event]
    with pytest.raises(RuntimeError, match="mismatched submit correlation"):
        wire_target._handle_deadline_before_submit_server(session, timeout_seconds=1.0)

    wrong_metadata = _runtime_event(
        MessageType.DEADLINE,
        RuntimeEventMetadataKind.NONE,
        None,
    )
    session.next_events = [NativeServerEvent.runtime(wrong_metadata), submit_event]
    with pytest.raises(RuntimeError, match="expected scheduling metadata"):
        wire_target._handle_deadline_before_submit_server(session, timeout_seconds=1.0)

    session.next_events = [NativeServerEvent.runtime(runtime_event), SimpleNamespace(as_submit=lambda: None)]
    with pytest.raises(RuntimeError, match="expected FRAME_SUBMIT after DEADLINE"):
        wire_target._handle_deadline_before_submit_server(session, timeout_seconds=1.0)


def test_await_server_runtime_frames_ignores_lifecycle_and_checks_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    progress = _runtime_event(
        MessageType.PROGRESS,
        RuntimeEventMetadataKind.PROGRESS,
        ProgressMetadata(301, 1, 1, 2_500, 0, 0),
    )
    session.next_events = [
        SimpleNamespace(as_runtime=lambda: None),
        NativeServerEvent.runtime(progress),
    ]

    assert wire_target._await_server_runtime_frames(
        session,
        [MessageType.PROGRESS],
        timeout_seconds=1.0,
    ) == [progress]

    session.next_events = [NativeServerEvent.runtime(progress)]
    with pytest.raises(RuntimeError, match=r"expected runtime frames \[CANCEL\], got \[PROGRESS\]"):
        wire_target._await_server_runtime_frames(
            session,
            [MessageType.CANCEL],
            timeout_seconds=1.0,
        )

    session.next_events = [SimpleNamespace(as_runtime=lambda: None)]
    timestamps = iter((100.0, 100.25, 101.0))
    monkeypatch.setattr(wire_target, "time", SimpleNamespace(monotonic=lambda: next(timestamps)))
    with pytest.raises(TimeoutError, match="expected runtime frames before the deadline"):
        wire_target._await_server_runtime_frames(
            session,
            [MessageType.CANCEL],
            timeout_seconds=1.0,
        )
    assert ("next_event", 0.75) in session.calls


def test_await_server_lifecycle_requires_exact_operation_and_terminal_state() -> None:
    session = _FakeSession()
    session.next_events = [NativeServerEvent.lifecycle(OperationLifecycleEvent(42, OperationState.COMPLETED))]
    wire_target._await_server_lifecycle(
        session,
        42,
        OperationState.COMPLETED,
        timeout_seconds=1.0,
    )

    session.next_events = [NativeServerEvent.lifecycle(OperationLifecycleEvent(43, OperationState.COMPLETED))]
    with pytest.raises(RuntimeError, match="expected operation 42 lifecycle COMPLETED"):
        wire_target._await_server_lifecycle(
            session,
            42,
            OperationState.COMPLETED,
            timeout_seconds=1.0,
        )

    session.next_events = [
        NativeServerEvent.runtime(_runtime_event(MessageType.SESSION_CLOSE, RuntimeEventMetadataKind.NONE, None))
    ]
    with pytest.raises(RuntimeError, match="expected an operation lifecycle event"):
        wire_target._await_server_lifecycle(
            session,
            42,
            OperationState.COMPLETED,
            timeout_seconds=1.0,
        )


def test_progress_client_validates_frames_and_terminal_result(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()

    async def open_session():
        return session

    connection = SimpleNamespace(open_session=open_session, close=lambda: None)

    @contextmanager
    def open_connection(*args, **kwargs):
        yield connection

    frames = [
        _runtime_event(
            MessageType.PROGRESS,
            RuntimeEventMetadataKind.PROGRESS,
            ProgressMetadata(301, 1, 1, 2_500, 0, len(wire_target._PROGRESS_BODY)),
            body=wire_target._PROGRESS_BODY,
        ),
        _runtime_event(
            MessageType.CREDIT_UPDATE,
            RuntimeEventMetadataKind.PRESSURE,
            PressureMetadata(1, 1, 0, 0, 0, 0),
        ),
        _runtime_event(
            MessageType.PARTIAL_RESULT,
            RuntimeEventMetadataKind.PARTIAL_RESULT,
            PartialResultMetadata(301, 1, 0, 0, len(wire_target._PARTIAL_BODY), 0),
            body=wire_target._PARTIAL_BODY,
        ),
    ]
    monkeypatch.setattr(wire_target, "_open_connection", open_connection)
    monkeypatch.setattr(wire_target, "_await_runtime_frames", lambda *args, **kwargs: frames)
    monkeypatch.setattr(
        wire_target,
        "_poll_result",
        lambda *args, **kwargs: SimpleNamespace(
            event=SimpleNamespace(
                as_runtime=lambda: SimpleNamespace(tail=SimpleNamespace(body=wire_target._RESPONSE_BODY))
            )
        ),
    )

    wire_target._run_progress_client(_scenario("progress", "suite_as_server"), timeout_seconds=1.0)
    assert any(name == "submit" for name, _ in session.calls)

    monkeypatch.setattr(
        wire_target,
        "_poll_result",
        lambda *args, **kwargs: SimpleNamespace(
            event=SimpleNamespace(as_runtime=lambda: SimpleNamespace(tail=SimpleNamespace(body=b"wrong")))
        ),
    )
    with pytest.raises(RuntimeError, match="expected canonical result body"):
        wire_target._run_progress_client(_scenario("progress", "suite_as_server"), timeout_seconds=1.0)

    monkeypatch.setattr(
        wire_target,
        "_poll_result",
        lambda *args, **kwargs: SimpleNamespace(event=SimpleNamespace(as_runtime=lambda: None)),
    )
    with pytest.raises(RuntimeError, match="expected a wire RESULT_PUSH terminal event"):
        wire_target._run_progress_client(_scenario("progress", "suite_as_server"), timeout_seconds=1.0)


def test_open_connection_retries_and_closes_context(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    exits: list[tuple[object, object, object]] = []

    class Context:
        def __enter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise NativeRuntimeError(NativeStatus(1), "retry")
            return "connected"

        def __exit__(self, *args):
            exits.append(args)

    monkeypatch.setattr(wire_target, "connect_native_client_connection", lambda *args, **kwargs: Context())
    monkeypatch.setattr(wire_target.time, "sleep", lambda _seconds: None)
    transport = wire_target.LiveWireTransport("tcp", "tcp://127.0.0.1:1")
    with wire_target._open_connection(transport, deadline=wire_target.time.monotonic() + 1.0) as connection:
        assert connection == "connected"
    assert attempts == 2
    assert exits == [(None, None, None)]

    with pytest.raises(LookupError):
        with wire_target._open_connection(transport, deadline=wire_target.time.monotonic() + 1.0):
            raise LookupError("caller failed")
    assert exits[-1][0] is LookupError


def test_poll_helpers_handle_empty_batches_mismatch_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wire_target.time, "sleep", lambda _seconds: None)
    session = _FakeSession()
    expected = _runtime_event(
        MessageType.PROGRESS,
        RuntimeEventMetadataKind.PROGRESS,
        ProgressMetadata(301, 1, 1, 2_500, 0, len(wire_target._PROGRESS_BODY)),
        body=wire_target._PROGRESS_BODY,
    )
    session.poll_frames_batches = [[], [expected]]
    assert wire_target._await_runtime_frames(
        session,
        [MessageType.PROGRESS],
        timeout_seconds=1.0,
    ) == [expected]

    session.poll_frames_batches = [[replace(expected, header=RuntimeFrameHeader(MessageType.CANCEL))]]
    with pytest.raises(RuntimeError, match="expected runtime frames"):
        wire_target._await_runtime_frames(session, [MessageType.PROGRESS], timeout_seconds=1.0)

    operation = object()
    session.poll_results = [
        NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK)),
        SimpleNamespace(body=b"done"),
    ]
    assert (
        wire_target._poll_result(
            session,
            operation,
            deadline=wire_target.time.monotonic() + 1.0,
        ).body
        == b"done"
    )

    session.poll_events_batches = [
        [],
        [
            NativeServerEvent.runtime(
                _runtime_event(
                    MessageType.SESSION_CLOSE,
                    RuntimeEventMetadataKind.SESSION_CLOSE,
                    SessionCloseMetadata(SessionCloseReason.NORMAL, InFlightPolicy.DRAIN, 0, 0, 0, 0),
                )
            )
        ],
    ]
    wire_target._finish_peer_close(session, timeout_seconds=1.0)
    assert session.calls[-1] == ("close", None)


def test_validate_progress_frames_rejects_noncanonical_payload() -> None:
    valid = [
        _runtime_event(
            MessageType.PROGRESS,
            RuntimeEventMetadataKind.PROGRESS,
            ProgressMetadata(301, 1, 1, 2_500, 0, len(wire_target._PROGRESS_BODY)),
            body=wire_target._PROGRESS_BODY,
        ),
        _runtime_event(
            MessageType.CREDIT_UPDATE,
            RuntimeEventMetadataKind.PRESSURE,
            PressureMetadata(1, 1, 0, 0, 0, 0),
        ),
        _runtime_event(
            MessageType.PARTIAL_RESULT,
            RuntimeEventMetadataKind.PARTIAL_RESULT,
            PartialResultMetadata(301, 1, 0, 0, len(wire_target._PARTIAL_BODY), 0),
            body=wire_target._PARTIAL_BODY,
        ),
    ]
    wire_target._validate_progress_frames(valid)
    valid[0] = replace(valid[0], tail=RuntimeEventTail.with_body(b"wrong"))
    with pytest.raises(RuntimeError, match="PROGRESS body"):
        wire_target._validate_progress_frames(valid)


def test_live_scenario_and_transport_parsing_reads_tls_material(tmp_path: Path) -> None:
    (tmp_path / "cert.der").write_bytes(b"certificate")
    (tmp_path / "key.der").write_bytes(b"private-key")
    target = {
        "wire_conformance": {
            "transports": [
                {"name": "tcp", "endpoint": "127.0.0.1:19091"},
                {
                    "name": "quic",
                    "endpoint": "127.0.0.1:19092",
                    "security": {
                        "server_name": "localhost",
                        "trusted_certificate_der_path": "cert.der",
                        "certificate_der_path": "cert.der",
                        "private_key_pkcs8_der_path": "key.der",
                    },
                },
            ]
        }
    }
    plan = {
        "scenarios": [
            {"id": "tcp-case", "mode": "suite_as_client", "transport": "tcp"},
            {"id": "quic-case", "mode": "suite_as_proxy", "transport": "quic"},
        ]
    }

    scenarios = wire_target._live_scenarios(
        plan,
        target,
        target_path=tmp_path / "target.json",
        mode="suite_as_client",
    )
    assert scenarios[0].transport.endpoint == "tcp://127.0.0.1:19091"
    transports = wire_target._target_transports(target, target_path=tmp_path / "target.json")
    assert transports["quic"].endpoint == "quic://127.0.0.1:19092"
    assert transports["quic"].client_security is not None
    assert transports["quic"].server_security is not None

    with pytest.raises(ValueError, match="contains no scenarios"):
        wire_target._live_scenarios(plan, target, target_path=tmp_path / "target.json", mode="suite_as_server")
    with pytest.raises(ValueError, match="does not declare transport"):
        wire_target._live_scenarios(
            {"scenarios": [{"id": "missing", "mode": "suite_as_client", "transport": "ipc"}]},
            target,
            target_path=tmp_path / "target.json",
            mode="suite_as_client",
        )


@pytest.mark.parametrize(
    ("target", "match"),
    [
        ({}, "must contain wire_conformance"),
        ({"wire_conformance": {}}, "must contain transports"),
        ({"wire_conformance": {"transports": [7]}}, "must be objects"),
        (
            {
                "wire_conformance": {
                    "transports": [
                        {"name": "tcp", "endpoint": "one"},
                        {"name": "tcp", "endpoint": "two"},
                    ]
                }
            },
            "duplicate transport",
        ),
        (
            {"wire_conformance": {"transports": [{"name": "tcp", "endpoint": "one", "security": 7}]}},
            "security must be an object",
        ),
    ],
)
def test_target_transport_parsing_rejects_invalid_documents(tmp_path: Path, target: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        wire_target._target_transports(target, target_path=tmp_path / "target.json")


def test_target_transport_parsing_reports_missing_security_material(tmp_path: Path) -> None:
    target = {
        "wire_conformance": {
            "transports": [
                {
                    "name": "quic",
                    "endpoint": "127.0.0.1:19092",
                    "security": {
                        "server_name": "localhost",
                        "trusted_certificate_der_path": "missing-trust.der",
                        "certificate_der_path": "missing-certificate.der",
                        "private_key_pkcs8_der_path": "missing-key.der",
                    },
                }
            ]
        }
    }

    with pytest.raises(
        ValueError,
        match=r"wire target quic certificate_der_path was not found: .*missing-certificate\.der",
    ):
        wire_target._target_transports(target, target_path=tmp_path / "target.json")


def test_security_material_reader_reports_unreadable_path(tmp_path: Path) -> None:
    unreadable = tmp_path / "certificate.der"
    unreadable.mkdir()

    with pytest.raises(
        ValueError,
        match=r"wire target websocket certificate_der_path could not be read: .*certificate\.der",
    ):
        wire_target._read_security_material(
            tmp_path,
            {"certificate_der_path": unreadable.name},
            "certificate_der_path",
            transport="websocket",
        )


def test_json_and_required_string_helpers(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    path.write_text(json.dumps({"field": "value"}), encoding="utf-8")
    assert wire_target._read_json_object(path, description="document") == {"field": "value"}
    assert wire_target._required_string({"field": "value"}, "field") == "value"

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        wire_target._read_json_object(path, description="document")
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        wire_target._read_json_object(path, description="document")
    with pytest.raises(ValueError, match="was not found"):
        wire_target._read_json_object(tmp_path / "missing.json", description="document")
    with pytest.raises(ValueError, match="non-empty string"):
        wire_target._required_string({"field": ""}, "field")
