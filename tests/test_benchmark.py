import json
from pathlib import Path
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
                "description": "Transport loopback throughput.",
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
                "description": "IPC transport loopback throughput.",
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
                "description": "WebSocket transport loopback throughput.",
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
                "id": "l4.native.submit_result.throughput",
                "category": "throughput",
                "feature": "benchmark.native.submit_result.throughput",
                "required_capabilities": ["session.open_close", "operation.submit", "event.polling.batch"],
                "description": "Native submit/result runtime throughput.",
                "workload": {
                    "operation": "native_submit_result_loop",
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
                "id": "l4.native.submit_result.allocations",
                "category": "memory",
                "feature": "benchmark.native.submit_result.allocations",
                "required_capabilities": ["session.open_close", "operation.submit", "event.polling.batch"],
                "description": "Native submit/result Python allocation smoke.",
                "workload": {
                    "operation": "native_submit_result_allocation_smoke",
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
                "description": "TCP transport loopback throughput.",
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
    monkeypatch.setattr(benchmark, "load_native_client", _missing_native_client)
    monkeypatch.setattr(benchmark, "probe_native_artifact", _missing_native_probe)

    report = build_benchmark_results_report(_plan_document())

    assert report["implementation_name"] == "nnrp-py"
    assert report["protocol_version"] == "nnrp-1"
    assert report["environment"]["os"]

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

    transport_result = results["l4.transport.quic.loopback.throughput"]
    assert transport_result["outcome"] == "measured"
    assert transport_result["metrics"]["throughput_ops_per_sec"] > 0
    assert "cpu_percent" not in transport_result["metrics"]
    assert "peak_memory_bytes" not in transport_result["metrics"]
    assert results["l4.transport.ipc.loopback.throughput"]["outcome"] == "measured"
    assert results["l4.transport.websocket.loopback.throughput"]["outcome"] == "measured"

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
    assert results["l4.native.submit_result.throughput"]["outcome"] == "skip"
    assert results["l4.native.submit_cancel.throughput"]["outcome"] == "skip"
    assert results["l4.native.progress_partial.polling.throughput"]["outcome"] == "skip"
    assert results["l4.native.submit_result.allocations"]["outcome"] == "skip"
    assert results["l4.native.artifact_probe.latency"]["outcome"] == "skip"
    assert results["l4.session.lifecycle.latency"]["outcome"] == "measured"
    assert results["l4.transport.tcp.loopback.throughput"]["outcome"] == "measured"


def test_build_benchmark_results_report_can_profile_throughput_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "load_native_schema_codec", _missing_native_schema_codec)
    monkeypatch.setattr(benchmark, "load_native_client", _missing_native_client)
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
            "native_submit_result_loop",
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


def test_build_benchmark_results_report_measures_native_scenarios_when_artifacts_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_codec = FakeNativeSchemaCodec()
    native_client = FakeNativeClient()
    native_probe = FakeNativeProbe()
    monkeypatch.setattr(benchmark, "load_native_schema_codec", lambda: schema_codec)
    monkeypatch.setattr(benchmark, "load_native_client", lambda: native_client)
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
    native_submit_result = results["l4.native.submit_result.throughput"]
    assert native_submit_result["outcome"] == "measured"
    assert native_submit_result["metrics"]["throughput_ops_per_sec"] > 0
    assert native_submit_result["metrics"]["completed_operations"] > 0
    assert native_submit_result["metrics"]["native_ffi_calls_per_op"] == 1
    assert native_submit_result["metrics"]["native_ffi_client_submit_result_compact_calls_per_op"] == 1
    assert native_submit_result["metrics"]["native_ffi_client_submit_result_calls_per_op"] == 0
    assert native_submit_result["metrics"]["native_ffi_client_submit_calls_per_op"] == 0
    assert native_submit_result["metrics"]["native_ffi_client_complete_operation_calls_per_op"] == 0
    assert native_submit_result["metrics"]["native_ffi_client_await_events_calls_per_op"] == 0
    assert native_submit_result["metrics"]["native_binding_mode"] == "ctypes"
    native_submit_cancel = results["l4.native.submit_cancel.throughput"]
    assert native_submit_cancel["outcome"] == "measured"
    assert native_submit_cancel["metrics"]["throughput_ops_per_sec"] > 0
    assert native_submit_cancel["metrics"]["completed_operations"] > 0
    assert native_submit_cancel["metrics"]["native_ffi_calls_per_op"] == 2
    assert native_submit_cancel["metrics"]["native_ffi_client_submit_calls_per_op"] == 1
    assert native_submit_cancel["metrics"]["native_ffi_client_cancel_calls_per_op"] == 1
    assert native_submit_cancel["metrics"]["native_ffi_client_submit_result_compact_calls_per_op"] == 0
    assert native_submit_cancel["metrics"]["native_ffi_client_submit_result_calls_per_op"] == 0
    assert native_submit_cancel["metrics"]["native_ffi_client_complete_operation_calls_per_op"] == 0
    assert native_submit_cancel["metrics"]["native_binding_mode"] == "ctypes"
    native_progress_partial = results["l4.native.progress_partial.polling.throughput"]
    assert native_progress_partial["outcome"] == "measured"
    assert native_progress_partial["metrics"]["throughput_ops_per_sec"] > 0
    assert native_progress_partial["metrics"]["completed_operations"] > 0
    assert native_progress_partial["metrics"]["native_ffi_calls_per_op"] == 3
    assert native_progress_partial["metrics"]["native_ffi_client_submit_calls_per_op"] == 1
    assert native_progress_partial["metrics"]["native_ffi_client_send_result_hint_calls_per_op"] == 1
    assert native_progress_partial["metrics"]["native_ffi_client_await_events_calls_per_op"] == 1
    assert native_progress_partial["metrics"]["native_ffi_client_submit_result_compact_calls_per_op"] == 0
    assert native_progress_partial["metrics"]["native_ffi_client_submit_result_calls_per_op"] == 0
    assert native_progress_partial["metrics"]["native_ffi_client_complete_operation_calls_per_op"] == 0
    assert native_progress_partial["metrics"]["native_binding_mode"] == "ctypes"
    allocation_result = results["l4.native.submit_result.allocations"]
    assert allocation_result["outcome"] == "measured"
    assert allocation_result["metrics"]["allocated_blocks_delta_per_op"] >= 0
    assert allocation_result["metrics"]["peak_traced_bytes_per_op"] >= 0
    assert results["l4.native.artifact_probe.latency"]["outcome"] == "measured"
    assert schema_codec.validations == 4
    assert schema_codec.descriptor.profile_id == token_delta_schema_descriptor().profile_id
    assert len(native_client.connection.polled_batches) >= 4
    assert set(native_client.connection.polled_batches) == {2}
    assert len(native_client.connection.submitted_payloads) >= 4
    assert set(native_client.connection.submitted_payloads) == {b"x" * 8}
    assert len(native_client.connection.cancelled_frames) >= 1
    assert len(native_client.connection.result_hints) >= 1
    assert native_client.connection.object_metadata_payloads == [b"x" * 8] * 8
    assert native_client.connection.closed is True
    assert native_probe.calls == [(None, None)] * 5


def test_native_submit_result_cffi_api_loop_measures_when_wrapper_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeFFI:
        def from_buffer(self, payload: bytes):
            return payload

        def new(self, type_name: str):
            if type_name == "size_t *":
                return [0]
            return SimpleNamespace(status_code=0, has_result=0)

    class FakeCffiApi:
        def __init__(self) -> None:
            self.calls = 0
            self.batch_calls = 0

        def nnrp_py_client_submit_result_compact(self, *_args):
            self.calls += 1
            out_result = _args[-1]
            out_result.status_code = 0
            out_result.has_result = 1
            return 0

        def nnrp_py_client_submit_result_compact_batch(self, *_args):
            self.batch_calls += 1
            iterations = _args[-3]
            out_result = _args[-2]
            out_completed = _args[-1]
            out_result.status_code = 0
            out_result.has_result = 1
            out_completed[0] = iterations
            return 0

    native_client = FakeNativeClient()
    cffi_api = FakeCffiApi()
    monkeypatch.setattr(benchmark, "default_artifact_root", lambda: Path("native-root"))
    monkeypatch.setattr(benchmark, "resolve_native_artifact", lambda _root: Path("nnrp_ffi.dll"))
    monkeypatch.setattr(benchmark, "load_native_client", lambda _artifact_path=None: native_client)
    monkeypatch.setattr(benchmark, "_load_cffi_api_submit_result_module", lambda: (FakeFFI(), cffi_api))

    result = benchmark._run_native_submit_result_cffi_api_loop(
        "l4.native.submit_result.cffi_api.throughput",
        {"duration_seconds": 0.01, "warmup_iterations": 1, "payload_bytes": 8, "batch_size": 8},
    )

    assert result["outcome"] == "measured"
    assert result["metrics"]["native_binding_mode"] == "cffi_api"
    assert result["metrics"]["native_ffi_client_submit_result_compact_calls_per_op"] == 0
    assert result["metrics"]["native_ffi_client_submit_result_compact_batch_calls_per_op"] == 0.125
    assert result["metrics"]["native_batch_size"] == 8
    assert cffi_api.calls == 0
    assert cffi_api.batch_calls > 0
    assert native_client.connection.closed is True


def test_native_submit_result_cffi_api_loop_uses_single_wrapper_without_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFFI:
        def from_buffer(self, payload: bytes):
            return payload

        def new(self, _type_name: str):
            return SimpleNamespace(status_code=0, has_result=0)

    class FakeCffiApi:
        def __init__(self) -> None:
            self.calls = 0

        def nnrp_py_client_submit_result_compact(self, *_args):
            self.calls += 1
            out_result = _args[-1]
            out_result.status_code = 0
            out_result.has_result = 1
            return 0

    native_client = FakeNativeClient()
    cffi_api = FakeCffiApi()
    monkeypatch.setattr(benchmark, "default_artifact_root", lambda: Path("native-root"))
    monkeypatch.setattr(benchmark, "resolve_native_artifact", lambda _root: Path("nnrp_ffi.dll"))
    monkeypatch.setattr(benchmark, "load_native_client", lambda _artifact_path=None: native_client)
    monkeypatch.setattr(benchmark, "_load_cffi_api_submit_result_module", lambda: (FakeFFI(), cffi_api))

    result = benchmark._run_native_submit_result_cffi_api_loop(
        "l4.native.submit_result.cffi_api.throughput",
        {"duration_seconds": 0.01, "warmup_iterations": 1, "payload_bytes": 8},
    )

    assert result["outcome"] == "measured"
    assert result["metrics"]["native_ffi_calls_per_op"] == 1
    assert result["metrics"]["native_ffi_client_submit_result_compact_calls_per_op"] == 1
    assert result["metrics"]["native_ffi_client_submit_result_compact_batch_calls_per_op"] == 0
    assert cffi_api.calls > 0
    assert native_client.connection.closed is True


def test_native_submit_result_cffi_api_loop_skips_failed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeFFI:
        def from_buffer(self, payload: bytes):
            return payload

        def new(self, type_name: str):
            if type_name == "size_t *":
                return [0]
            return SimpleNamespace(status_code=0, has_result=0)

    class FakeCffiApi:
        def nnrp_py_client_submit_result_compact_batch(self, *_args):
            out_result = _args[-2]
            out_completed = _args[-1]
            out_result.status_code = 0
            out_result.has_result = 1
            out_completed[0] = 1
            return 0

    native_client = FakeNativeClient()
    monkeypatch.setattr(benchmark, "default_artifact_root", lambda: Path("native-root"))
    monkeypatch.setattr(benchmark, "resolve_native_artifact", lambda _root: Path("nnrp_ffi.dll"))
    monkeypatch.setattr(benchmark, "load_native_client", lambda _artifact_path=None: native_client)
    monkeypatch.setattr(benchmark, "_load_cffi_api_submit_result_module", lambda: (FakeFFI(), FakeCffiApi()))

    result = benchmark._run_native_submit_result_cffi_api_loop(
        "l4.native.submit_result.cffi_api.throughput",
        {"duration_seconds": 0.01, "warmup_iterations": 1, "payload_bytes": 8, "batch_size": 8},
    )

    assert result["outcome"] == "skip"
    assert "batch failed" in result["message"]
    assert native_client.connection.closed is True


def test_native_submit_result_cffi_api_loop_skips_wrapper_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark, "resolve_native_artifact", lambda _root: Path("nnrp_ffi.dll"))
    monkeypatch.setattr(
        benchmark,
        "_load_cffi_api_submit_result_module",
        lambda: (_ for _ in ()).throw(OSError("no compiler")),
    )

    result = benchmark._run_native_submit_result_cffi_api_loop(
        "l4.native.submit_result.cffi_api.throughput",
        {"duration_seconds": 0.01, "warmup_iterations": 1, "payload_bytes": 8},
    )

    assert result["outcome"] == "skip"
    assert "no compiler" in result["message"]


