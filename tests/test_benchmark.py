import ctypes
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

import nnrp.tools.benchmark as benchmark
from nnrp import (
    FFI_STATUS_WOULD_BLOCK,
    NativeArtifactError,
    NativeStatus,
    NativeWouldBlockError,
    token_delta_payload_descriptor,
    token_delta_schema_descriptor,
)
from nnrp.core import MessageType
from nnrp.tools.benchmark import build_benchmark_results_report, main, write_benchmark_results


def _plan_document() -> dict[str, object]:
    return {
        "$schema": "../../schemas/benchmark-execution-plan.schema.json",
        "protocol_version": "nnrp-1",
        "suite_version": "nnrp-1-bootstrap",
        "implementation_name": "nnrp-py",
        "artifacts": {
            "results_path": "artifacts/benchmark-results.json",
            "evidence_dir": "artifacts/benchmark-evidence",
        },
        "scenarios": [
            {
                "id": "l4.header.encode_decode.latency",
                "category": "latency",
                "feature": "benchmark.header",
                "required_capabilities": [],
                "description": "Header roundtrip latency.",
                "workload": {
                    "operation": "header_encode_decode",
                    "payload": "l0_header",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.submit_result.inline_tensor.throughput",
                "category": "throughput",
                "feature": "benchmark.submit_result",
                "required_capabilities": ["frame_submit.tensor.inline", "result_push.basic"],
                "description": "Submit/result throughput.",
                "workload": {
                    "operation": "submit_result_loop",
                    "payload": "inline_tensor_4k",
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.metadata.session_open_ack.latency",
                "category": "latency",
                "feature": "benchmark.metadata",
                "required_capabilities": ["session.open_close"],
                "description": "Metadata latency.",
                "workload": {
                    "operation": "metadata_encode_decode",
                    "payload": "session_open_ack",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.transport.quic.loopback.throughput",
                "category": "throughput",
                "feature": "benchmark.transport.quic",
                "required_capabilities": ["transport.quic"],
                "description": "Native QUIC role loopback throughput.",
                "workload": {
                    "operation": "transport_loopback",
                    "payload": "request_result_stream",
                    "transport": "quic",
                    "probe_payload_bytes": 8,
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.transport.ipc.loopback.throughput",
                "category": "throughput",
                "feature": "benchmark.transport.ipc",
                "required_capabilities": ["transport.ipc"],
                "description": "Native IPC role loopback throughput.",
                "workload": {
                    "operation": "transport_loopback",
                    "payload": "request_result_stream",
                    "transport": "ipc",
                    "probe_payload_bytes": 8,
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.transport.websocket.loopback.throughput",
                "category": "throughput",
                "feature": "benchmark.transport.websocket",
                "required_capabilities": ["transport.websocket"],
                "description": "Native WebSocket role loopback throughput.",
                "workload": {
                    "operation": "transport_loopback",
                    "payload": "request_result_stream",
                    "transport": "websocket",
                    "probe_payload_bytes": 8,
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.metadata.submit_result.latency",
                "category": "latency",
                "feature": "benchmark.metadata.submit_result",
                "required_capabilities": ["frame_submit.tensor.inline", "result_push.basic"],
                "description": "Submit/result metadata latency.",
                "workload": {
                    "operation": "submit_result_metadata_encode_decode",
                    "payload": "frame_submit_result_push",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.typed_payload.tensor_pack_unpack.latency",
                "category": "latency",
                "feature": "benchmark.typed_payload.tensor",
                "required_capabilities": ["frame_submit.tensor.inline"],
                "description": "Typed payload latency.",
                "workload": {
                    "operation": "typed_payload_pack_unpack",
                    "payload": "tensor_descriptor_plus_payload",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.runtime.probe.latency",
                "category": "latency",
                "feature": "benchmark.runtime_probe",
                "required_capabilities": [],
                "description": "Runtime probe latency.",
                "workload": {
                    "operation": "runtime_probe",
                    "payload": "version_capability_query",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.native.schema_descriptor.latency",
                "category": "latency",
                "feature": "benchmark.native.schema_descriptor",
                "required_capabilities": ["schema.descriptor.native"],
                "description": "Native schema descriptor latency.",
                "workload": {
                    "operation": "native_schema_descriptor_roundtrip",
                    "payload": "token_delta_descriptor",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.native.event_polling.latency",
                "category": "latency",
                "feature": "benchmark.native.event_polling",
                "required_capabilities": ["event.polling.batch"],
                "description": "Native batch event polling latency.",
                "workload": {
                    "operation": "native_event_polling",
                    "payload": "empty_batch",
                    "iterations": 3,
                    "warmup_iterations": 1,
                    "max_events": 2,
                },
            },
            {
                "id": "l4.native.event_polling.throughput",
                "category": "throughput",
                "feature": "benchmark.native.event_polling.throughput",
                "required_capabilities": ["event.polling.batch"],
                "description": "Native batch event polling throughput.",
                "workload": {
                    "operation": "native_batch_event_polling_throughput",
                    "payload": "empty_batch",
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                    "max_events": 2,
                },
            },
            {
                "id": "l4.runtime.control_metadata.latency",
                "category": "latency",
                "feature": "benchmark.runtime_control.metadata",
                "required_capabilities": [
                    "control.cancel_abort",
                    "control.deadline_expire",
                    "control.progress_partial",
                    "control.credit_backpressure",
                    "control.result_drop_reason",
                ],
                "description": "Preview4 runtime control metadata encode/decode latency.",
                "workload": {
                    "operation": "runtime_control_metadata_encode_decode",
                    "payload": "control_frame_metadata",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.runtime.object_metadata.latency",
                "category": "latency",
                "feature": "benchmark.runtime_object.metadata",
                "required_capabilities": [
                    "object.lifecycle",
                    "object.delta",
                    "cache.reference",
                ],
                "description": "Preview4 runtime object and cache metadata encode/decode latency.",
                "workload": {
                    "operation": "runtime_object_metadata_encode_decode",
                    "payload": "object_cache_metadata",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.native.object_metadata.copy.latency",
                "category": "latency",
                "feature": "benchmark.native.object_metadata.copy",
                "required_capabilities": ["object.lifecycle"],
                "description": "Native object metadata copied snapshot latency.",
                "workload": {
                    "operation": "native_object_metadata_copy_snapshot",
                    "payload": "object_metadata_copy",
                    "iterations": 3,
                    "warmup_iterations": 1,
                    "payload_bytes": 8,
                },
            },
            {
                "id": "l4.native.object_metadata.borrow.latency",
                "category": "latency",
                "feature": "benchmark.native.object_metadata.borrow",
                "required_capabilities": ["object.lifecycle"],
                "description": "Native object metadata borrowed view latency.",
                "workload": {
                    "operation": "native_object_metadata_borrowed_view",
                    "payload": "object_metadata_borrow",
                    "iterations": 3,
                    "warmup_iterations": 1,
                    "payload_bytes": 8,
                },
            },
            {
                "id": "l4.native.role_submit_result.throughput",
                "category": "throughput",
                "feature": "benchmark.native.submit_result.throughput",
                "required_capabilities": ["session.open_close", "operation.submit", "event.polling.batch"],
                "description": "Native submit/result runtime throughput.",
                "workload": {
                    "operation": "native_role_submit_result_loop",
                    "payload": "inline_payload",
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                    "payload_bytes": 8,
                },
            },
            {
                "id": "l4.native.submit_cancel.throughput",
                "category": "throughput",
                "feature": "benchmark.native.submit_cancel.throughput",
                "required_capabilities": ["session.open_close", "operation.submit", "control.cancel_abort"],
                "description": "Native submit/cancel runtime throughput.",
                "workload": {
                    "operation": "native_submit_cancel_loop",
                    "payload": "inline_payload",
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                    "payload_bytes": 8,
                },
            },
            {
                "id": "l4.native.progress_partial.polling.throughput",
                "category": "throughput",
                "feature": "benchmark.native.progress_partial.polling",
                "required_capabilities": [
                    "session.open_close",
                    "operation.submit",
                    "control.progress_partial",
                    "event.polling.batch",
                ],
                "description": "Native result-hint control and partial-result polling throughput.",
                "workload": {
                    "operation": "native_progress_partial_polling_loop",
                    "payload": "inline_payload",
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                    "payload_bytes": 8,
                    "max_events": 2,
                },
            },
            {
                "id": "l4.native.role_submit_result.allocations",
                "category": "memory",
                "feature": "benchmark.native.submit_result.allocations",
                "required_capabilities": ["session.open_close", "operation.submit", "event.polling.batch"],
                "description": "Native submit/result Python allocation smoke.",
                "workload": {
                    "operation": "native_role_submit_result_allocation_smoke",
                    "payload": "inline_payload",
                    "iterations": 3,
                    "warmup_iterations": 1,
                    "payload_bytes": 8,
                },
            },
            {
                "id": "l4.native.artifact_probe.latency",
                "category": "latency",
                "feature": "benchmark.native.artifact_probe",
                "required_capabilities": ["native.artifact.probe"],
                "description": "Native artifact load/probe latency smoke.",
                "workload": {
                    "operation": "native_artifact_probe",
                    "payload": "version_capability_query",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.session.lifecycle.latency",
                "category": "latency",
                "feature": "benchmark.session_lifecycle",
                "required_capabilities": ["session.open_close"],
                "description": "Session lifecycle latency.",
                "workload": {
                    "operation": "session_lifecycle",
                    "payload": "open_close_loop",
                    "iterations": 3,
                    "warmup_iterations": 1,
                },
            },
            {
                "id": "l4.transport.tcp.loopback.throughput",
                "category": "throughput",
                "feature": "benchmark.transport.tcp",
                "required_capabilities": ["transport.tcp"],
                "description": "Native TCP role loopback throughput.",
                "workload": {
                    "operation": "transport_loopback",
                    "payload": "request_result_stream",
                    "transport": "tcp",
                    "probe_payload_bytes": 8,
                    "duration_seconds": 0.01,
                    "warmup_iterations": 1,
                },
            },
        ],
    }


def test_build_benchmark_results_report_measures_configured_scenarios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark, "load_native_schema_codec", _missing_native_schema_codec)
    monkeypatch.setattr(benchmark, "_open_native_role_loopback", _missing_native_role_loopback)
    monkeypatch.setattr(benchmark, "probe_native_artifact", _missing_native_probe)

    report = build_benchmark_results_report(_plan_document())

    assert report["implementation_name"] == "nnrp-py"
    assert report["protocol_version"] == "nnrp-1"
    assert report["environment"]["os"]
    assert report["environment"]["notes"].startswith("candidate_wheel=source-tree; sdk_version=")

    results = {result["id"]: result for result in report["results"]}
    header_result = results["l4.header.encode_decode.latency"]
    assert header_result["outcome"] == "measured"
    assert header_result["metrics"]["p50_us"] >= 0
    assert header_result["metrics"]["p95_us"] >= 0
    assert header_result["metrics"]["p99_us"] >= 0

    submit_result = results["l4.submit_result.inline_tensor.throughput"]
    assert submit_result["outcome"] == "measured"
    assert submit_result["metrics"]["throughput_ops_per_sec"] > 0
    assert "cpu_percent" not in submit_result["metrics"]
    assert "peak_memory_bytes" not in submit_result["metrics"]

    metadata_result = results["l4.metadata.session_open_ack.latency"]
    assert metadata_result["outcome"] == "measured"
    assert metadata_result["metrics"]["p50_us"] >= 0

    assert results["l4.transport.quic.loopback.throughput"]["outcome"] == "skip"
    assert results["l4.transport.ipc.loopback.throughput"]["outcome"] == "skip"
    assert results["l4.transport.websocket.loopback.throughput"]["outcome"] == "skip"

    assert results["l4.metadata.submit_result.latency"]["outcome"] == "measured"
    assert results["l4.typed_payload.tensor_pack_unpack.latency"]["outcome"] == "measured"
    assert results["l4.runtime.probe.latency"]["outcome"] == "measured"
    assert results["l4.native.schema_descriptor.latency"]["outcome"] == "skip"
    assert results["l4.native.event_polling.latency"]["outcome"] == "skip"
    assert results["l4.native.event_polling.throughput"]["outcome"] == "skip"
    assert results["l4.runtime.control_metadata.latency"]["outcome"] == "measured"
    assert results["l4.runtime.object_metadata.latency"]["outcome"] == "measured"
    assert results["l4.native.object_metadata.copy.latency"]["outcome"] == "skip"
    assert results["l4.native.object_metadata.borrow.latency"]["outcome"] == "skip"
    assert results["l4.native.role_submit_result.throughput"]["outcome"] == "skip"
    assert results["l4.native.submit_cancel.throughput"]["outcome"] == "skip"
    assert results["l4.native.progress_partial.polling.throughput"]["outcome"] == "skip"
    assert results["l4.native.role_submit_result.allocations"]["outcome"] == "skip"
    assert results["l4.native.artifact_probe.latency"]["outcome"] == "skip"
    assert results["l4.session.lifecycle.latency"]["outcome"] == "measured"
    assert results["l4.transport.tcp.loopback.throughput"]["outcome"] == "skip"


def test_build_benchmark_results_report_can_profile_throughput_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "load_native_schema_codec", _missing_native_schema_codec)
    role_loopbacks = FakeNativeRoleLoopbacks()
    monkeypatch.setattr(benchmark, "_open_native_role_loopback", role_loopbacks.open)
    monkeypatch.setattr(benchmark, "probe_native_artifact", _missing_native_probe)
    plan = _plan_document()
    scenarios = plan["scenarios"]
    assert isinstance(scenarios, list)
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        workload = scenario.get("workload")
        assert isinstance(workload, dict)
        if workload.get("operation") in {
            "submit_result_loop",
            "transport_loopback",
            "native_batch_event_polling_throughput",
            "native_role_submit_result_loop",
        }:
            workload["profile"] = True

    report = build_benchmark_results_report(plan)

    results = {result["id"]: result for result in report["results"]}
    for scenario_id in (
        "l4.submit_result.inline_tensor.throughput",
        "l4.transport.quic.loopback.throughput",
        "l4.transport.ipc.loopback.throughput",
        "l4.transport.websocket.loopback.throughput",
        "l4.transport.tcp.loopback.throughput",
    ):
        result = results[scenario_id]
        assert result["outcome"] == "measured"
        assert result["metrics"]["throughput_ops_per_sec"] > 0
        assert result["metrics"]["cpu_percent"] >= 0
        assert result["metrics"]["peak_memory_bytes"] >= 0
        if scenario_id.startswith("l4.transport."):
            assert result["metrics"]["native_ffi_calls_per_op"] == 4


def test_build_benchmark_results_report_skips_unknown_operations() -> None:
    plan = _plan_document()
    scenarios = plan["scenarios"]
    assert isinstance(scenarios, list)
    scenarios.append(
        {
            "id": "l4.unknown",
            "workload": {
                "operation": "unknown_operation",
            },
        }
    )

    report = build_benchmark_results_report(plan)

    unknown_result = report["results"][-1]
    assert unknown_result["outcome"] == "skip"
    assert "not implemented" in unknown_result["message"]


def test_build_benchmark_results_report_can_override_implementation_name() -> None:
    report = build_benchmark_results_report(_plan_document(), implementation_name="custom-runner")

    assert report["implementation_name"] == "custom-runner"


def test_open_native_role_loopback_yields_roles_and_coordinates_close(monkeypatch: pytest.MonkeyPatch) -> None:
    client_session = object()
    server_session = object()
    calls: list[tuple[object, ...]] = []

    class Server:
        def accept(self, options: object) -> object:
            calls.append(("accept", options))
            return server_session

    class Client:
        def open_session(self, options: object) -> object:
            calls.append(("open-session", options))
            return client_session

    server = Server()
    client = Client()

    @contextmanager
    def listen(*args: object, **kwargs: object):
        calls.append(("listen", args, kwargs))
        yield server

    @contextmanager
    def connect(*args: object, **kwargs: object):
        calls.append(("connect", args, kwargs))
        yield client

    def close_sessions(client_value: object, server_value: object, _executor: ThreadPoolExecutor) -> None:
        calls.append(("close", client_value, server_value))

    monkeypatch.setattr(benchmark, "_native_role_loopback_endpoint", lambda _transport: "npipe://benchmark")
    monkeypatch.setattr(benchmark, "listen_native_server", listen)
    monkeypatch.setattr(benchmark, "connect_native_client_connection", connect)
    monkeypatch.setattr(benchmark, "_close_native_role_sessions", close_sessions)

    with benchmark._open_native_role_loopback() as roles:
        assert roles == (client, client_session, server_session)

    assert calls[-1] == ("close", client_session, server_session)


def test_close_native_role_sessions_completes_session_close_handshake() -> None:
    close_started = Event()
    close_acknowledged = Event()
    calls: list[str] = []

    class ClientSession:
        def close(self) -> None:
            calls.append("client-close-start")
            close_started.set()
            assert close_acknowledged.wait(timeout=1)
            calls.append("client-close-complete")

    class ServerSession:
        def poll_events(self, *, max_events: int, timeout_ms: int):
            assert max_events == 8
            assert timeout_ms == 5_000
            assert close_started.wait(timeout=1)
            calls.append("server-receive-close")
            return (SimpleNamespace(kind=benchmark.EVENT_KIND_SESSION_CLOSED),)

        def close(self) -> None:
            calls.append("server-close")
            close_acknowledged.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        benchmark._close_native_role_sessions(ClientSession(), ServerSession(), executor)

    assert calls == [
        "client-close-start",
        "server-receive-close",
        "server-close",
        "client-close-complete",
    ]


def test_close_native_role_sessions_rejects_missing_close_event() -> None:
    server_closed = Event()

    def close_client() -> None:
        assert server_closed.wait(timeout=1)

    client_session = SimpleNamespace(close=close_client)
    server_session = SimpleNamespace(
        poll_events=lambda **_kwargs: (),
        close=server_closed.set,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(RuntimeError, match="did not receive SESSION_CLOSE"):
            benchmark._close_native_role_sessions(client_session, server_session, executor)

    assert server_closed.is_set()


def test_benchmark_environment_records_release_candidate_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NNRP_BENCHMARK_SDK_COMMIT", "0123456789abcdef")
    monkeypatch.setenv("NNRP_BENCHMARK_RUST_ARTIFACT_VERSION", "1.0.0-preview.4.10")
    monkeypatch.setenv("NNRP_BENCHMARK_CANDIDATE_WHEEL", "nnrp_py-1.0.0rc4.post6-py3-none-linux.whl")

    environment = benchmark._build_environment()

    assert environment["sdk_commit"] == "0123456789abcdef"
    assert environment["nnrp_rs_artifact"] == "1.0.0-preview.4.10"
    assert environment["notes"].startswith(
        "candidate_wheel=nnrp_py-1.0.0rc4.post6-py3-none-linux.whl; sdk_version="
    )


def test_build_benchmark_results_report_measures_native_scenarios_when_artifacts_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_codec = FakeNativeSchemaCodec()
    role_loopbacks = FakeNativeRoleLoopbacks()
    native_probe = FakeNativeProbe()
    monkeypatch.setattr(benchmark, "load_native_schema_codec", lambda: schema_codec)
    monkeypatch.setattr(benchmark, "_open_native_role_loopback", role_loopbacks.open)
    monkeypatch.setattr(benchmark, "probe_native_artifact", native_probe)

    report = build_benchmark_results_report(_plan_document())

    results = {result["id"]: result for result in report["results"]}
    assert results["l4.native.schema_descriptor.latency"]["outcome"] == "measured"
    assert results["l4.native.event_polling.latency"]["outcome"] == "measured"
    native_event_throughput_result = results["l4.native.event_polling.throughput"]
    assert native_event_throughput_result["outcome"] == "measured"
    assert native_event_throughput_result["metrics"]["throughput_ops_per_sec"] > 0
    native_object_copy = results["l4.native.object_metadata.copy.latency"]
    assert native_object_copy["outcome"] == "measured"
    assert native_object_copy["metrics"]["p50_us"] >= 0
    native_object_borrow = results["l4.native.object_metadata.borrow.latency"]
    assert native_object_borrow["outcome"] == "measured"
    assert native_object_borrow["metrics"]["p50_us"] >= 0
    native_submit_result = results["l4.native.role_submit_result.throughput"]
    assert native_submit_result["outcome"] == "measured"
    assert native_submit_result["metrics"]["throughput_ops_per_sec"] > 0
    assert native_submit_result["metrics"]["completed_operations"] > 0
    assert native_submit_result["metrics"]["native_ffi_calls_per_op"] == 4
    assert native_submit_result["metrics"]["native_ffi_client_submit_calls_per_op"] == 1
    assert native_submit_result["metrics"]["native_ffi_server_await_events_calls_per_op"] == 1
    assert native_submit_result["metrics"]["native_ffi_server_send_result_calls_per_op"] == 1
    assert native_submit_result["metrics"]["native_ffi_client_await_events_calls_per_op"] == 1
    native_submit_cancel = results["l4.native.submit_cancel.throughput"]
    assert native_submit_cancel["outcome"] == "measured"
    assert native_submit_cancel["metrics"]["throughput_ops_per_sec"] > 0
    assert native_submit_cancel["metrics"]["completed_operations"] > 0
    assert native_submit_cancel["metrics"]["native_ffi_calls_per_op"] == 4
    assert native_submit_cancel["metrics"]["native_ffi_client_submit_calls_per_op"] == 1
    assert native_submit_cancel["metrics"]["native_ffi_client_cancel_calls_per_op"] == 1
    assert native_submit_cancel["metrics"]["native_ffi_server_await_events_calls_per_op"] == 2
    native_progress_partial = results["l4.native.progress_partial.polling.throughput"]
    assert native_progress_partial["outcome"] == "measured"
    assert native_progress_partial["metrics"]["throughput_ops_per_sec"] > 0
    assert native_progress_partial["metrics"]["completed_operations"] > 0
    assert native_progress_partial["metrics"]["native_ffi_calls_per_op"] == 7
    assert native_progress_partial["metrics"]["native_ffi_client_submit_calls_per_op"] == 1
    assert native_progress_partial["metrics"]["native_ffi_server_await_events_calls_per_op"] == 1
    assert native_progress_partial["metrics"]["native_ffi_runtime_frame_send_calls_per_op"] == 2
    assert native_progress_partial["metrics"]["native_ffi_server_send_result_calls_per_op"] == 1
    assert native_progress_partial["metrics"]["native_ffi_client_await_events_calls_per_op"] == 2
    for transport in ("tcp", "quic", "ipc", "websocket"):
        transport_result = results[f"l4.transport.{transport}.loopback.throughput"]
        assert transport_result["outcome"] == "measured"
        assert transport_result["metrics"]["throughput_ops_per_sec"] > 0
        assert transport_result["metrics"]["native_ffi_calls_per_op"] == 4
    allocation_result = results["l4.native.role_submit_result.allocations"]
    assert allocation_result["outcome"] == "measured"
    assert allocation_result["metrics"]["allocated_blocks_delta_per_op"] >= 0
    assert allocation_result["metrics"]["peak_traced_bytes_per_op"] >= 0
    assert results["l4.native.artifact_probe.latency"]["outcome"] == "measured"
    assert schema_codec.validations == 4
    assert schema_codec.descriptor.profile_id == token_delta_schema_descriptor().profile_id
    assert role_loopbacks.contexts
    assert all(context.closed for context in role_loopbacks.contexts)
    assert any(context.client_connection.submitted_payloads for context in role_loopbacks.contexts)
    assert any(context.client_connection.cancelled_frames for context in role_loopbacks.contexts)
    assert any(context.client_connection.object_metadata_payloads for context in role_loopbacks.contexts)
    assert native_probe.calls == [(None, None)] * 5
    for result in results.values():
        if result["outcome"] == "measured":
            assert all(isinstance(value, int | float) for value in result["metrics"].values())


def test_drain_native_setup_events_ignores_single_poll_would_block() -> None:
    class SinglePollConnection:
        def __init__(self) -> None:
            self.calls = 0

        def poll_events(self):
            self.calls += 1
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    connection = SinglePollConnection()

    benchmark._drain_native_setup_events(connection)

    assert connection.calls == 1


def test_drain_native_setup_events_supports_batch_only_connections() -> None:
    class BatchOnlyConnection:
        def __init__(self) -> None:
            self.calls: list[int] = []
            self.remaining = 2

        def poll_events_batch(self, *, max_events: int):
            self.calls.append(max_events)
            if self.remaining == 0:
                return ()
            self.remaining -= 1
            return ("event",)

    connection = BatchOnlyConnection()

    benchmark._drain_native_setup_events(connection)

    assert connection.calls == [8, 8, 8]


def test_drain_native_setup_events_ignores_batch_would_block_and_missing_pollers() -> None:
    class WouldBlockBatchConnection:
        def __init__(self) -> None:
            self.calls = 0

        def poll_events_batch(self, *, max_events: int):
            self.calls += 1
            assert max_events == 8
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    batch_connection = WouldBlockBatchConnection()

    benchmark._drain_native_setup_events(object())
    benchmark._drain_native_setup_events(batch_connection)

    assert batch_connection.calls == 1


def test_build_benchmark_results_report_skips_native_scenarios_without_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "load_native_schema_codec", _missing_native_schema_codec)
    monkeypatch.setattr(benchmark, "_open_native_role_loopback", _missing_native_role_loopback)
    monkeypatch.setattr(benchmark, "probe_native_artifact", _missing_native_probe)

    report = build_benchmark_results_report(_plan_document())

    results = {result["id"]: result for result in report["results"]}
    assert results["l4.native.schema_descriptor.latency"]["outcome"] == "skip"
    assert "missing schema artifact" in results["l4.native.schema_descriptor.latency"]["message"]
    assert results["l4.native.event_polling.latency"]["outcome"] == "skip"
    assert "missing role artifact" in results["l4.native.event_polling.latency"]["message"]
    assert results["l4.native.event_polling.throughput"]["outcome"] == "skip"
    assert "missing role artifact" in results["l4.native.event_polling.throughput"]["message"]
    assert results["l4.native.object_metadata.copy.latency"]["outcome"] == "skip"
    assert "missing role artifact" in results["l4.native.object_metadata.copy.latency"]["message"]
    assert results["l4.native.object_metadata.borrow.latency"]["outcome"] == "skip"
    assert "missing role artifact" in results["l4.native.object_metadata.borrow.latency"]["message"]
    assert results["l4.native.role_submit_result.throughput"]["outcome"] == "skip"
    assert "missing role artifact" in results["l4.native.role_submit_result.throughput"]["message"]
    assert results["l4.native.submit_cancel.throughput"]["outcome"] == "skip"
    assert "missing role artifact" in results["l4.native.submit_cancel.throughput"]["message"]
    assert results["l4.native.progress_partial.polling.throughput"]["outcome"] == "skip"
    assert "missing role artifact" in results["l4.native.progress_partial.polling.throughput"]["message"]
    assert results["l4.native.role_submit_result.allocations"]["outcome"] == "skip"
    assert "missing role artifact" in results["l4.native.role_submit_result.allocations"]["message"]
    assert results["l4.native.artifact_probe.latency"]["outcome"] == "skip"
    assert "missing probe artifact" in results["l4.native.artifact_probe.latency"]["message"]


def test_build_benchmark_results_report_supports_single_sample_header_measurement() -> None:
    plan = _plan_document()
    scenarios = plan["scenarios"]
    assert isinstance(scenarios, list)
    header_scenario = scenarios[0]
    assert isinstance(header_scenario, dict)
    workload = header_scenario["workload"]
    assert isinstance(workload, dict)
    workload["iterations"] = 1
    workload["warmup_iterations"] = 0

    report = build_benchmark_results_report(plan)

    header_result = report["results"][0]
    assert header_result["outcome"] == "measured"
    assert header_result["metrics"]["p50_us"] == header_result["metrics"]["p95_us"]


@pytest.mark.parametrize(
    ("workload", "match"),
    [
        ("bad", "workload must be a JSON object"),
        ({"operation": "header_encode_decode", "payload": "l0_header", "iterations": 0}, "positive integer"),
        (
            {"operation": "header_encode_decode", "payload": "l0_header", "warmup_iterations": -1},
            "non-negative integer",
        ),
        ({"operation": "submit_result_loop", "duration_seconds": 0}, "positive number"),
        ({"operation": "submit_result_loop", "profile": "yes"}, "profile must be a boolean"),
        ({"operation": "transport_loopback", "probe_payload_bytes": 0}, "positive integer"),
        ({"operation": "native_artifact_probe", "artifact_path": []}, "artifact_path must be a string"),
    ],
)
def test_build_benchmark_results_report_rejects_invalid_workload_shapes(workload: object, match: str) -> None:
    plan = _plan_document()
    scenarios = plan["scenarios"]
    assert isinstance(scenarios, list)
    header_scenario = scenarios[0]
    assert isinstance(header_scenario, dict)
    header_scenario["workload"] = workload

    with pytest.raises(ValueError, match=match):
        build_benchmark_results_report(plan)


def test_main_reads_paths_from_environment_and_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_path = tmp_path / "benchmark-plan.json"
    output_path = tmp_path / "artifacts" / "benchmark-results.json"
    plan_path.write_text(json.dumps(_plan_document()), encoding="utf-8")
    monkeypatch.setenv("NNRP_CONFORMANCE_BENCHMARK_PLAN", str(plan_path))
    monkeypatch.setenv("NNRP_CONFORMANCE_BENCHMARK_RESULTS", str(output_path))

    assert main([]) == 0

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["protocol_version"] == "nnrp-1"
    assert len(report["results"]) == 23


def test_main_accepts_explicit_cli_paths_and_creates_parent_directory(tmp_path: Path) -> None:
    plan_path = tmp_path / "benchmark-plan.json"
    output_path = tmp_path / "nested" / "artifacts" / "benchmark-results.json"
    plan_path.write_text(json.dumps(_plan_document()), encoding="utf-8")

    assert main(["--plan", str(plan_path), "--output", str(output_path)]) == 0

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["implementation_name"] == "nnrp-py"


def test_main_uses_argparse_error_when_required_paths_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NNRP_CONFORMANCE_BENCHMARK_PLAN", raising=False)
    monkeypatch.delenv("NNRP_CONFORMANCE_BENCHMARK_RESULTS", raising=False)

    with pytest.raises(SystemExit, match="2"):
        main([])


def test_write_benchmark_results_rejects_missing_plan_path(tmp_path: Path) -> None:
    output_path = tmp_path / "artifacts" / "benchmark-results.json"

    with pytest.raises(ValueError, match="benchmark execution plan path does not exist"):
        write_benchmark_results(tmp_path / "missing-plan.json", output_path)


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ([], "must be a JSON object"),
        ({"protocol_version": "nnrp-1"}, "scenarios list"),
        (
            {
                "protocol_version": "nnrp-1",
                "scenarios": ["l4.header.encode_decode.latency"],
            },
            "JSON objects",
        ),
    ],
)
def test_write_benchmark_results_rejects_invalid_plan_shapes(
    tmp_path: Path,
    document: object,
    match: str,
) -> None:
    plan_path = tmp_path / "benchmark-plan.json"
    output_path = tmp_path / "artifacts" / "benchmark-results.json"
    plan_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        write_benchmark_results(plan_path, output_path)


class FakeNativeSchemaCodec:
    def __init__(self) -> None:
        self.schema = token_delta_schema_descriptor()
        self.descriptor = token_delta_payload_descriptor(offset=8, length=13)
        self.validations = 0

    def parse_schema_descriptor(self, payload: bytes | bytearray | memoryview):
        assert bytes(payload) == b"native-schema"
        return self.schema

    def write_schema_descriptor(self, descriptor):
        assert descriptor == self.schema
        return b"native-schema"

    def parse_typed_payload_descriptor(self, payload: bytes | bytearray | memoryview):
        assert bytes(payload) == b"native-typed"
        return self.descriptor

    def write_typed_payload_descriptor(self, descriptor):
        assert descriptor == self.descriptor
        return b"native-typed"

    def validate_typed_payload_binding(self, schemas, descriptor) -> None:
        assert schemas == (self.schema,)
        assert descriptor == self.descriptor
        self.validations += 1


class FakeNativeRoleLoopbacks:
    def __init__(self) -> None:
        self.contexts: list[FakeNativeRoleContext] = []

    def open(self, _transport: str = "ipc") -> "FakeNativeRoleContext":
        context = FakeNativeRoleContext()
        self.contexts.append(context)
        return context


class FakeNativeRoleContext:
    def __init__(self) -> None:
        self.client_connection = FakeNativeConnection()
        self.server_session = FakeNativeServerSession(self.client_connection)
        self.client_connection.server_session = self.server_session
        self.client_session = FakeNativeSession(self.client_connection)
        self.client = SimpleNamespace(connection=self.client_connection)
        self.closed = False

    def __enter__(self):
        return self.client, self.client_session, self.server_session

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.closed = True


class FakeNativeEntrypoints:
    binding_mode = "ctypes"

    def client_submit(self, *_args) -> None:
        return None

    def client_await_events(self, *_args) -> None:
        return None

    def client_cancel(self, *_args) -> None:
        return None

    def server_await_events(self, *_args) -> None:
        return None

    def server_send_result(self, *_args) -> None:
        return None

    def runtime_frame_send(self, *_args) -> None:
        return None


class FakeNativeConnection:
    def __init__(self) -> None:
        self.entrypoints = FakeNativeEntrypoints()
        self.server_session: FakeNativeServerSession | None = None
        self.polled_batches: list[int] = []
        self.submitted_payloads: list[bytes] = []
        self.cancelled_frames: list[int] = []
        self.object_metadata_payloads: list[bytes] = []
        self.runtime_events: list[SimpleNamespace] = []
        self.results: list[SimpleNamespace] = []

    def acquire_object_metadata_copy(self, payload: bytes | bytearray | memoryview):
        copied_payload = bytes(payload)
        self.object_metadata_payloads.append(copied_payload)
        return FakeNativeObjectMetadataBuffer(copied_payload)


class FakeNativeSession:
    def __init__(self, connection: FakeNativeConnection) -> None:
        self.connection = connection
        self.entrypoints = connection.entrypoints

    def poll_events(self):
        return self.poll_events_batch(max_events=8)

    def poll_events_batch(self, *, max_events: int):
        self.entrypoints.client_await_events(max_events)
        self.connection.polled_batches.append(max_events)
        events = tuple(self.connection.runtime_events[:max_events])
        del self.connection.runtime_events[:max_events]
        return events

    def submit_operation(
        self,
        *,
        operation_id: int,
        frame_id: int,
        metadata=None,
        body: bytes | bytearray | memoryview = b"",
    ) -> "FakeNativeOperation":
        del metadata
        self.entrypoints.client_submit(operation_id, frame_id, body)
        operation = FakeNativeOperation(self, operation_id, frame_id, bytes(body))
        self.connection.submitted_payloads.append(bytes(body))
        assert self.connection.server_session is not None
        self.connection.server_session.pending_submits.append(operation)
        return operation

    def poll_result(
        self,
        operation: "FakeNativeOperation",
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> SimpleNamespace:
        self.entrypoints.client_await_events(max_events, timeout_ms)
        for index, result in enumerate(self.connection.results):
            if result.frame_id == operation.frame_id:
                return self.connection.results.pop(index)
        raise AssertionError("fake role loopback result was not queued")


class FakeNativeOperation:
    def __init__(self, session: FakeNativeSession, operation_id: int, frame_id: int, payload: bytes) -> None:
        self.session = session
        self.operation_id = operation_id
        self.frame_id = frame_id
        self.payload = payload

    def cancel(self) -> None:
        self.session.entrypoints.client_cancel(self.frame_id)
        self.session.connection.cancelled_frames.append(self.frame_id)
        assert self.session.connection.server_session is not None
        self.session.connection.server_session.control_events.append(self.frame_id)


class FakeNativeServerOperation:
    def __init__(self, server_session: "FakeNativeServerSession", operation: FakeNativeOperation) -> None:
        self.server_session = server_session
        self.operation_id = operation.operation_id
        self.frame_id = operation.frame_id

    def send_result(self, metadata, body: bytes | bytearray | memoryview = b"") -> None:
        del metadata
        self.server_session.entrypoints.server_send_result(self.operation_id, body)
        self.server_session.connection.results.append(
            SimpleNamespace(
                operation_id=self.operation_id,
                frame_id=self.frame_id,
                body=bytes(body),
            )
        )


class FakeNativeServerSession:
    def __init__(self, connection: FakeNativeConnection) -> None:
        self.connection = connection
        self.entrypoints = FakeNativeEntrypoints()
        self.pending_submits: list[FakeNativeOperation] = []
        self.control_events: list[int] = []

    def receive_submit(self, *, timeout_ms: int = 0, max_events: int = 1) -> FakeNativeServerOperation:
        self.entrypoints.server_await_events(timeout_ms, max_events)
        if not self.pending_submits:
            raise AssertionError("fake role loopback submit was not queued")
        return FakeNativeServerOperation(self, self.pending_submits.pop(0))

    def poll_events(self, *, max_events: int = 1, timeout_ms: int = 0):
        self.entrypoints.server_await_events(max_events, timeout_ms)
        events = tuple(self.control_events[:max_events])
        del self.control_events[:max_events]
        return events

    def send_progress(self, metadata, body=b"") -> None:
        self.entrypoints.runtime_frame_send(MessageType.PROGRESS, metadata, body)
        self.connection.runtime_events.append(SimpleNamespace(message_type=int(MessageType.PROGRESS)))

    def send_partial_result(self, metadata, body=b"") -> None:
        self.entrypoints.runtime_frame_send(MessageType.PARTIAL_RESULT, metadata, body)
        self.connection.runtime_events.append(SimpleNamespace(message_type=int(MessageType.PARTIAL_RESULT)))


class FakeNativeObjectMetadataBuffer:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.borrow_count = 0
        self.closed = False

    def to_bytes(self) -> bytes:
        if self.closed:
            raise RuntimeError("fake native object metadata buffer is closed")
        return self.payload

    def borrow_view(self) -> "FakeNativeBorrowedBufferView":
        if self.closed:
            raise RuntimeError("fake native object metadata buffer is closed")
        return FakeNativeBorrowedBufferView(self)

    def close(self) -> None:
        if self.borrow_count != 0:
            raise RuntimeError("fake native object metadata buffer still has active borrows")
        self.closed = True


class FakeNativeBorrowedBufferView:
    def __init__(self, buffer: FakeNativeObjectMetadataBuffer) -> None:
        self.buffer = buffer

    def __enter__(self):
        self.buffer.borrow_count += 1
        array_type = ctypes.c_ubyte * len(self.buffer.payload)
        self._owner = array_type.from_buffer_copy(self.buffer.payload)
        return memoryview(self._owner).toreadonly()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.buffer.borrow_count -= 1


class FakeNativeProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def __call__(self, artifact_path=None, *, root=None):
        self.calls.append((artifact_path, root))
        return object()


def _missing_native_schema_codec() -> object:
    raise NativeArtifactError("missing schema artifact")


def _missing_native_role_loopback(_transport: str = "ipc") -> object:
    raise NativeArtifactError("missing role artifact")


def _missing_native_probe(*_args: object, **_kwargs: object) -> object:
    raise NativeArtifactError("missing probe artifact")