def test_cffi_api_submit_result_module_builds_and_loads_helpers(tmp_path: Path) -> None:
    class FakeBuilder:
        def __init__(self) -> None:
            self.cdef_source = ""
            self.set_source_args: tuple[str, str] | None = None

        def cdef(self, source: str) -> None:
            self.cdef_source = source

        def set_source(self, module_name: str, source: str) -> None:
            self.set_source_args = (module_name, source)

        def compile(self, *, tmpdir: str, verbose: bool) -> str:
            assert Path(tmpdir) == tmp_path
            assert verbose is False
            output = tmp_path / "compiled.py"
            output.write_text("value = 42\n", encoding="utf-8")
            return str(output)

    builder = FakeBuilder()
    built = benchmark._build_cffi_api_submit_result_module(lambda: builder, "compiled", tmp_path)

    assert built == tmp_path / "compiled.py"
    assert "nnrp_py_client_submit_result_compact_batch" in builder.cdef_source
    assert builder.set_source_args is not None
    assert builder.set_source_args[0] == "compiled"

    module = benchmark._load_compiled_cffi_api_module("compiled", built)
    assert module.value == 42


def test_measure_counted_throughput_metrics_profiles_completed_operations() -> None:
    calls = 0

    def operation() -> int:
        nonlocal calls
        calls += 1
        return 3

    metrics = benchmark._measure_counted_throughput_metrics(operation, 0.001, profile=True)

    assert metrics["completed_operations"] >= 3
    assert metrics["throughput_ops_per_sec"] > 0
    assert metrics["cpu_percent"] >= 0
    assert metrics["peak_memory_bytes"] >= 0
    assert calls > 0


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
    monkeypatch.setattr(benchmark, "load_native_client", _missing_native_client)
    monkeypatch.setattr(benchmark, "probe_native_artifact", _missing_native_probe)

    report = build_benchmark_results_report(_plan_document())

    results = {result["id"]: result for result in report["results"]}
    assert results["l4.native.schema_descriptor.latency"]["outcome"] == "skip"
    assert "missing schema artifact" in results["l4.native.schema_descriptor.latency"]["message"]
    assert results["l4.native.event_polling.latency"]["outcome"] == "skip"
    assert "missing client artifact" in results["l4.native.event_polling.latency"]["message"]
    assert results["l4.native.event_polling.throughput"]["outcome"] == "skip"
    assert "missing client artifact" in results["l4.native.event_polling.throughput"]["message"]
    assert results["l4.native.object_metadata.copy.latency"]["outcome"] == "skip"
    assert "missing client artifact" in results["l4.native.object_metadata.copy.latency"]["message"]
    assert results["l4.native.object_metadata.borrow.latency"]["outcome"] == "skip"
    assert "missing client artifact" in results["l4.native.object_metadata.borrow.latency"]["message"]
    assert results["l4.native.submit_result.throughput"]["outcome"] == "skip"
    assert "missing client artifact" in results["l4.native.submit_result.throughput"]["message"]
    assert results["l4.native.submit_cancel.throughput"]["outcome"] == "skip"
    assert "missing client artifact" in results["l4.native.submit_cancel.throughput"]["message"]
    assert results["l4.native.progress_partial.polling.throughput"]["outcome"] == "skip"
    assert "missing client artifact" in results["l4.native.progress_partial.polling.throughput"]["message"]
    assert results["l4.native.submit_result.allocations"]["outcome"] == "skip"
    assert "missing client artifact" in results["l4.native.submit_result.allocations"]["message"]
    assert results["l4.native.artifact_probe.latency"]["outcome"] == "skip"
    assert "missing probe artifact" in results["l4.native.artifact_probe.latency"]["message"]


def test_build_benchmark_results_report_skips_native_allocation_when_result_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_client = WouldBlockNativeClient()
    monkeypatch.setattr(benchmark, "load_native_schema_codec", _missing_native_schema_codec)
    monkeypatch.setattr(benchmark, "load_native_client", lambda: native_client)
    monkeypatch.setattr(benchmark, "probe_native_artifact", _missing_native_probe)

    report = build_benchmark_results_report(_plan_document())

    results = {result["id"]: result for result in report["results"]}
    allocation_result = results["l4.native.submit_result.allocations"]
    assert allocation_result["outcome"] == "skip"
    assert "allocation smoke unavailable" in allocation_result["message"]
    assert native_client.connection.closed is True


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


class FakeNativeClient:
    def __init__(self) -> None:
        self.connection = FakeNativeConnection()

    def connect(self, *, connection_id: int, generation: int, transport_id: int):
        assert (connection_id, generation, transport_id) == (1, 1, 2)
        return self.connection

    def bootstrap_connection(self, *, connection_id: int, generation: int, transport_id: int):
        assert (connection_id, generation, transport_id) == (1, 1, 2)
        return self.connection


class FakeNativeConnection:
    def __init__(self) -> None:
        self.polled_batches: list[int] = []
        self.submitted_payloads: list[bytes] = []
        self.completed_payloads: list[bytes] = []
        self.polled_results: list[int | None] = []
        self.cancelled_frames: list[int] = []
        self.result_hints: list[bytes] = []
        self.object_metadata_payloads: list[bytes] = []
        self.opened_session: FakeNativeSession | None = None
        self.closed = False

    def open_session(
        self,
        *,
        requested_session_id: int,
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
    ):
        assert (requested_session_id, generation, profile_id, schema_id, schema_version) == (1, 1, 0, 0, 0)
        self.opened_session = FakeNativeSession(self)
        return self.opened_session

    def poll_events_batch(self, *, max_events: int):
        self.polled_batches.append(max_events)
        return ()

    def poll_events(self):
        return ()

    def acquire_object_metadata_copy(self, payload: bytes | bytearray | memoryview):
        copied_payload = bytes(payload)
        self.object_metadata_payloads.append(copied_payload)
        return FakeNativeObjectMetadataBuffer(copied_payload)

    def close(self) -> None:
        self.closed = True


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
        return memoryview(self.buffer.payload)

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.buffer.borrow_count -= 1


class FakeNativeSession:
    def __init__(self, connection: FakeNativeConnection) -> None:
        self.connection = connection
        self.entrypoints = FakeNativeEntrypoints(self)
        self.handle = SimpleNamespace(handle=SimpleNamespace(kind=2, id=1, generation=1, flags=0))

    def submit_operation(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
    ):
        assert operation_id == frame_id
        self.entrypoints.client_submit(payload)
        return FakeNativeOperation(self.entrypoints, operation_id, frame_id)

    def complete_operation(
        self,
        operation: "FakeNativeOperation",
        payload: bytes | bytearray | memoryview = b"",
    ) -> None:
        self.entrypoints.client_complete_operation(payload)

    def poll_result(self, operation: "FakeNativeOperation", *, state=None, max_events: int | None = None):
        self.entrypoints.client_await_events(max_events)
        assert operation.operation_id == operation.frame_id
        assert max_events == 2
        assert state is not None
        return object()

    def send_result_hint(self, payload: bytes | bytearray | memoryview = b"") -> None:
        self.entrypoints.client_send_result_hint(payload)

    def submit_result(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        result_payload: bytes | bytearray | memoryview | None = None,
        max_events: int | None = None,
    ):
        assert operation_id == frame_id
        assert max_events == 2
        self.entrypoints.client_submit_result(
            payload,
            payload if result_payload is None else result_payload,
            max_events,
        )
        return object()


class FakeNativeEntrypoints:
    def __init__(self, session: FakeNativeSession) -> None:
        self.session = session
        self.binding_mode = "ctypes"

    def client_submit(self, payload: bytes | bytearray | memoryview) -> None:
        self.session.connection.submitted_payloads.append(bytes(payload))

    def client_complete_operation(self, payload: bytes | bytearray | memoryview) -> None:
        self.session.connection.completed_payloads.append(bytes(payload))

    def client_submit_result(
        self,
        payload: bytes | bytearray | memoryview,
        result_payload: bytes | bytearray | memoryview,
        max_events: int | None,
    ) -> None:
        self.session.connection.submitted_payloads.append(bytes(payload))
        self.session.connection.completed_payloads.append(bytes(result_payload))
        self.session.connection.polled_results.append(max_events)

    def client_await_event(self, max_events: int | None) -> None:
        self.session.connection.polled_results.append(max_events)

    def client_await_events(self, max_events: int | None) -> None:
        self.session.connection.polled_results.append(max_events)

    def client_cancel(self, frame_id: int) -> None:
        self.session.connection.cancelled_frames.append(frame_id)

    def client_send_result_hint(self, payload: bytes | bytearray | memoryview) -> None:
        self.session.connection.result_hints.append(bytes(payload))


class FakeNativeOperation:
    def __init__(self, entrypoints: FakeNativeEntrypoints | None, operation_id: int, frame_id: int) -> None:
        self.entrypoints = entrypoints
        self.operation_id = operation_id
        self.frame_id = frame_id

    def cancel(self) -> None:
        assert self.entrypoints is not None
        self.entrypoints.client_cancel(self.frame_id)


class WouldBlockNativeOperation:
    def __init__(self, operation_id: int, frame_id: int) -> None:
        self.operation_id = operation_id
        self.frame_id = frame_id

    def cancel(self) -> None:
        raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))


class WouldBlockNativeClient(FakeNativeClient):
    def __init__(self) -> None:
        self.connection = WouldBlockNativeConnection()


class WouldBlockNativeConnection(FakeNativeConnection):
    def open_session(
        self,
        *,
        requested_session_id: int,
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
    ):
        assert (requested_session_id, generation, profile_id, schema_id, schema_version) == (1, 1, 0, 0, 0)
        return WouldBlockNativeSession()


class WouldBlockNativeSession:
    entrypoints = None

    def submit_operation(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
    ):
        return WouldBlockNativeOperation(operation_id, frame_id)

    def complete_operation(
        self,
        operation: FakeNativeOperation,
        payload: bytes | bytearray | memoryview = b"",
    ) -> None:
        return None

    def poll_result(self, operation: FakeNativeOperation, *, state=None, max_events: int | None = None):
        raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    def send_result_hint(self, payload: bytes | bytearray | memoryview = b"") -> None:
        return None

    def submit_result(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        result_payload: bytes | bytearray | memoryview | None = None,
        max_events: int | None = None,
    ):
        raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))


class FakeNativeProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def __call__(self, artifact_path=None, *, root=None):
        self.calls.append((artifact_path, root))
        return object()


def _missing_native_schema_codec() -> object:
    raise NativeArtifactError("missing schema artifact")


def _missing_native_client() -> object:
    raise NativeArtifactError("missing client artifact")


def _missing_native_probe(*_args: object, **_kwargs: object) -> object:
    raise NativeArtifactError("missing probe artifact")
