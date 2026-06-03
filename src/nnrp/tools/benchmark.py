"""NNRP/1 benchmark wrapper for suite-owned execution plans."""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from nnrp.core.enums import HeaderFlags, MessageType, WireFormat
from nnrp.core.header import HEADER_LENGTH, NnrpHeader
from nnrp.core.messages.control import ClientHelloMetadata, ServerHelloAckMetadata, TransportProbeAckMetadata
from nnrp.core.messages.data import InputProfile, TensorDType, TensorLayout, TileIndexMode
from nnrp.core.packet import (
    NnrpPacket,
    TensorSectionData,
    build_frame_submit_packet,
    build_result_push_packet,
    build_transport_probe_ack_packet,
    build_transport_probe_packet,
    pack_tensor_section_data,
    pack_tile_index_block,
    unpack_tensor_body,
    unpack_tile_index_block,
)
from nnrp.native import (
    NativeArtifactError,
    NativeWouldBlockError,
    default_artifact_root,
    load_native_client,
    load_native_schema_codec,
    probe_native_artifact,
    resolve_native_artifact,
)
from nnrp.schema import (
    pack_schema_descriptor,
    pack_typed_payload_descriptor,
    token_delta_payload_descriptor,
    token_delta_schema_descriptor,
    unpack_schema_descriptor,
    unpack_typed_payload_descriptor,
    validate_typed_payload_binding,
)

_RESULTS_SCHEMA_URL = (
    "https://raw.githubusercontent.com/NagareWorks/nnrp-conformance/main/schemas/benchmark-results.schema.json"
)
_DEFAULT_IMPLEMENTATION_NAME = "nnrp-py"
_DEFAULT_SKIP_MESSAGE = "This benchmark scenario is not implemented in the current Python baseline runner."
_NATIVE_SUBMIT_RESULT_ENTRYPOINTS = (
    "client_submit_result_compact",
    "client_submit_result",
    "client_submit",
    "client_complete_operation",
    "client_await_event",
    "client_await_events",
)


def build_benchmark_results_report(
    plan_document: dict[str, Any],
    *,
    implementation_name: str | None = None,
) -> dict[str, Any]:
    protocol_version = _require_string(plan_document, "protocol_version")
    scenarios = _require_scenario_list(plan_document)
    resolved_implementation_name = implementation_name or _require_string(plan_document, "implementation_name")

    return {
        "$schema": _RESULTS_SCHEMA_URL,
        "protocol_version": protocol_version,
        "implementation_name": resolved_implementation_name,
        "environment": _build_environment(),
        "results": [_run_scenario(scenario) for scenario in scenarios],
    }


def write_benchmark_results(plan_path: Path, output_path: Path) -> None:
    if not plan_path.is_file():
        raise ValueError(f"benchmark execution plan path does not exist: {plan_path}")

    plan_document = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan_document, dict):
        raise ValueError("benchmark execution plan must be a JSON object")

    report = build_benchmark_results_report(plan_document)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(report, indent=2)}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nnrp.tools.benchmark")
    parser.add_argument("--plan", default=os.environ.get("NNRP_CONFORMANCE_BENCHMARK_PLAN"))
    parser.add_argument("--output", default=os.environ.get("NNRP_CONFORMANCE_BENCHMARK_RESULTS"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.plan:
        parser.error("benchmark execution plan path is required via --plan or NNRP_CONFORMANCE_BENCHMARK_PLAN")

    if not args.output:
        parser.error("benchmark result path is required via --output or NNRP_CONFORMANCE_BENCHMARK_RESULTS")

    write_benchmark_results(Path(args.plan), Path(args.output))
    return 0


def _run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    scenario_id = _require_string(scenario, "id")
    workload = scenario.get("workload")
    if not isinstance(workload, dict):
        raise ValueError("benchmark execution plan scenario workload must be a JSON object")

    operation = _require_string(workload, "operation")
    runner = _SCENARIO_RUNNERS.get(operation)
    if runner is None:
        return _skip_result(scenario_id)

    return runner(scenario_id, workload)


def _run_header_encode_decode(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=100_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(10_000, iterations))
    header = NnrpHeader(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.PING,
        flags=HeaderFlags.CAN_DROP,
        meta_len=0,
        body_len=0,
        session_id=7,
        frame_id=11,
        view_id=13,
        route_id=17,
        trace_id=19,
    )

    def operation() -> None:
        encoded = header.pack()
        decoded = NnrpHeader.unpack(encoded, expected_wire_format=WireFormat.CURRENT)
        if decoded != header:
            raise RuntimeError("header benchmark roundtrip mismatch")

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    return _measured_latency_result(scenario_id, samples)


def _run_metadata_encode_decode(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=100_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(10_000, iterations))
    client_hello = ClientHelloMetadata(
        min_version_major=1,
        max_version_major=1,
        supported_wire_format_bitmap=1,
        supported_profile_bitmap=1,
        supported_payload_kind_bitmap=1,
        supported_codec_bitmap=1,
        supported_compression_bitmap=1,
        supported_dtype_bitmap=1 << int(TensorDType.UINT8),
        supported_layout_bitmap=1 << int(TensorLayout.NHWC),
        cache_digest_bitmap=0,
        cache_object_bitmap=0,
        cache_namespace_count=0,
        max_lane_count=2,
        max_cache_entries=0,
        max_cache_bytes=0,
        target_cadence_x100=6000,
        latency_budget_ms=16,
        quality_tier=1,
        degrade_policy=0,
        requested_session_id=41,
        auth_bytes=0,
        control_extension_bytes=0,
    )
    server_ack = ServerHelloAckMetadata(
        selected_version_major=1,
        selected_wire_format=int(WireFormat.CURRENT),
        auth_status=0,
        session_id=41,
        accepted_profile_bitmap=1,
        accepted_payload_kind_bitmap=1,
        accepted_codec_bitmap=1,
        accepted_compression_bitmap=1,
        accepted_dtype_bitmap=1 << int(TensorDType.UINT8),
        accepted_layout_bitmap=1 << int(TensorLayout.NHWC),
        cache_digest_bitmap=0,
        cache_object_bitmap=0,
        max_cache_entries=0,
        max_cache_bytes=0,
        max_lane_count=2,
        max_concurrent_frames=4,
        target_cadence_x100=6000,
        latency_budget_ms=16,
        quality_tier=1,
        degrade_policy=0,
        max_body_bytes=1 << 20,
        token_ttl_ms=30_000,
        retry_after_ms=0,
        control_extension_bytes=0,
        server_flags=0,
    )

    def operation() -> None:
        decoded_hello = ClientHelloMetadata.unpack(client_hello.pack())
        decoded_ack = ServerHelloAckMetadata.unpack(server_ack.pack())
        if decoded_hello != client_hello or decoded_ack != server_ack:
            raise RuntimeError("metadata benchmark roundtrip mismatch")

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    return _measured_latency_result(scenario_id, samples)


def _run_submit_result_loop(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = _positive_float(workload.get("duration_seconds"), default=10.0)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=1_000)
    profile = _bool(workload.get("profile"), default=False)
    submit_packet, result_packet = _build_submit_result_packets()

    def operation() -> None:
        decoded_submit = NnrpPacket.unpack(submit_packet)
        decoded_result = NnrpPacket.unpack(result_packet)
        if decoded_submit.header.msg_type is not MessageType.FRAME_SUBMIT:
            raise RuntimeError("submit/result benchmark decoded wrong submit type")
        if decoded_result.header.msg_type is not MessageType.RESULT_PUSH:
            raise RuntimeError("submit/result benchmark decoded wrong result type")

    for _ in range(warmup_iterations):
        operation()

    metrics = _measure_throughput_metrics(operation, duration_seconds, profile=profile)
    return _measured_throughput_result(scenario_id, metrics)


def _run_submit_result_metadata_encode_decode(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=100_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(10_000, iterations))
    submit_packet, result_packet = _build_submit_result_packet_views()
    submit_metadata = submit_packet.header.pack() + submit_packet.metadata
    result_metadata = result_packet.header.pack() + result_packet.metadata

    def operation() -> None:
        decoded_submit_header = NnrpHeader.unpack(submit_metadata[:HEADER_LENGTH])
        decoded_result_header = NnrpHeader.unpack(result_metadata[:HEADER_LENGTH])
        decoded_submit = NnrpPacket(header=decoded_submit_header, metadata=submit_packet.metadata, body=b"")
        decoded_result = NnrpPacket(header=decoded_result_header, metadata=result_packet.metadata, body=b"")
        if decoded_submit.header.msg_type is not MessageType.FRAME_SUBMIT:
            raise RuntimeError("submit metadata benchmark decoded wrong submit type")
        if decoded_result.header.msg_type is not MessageType.RESULT_PUSH:
            raise RuntimeError("submit metadata benchmark decoded wrong result type")

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    return _measured_latency_result(scenario_id, samples)


def _run_typed_payload_pack_unpack(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=100_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(10_000, iterations))
    tile_ids = (0, 1, 2, 3)
    section = _build_tensor_section()

    def operation() -> None:
        tile_index = pack_tile_index_block(tile_ids, mode=TileIndexMode.RAW_U16)
        section_payload = pack_tensor_section_data(section)
        unpack_tile_index_block(tile_index, mode=TileIndexMode.RAW_U16, tile_count=len(tile_ids))
        unpack_tensor_body(
            tile_index + section_payload,
            tile_index_bytes=len(tile_index),
            section_count=1,
            tile_count=4,
        )

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    return _measured_latency_result(scenario_id, samples)


def _run_runtime_probe(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=100_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(10_000, iterations))

    def operation() -> None:
        environment = _build_environment()
        capabilities = (
            "benchmark.header",
            "benchmark.metadata",
            "benchmark.submit_result",
            "benchmark.transport.tcp",
            "benchmark.transport.quic",
        )
        if not environment["os"] or "benchmark.header" not in capabilities:
            raise RuntimeError("runtime probe benchmark mismatch")

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    return _measured_latency_result(scenario_id, samples)


def _run_native_schema_descriptor_roundtrip(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=100_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(10_000, iterations))
    try:
        codec = load_native_schema_codec()
    except NativeArtifactError as error:
        return _skip_result(scenario_id, f"native schema codec unavailable: {error}")

    schema = token_delta_schema_descriptor()
    descriptor = token_delta_payload_descriptor(offset=8, length=13)

    def operation() -> None:
        decoded_schema = unpack_schema_descriptor(pack_schema_descriptor(schema, codec=codec), codec=codec)
        decoded_descriptor = unpack_typed_payload_descriptor(
            pack_typed_payload_descriptor(descriptor, codec=codec),
            codec=codec,
        )
        validate_typed_payload_binding((decoded_schema,), decoded_descriptor, codec=codec)
        if decoded_schema != schema or decoded_descriptor != descriptor:
            raise RuntimeError("native schema descriptor benchmark roundtrip mismatch")

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    return _measured_latency_result(scenario_id, samples)


def _run_native_event_polling(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=100_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(10_000, iterations))
    max_events = _positive_int(workload.get("max_events"), default=8)
    try:
        connection = load_native_client().bootstrap_connection(
            connection_id=1,
            generation=1,
            transport_id=2,
        )
    except NativeArtifactError as error:
        return _skip_result(scenario_id, f"native client unavailable: {error}")

    def operation() -> None:
        connection.poll_events_batch(max_events=max_events)

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    connection.close()
    return _measured_latency_result(scenario_id, samples)


def _run_native_batch_event_polling_throughput(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = _positive_float(workload.get("duration_seconds"), default=10.0)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=1_000)
    max_events = _positive_int(workload.get("max_events"), default=8)
    profile = _bool(workload.get("profile"), default=False)
    try:
        connection = load_native_client().bootstrap_connection(
            connection_id=1,
            generation=1,
            transport_id=2,
        )
    except NativeArtifactError as error:
        return _skip_result(scenario_id, f"native client unavailable: {error}")

    def operation() -> None:
        connection.poll_events_batch(max_events=max_events)

    try:
        for _ in range(warmup_iterations):
            operation()

        metrics = _measure_throughput_metrics(operation, duration_seconds, profile=profile)
        return _measured_throughput_result(scenario_id, metrics)
    finally:
        connection.close()


def _run_native_submit_result_loop(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = _positive_float(workload.get("duration_seconds"), default=10.0)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=1_000)
    payload_bytes = _positive_int(workload.get("payload_bytes"), default=1024)
    profile = _bool(workload.get("profile"), default=False)
    try:
        connection = load_native_client().connect(
            connection_id=1,
            generation=1,
            transport_id=2,
        )
    except NativeArtifactError as error:
        return _skip_result(scenario_id, f"native client unavailable: {error}")

    session = connection.open_session(
        requested_session_id=1,
        generation=1,
        profile_id=0,
        schema_id=0,
        schema_version=0,
    )
    _drain_native_setup_events(connection)
    payload = b"x" * payload_bytes
    counter = 0

    def operation() -> None:
        nonlocal counter
        counter += 1
        session.submit_result(
            operation_id=counter,
            frame_id=counter,
            payload=payload,
            result_payload=payload,
            max_events=2,
        )

    try:
        for _ in range(warmup_iterations):
            operation()

        metrics = _measure_throughput_metrics(operation, duration_seconds, profile=profile, include_completed=True)
        _add_native_submit_result_call_metrics(metrics, int(metrics["completed_operations"]))
        metrics["native_binding_mode"] = _native_binding_mode(session)
        return _measured_throughput_result(scenario_id, metrics)
    except NativeWouldBlockError as error:
        return _skip_result(scenario_id, f"native submit/result loop unavailable: {error}")
    finally:
        connection.close()


def _run_native_submit_result_cffi_api_loop(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = _positive_float(workload.get("duration_seconds"), default=10.0)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=1_000)
    payload_bytes = _positive_int(workload.get("payload_bytes"), default=1024)
    batch_size = _positive_int(workload.get("batch_size"), default=1024)
    profile = _bool(workload.get("profile"), default=False)
    try:
        artifact_path = resolve_native_artifact(default_artifact_root())
        ffi, cffi_api = _load_cffi_api_submit_result_module()
        connection = load_native_client(artifact_path).connect(
            connection_id=1,
            generation=1,
            transport_id=2,
        )
    except (ImportError, NativeArtifactError, OSError, RuntimeError, Exception) as error:
        return _skip_result(scenario_id, f"native cffi api submit/result loop unavailable: {error}")

    session = connection.open_session(
        requested_session_id=1,
        generation=1,
        profile_id=0,
        schema_id=0,
        schema_version=0,
    )
    _drain_native_setup_events(connection)
    native_session = session.handle.handle
    payload = b"x" * payload_bytes
    payload_view = ffi.from_buffer(payload)
    artifact_path_bytes = os.fsencode(artifact_path)
    out_result = ffi.new("NnrpPyCompactResult *")
    out_completed = ffi.new("size_t *")
    counter = 0
    has_batch = hasattr(cffi_api, "nnrp_py_client_submit_result_compact_batch")

    def single_operation() -> int:
        nonlocal counter
        counter += 1
        status = cffi_api.nnrp_py_client_submit_result_compact(
            artifact_path_bytes,
            native_session.kind,
            native_session.id,
            native_session.generation,
            native_session.flags,
            counter,
            counter,
            payload_view,
            payload_bytes,
            out_result,
        )
        if status != 0 or out_result.status_code != 0 or not out_result.has_result:
            raise RuntimeError(
                f"cffi api submit/result failed: wrapper_status={status} "
                f"ffi_status={out_result.status_code} has_result={int(out_result.has_result)}"
            )
        return 1

    def batch_operation() -> int:
        nonlocal counter
        operation_id_start = counter + 1
        status = cffi_api.nnrp_py_client_submit_result_compact_batch(
            artifact_path_bytes,
            native_session.kind,
            native_session.id,
            native_session.generation,
            native_session.flags,
            operation_id_start,
            operation_id_start,
            1,
            payload_view,
            payload_bytes,
            2,
            batch_size,
            out_result,
            out_completed,
        )
        completed = int(out_completed[0])
        counter += completed
        if status != 0 or out_result.status_code != 0 or completed != batch_size or not out_result.has_result:
            raise RuntimeError(
                f"cffi api submit/result batch failed: wrapper_status={status} "
                f"ffi_status={out_result.status_code} completed={completed} has_result={int(out_result.has_result)}"
            )
        return completed

    operation = batch_operation if has_batch else single_operation

    try:
        for _ in range(max(1, (warmup_iterations + batch_size - 1) // batch_size) if has_batch else warmup_iterations):
            operation()

        metrics = _measure_counted_throughput_metrics(operation, duration_seconds, profile=profile)
        if has_batch:
            metrics["native_ffi_calls_per_op"] = 1.0 / batch_size
            metrics["native_ffi_client_submit_result_compact_batch_calls_per_op"] = 1.0 / batch_size
            metrics["native_ffi_client_submit_result_compact_calls_per_op"] = 0.0
            metrics["native_batch_size"] = batch_size
        else:
            metrics["native_ffi_calls_per_op"] = 1.0
            metrics["native_ffi_client_submit_result_compact_calls_per_op"] = 1.0
            metrics["native_ffi_client_submit_result_compact_batch_calls_per_op"] = 0.0
        metrics["native_binding_mode"] = "cffi_api"
        return _measured_throughput_result(scenario_id, metrics)
    except RuntimeError as error:
        return _skip_result(scenario_id, f"native cffi api submit/result loop unavailable: {error}")
    finally:
        connection.close()


def _run_native_submit_result_allocation_smoke(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=1_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(100, iterations))
    payload_bytes = _positive_int(workload.get("payload_bytes"), default=1024)
    try:
        connection = load_native_client().connect(
            connection_id=1,
            generation=1,
            transport_id=2,
        )
    except NativeArtifactError as error:
        return _skip_result(scenario_id, f"native client unavailable: {error}")

    session = connection.open_session(
        requested_session_id=1,
        generation=1,
        profile_id=0,
        schema_id=0,
        schema_version=0,
    )
    _drain_native_setup_events(connection)
    payload = b"x" * payload_bytes
    counter = 0

    def operation() -> None:
        nonlocal counter
        counter += 1
        session.submit_result(
            operation_id=counter,
            frame_id=counter,
            payload=payload,
            result_payload=payload,
            max_events=2,
        )

    try:
        for _ in range(warmup_iterations):
            operation()

        metrics = _measure_allocation_smoke(operation, iterations)
        return {
            "id": scenario_id,
            "outcome": "measured",
            "metrics": metrics,
        }
    except NativeWouldBlockError as error:
        return _skip_result(scenario_id, f"native submit/result allocation smoke unavailable: {error}")
    finally:
        connection.close()


def _drain_native_setup_events(connection: Any) -> None:
    poll_events = getattr(connection, "poll_events", None)
    if callable(poll_events):
        try:
            poll_events()
        except NativeWouldBlockError:
            pass
        return

    poll_events_batch = getattr(connection, "poll_events_batch", None)
    if not callable(poll_events_batch):
        return
    try:
        while poll_events_batch(max_events=8):
            pass
    except NativeWouldBlockError:
        pass


class _NativeEntrypointCallCounter:
    def __init__(self, entrypoints: Any, names: Sequence[str]) -> None:
        self._entrypoints = entrypoints
        self._originals: dict[str, Callable[..., Any]] = {}
        self._counts: dict[str, int] = {}
        for name in names:
            candidate = getattr(entrypoints, name, None)
            if callable(candidate):
                self._originals[name] = candidate
                self._counts[name] = 0

    @classmethod
    def try_install(cls, entrypoints: Any) -> _NativeEntrypointCallCounter:
        counter = cls(entrypoints, _NATIVE_SUBMIT_RESULT_ENTRYPOINTS) if entrypoints is not None else cls(None, ())
        counter.install()
        return counter

    def install(self) -> None:
        for name, original in self._originals.items():
            setattr(self._entrypoints, name, self._wrap(name, original))

    def reset(self) -> None:
        for name in self._counts:
            self._counts[name] = 0

    def add_metrics(self, metrics: dict[str, float | int], completed_operations: int) -> None:
        if completed_operations <= 0 or not self._counts:
            return
        total_calls = sum(self._counts.values())
        metrics["native_ffi_calls_per_op"] = total_calls / completed_operations
        for name, count in self._counts.items():
            metrics[f"native_ffi_{name}_calls_per_op"] = count / completed_operations

    def restore(self) -> None:
        for name, original in self._originals.items():
            setattr(self._entrypoints, name, original)

    def _wrap(self, name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def counted(*args: Any, **kwargs: Any) -> Any:
            self._counts[name] += 1
            return original(*args, **kwargs)

        return counted


def _add_native_submit_result_call_metrics(metrics: dict[str, float | int], completed_operations: int) -> None:
    if completed_operations <= 0:
        return
    metrics["native_ffi_calls_per_op"] = 1.0
    metrics["native_ffi_client_submit_result_compact_calls_per_op"] = 1.0
    metrics["native_ffi_client_submit_result_calls_per_op"] = 0.0
    metrics["native_ffi_client_submit_calls_per_op"] = 0.0
    metrics["native_ffi_client_complete_operation_calls_per_op"] = 0.0
    metrics["native_ffi_client_await_event_calls_per_op"] = 0.0
    metrics["native_ffi_client_await_events_calls_per_op"] = 0.0
    metrics.setdefault("native_binding_mode", "unknown")


def _native_binding_mode(owner: Any) -> str:
    entrypoints = getattr(owner, "entrypoints", None)
    mode = getattr(entrypoints, "binding_mode", None)
    return mode if isinstance(mode, str) and mode else "unknown"


def _load_cffi_api_submit_result_module() -> tuple[Any, Any]:  # pragma: no cover
    try:
        from cffi import FFI
    except ImportError as exc:
        raise ImportError("cffi is not installed") from exc

    module_name = "_nnrp_cffi_api_submit_result"
    build_dir = Path("artifacts") / "cffi-api"
    build_dir.mkdir(parents=True, exist_ok=True)
    existing = next(build_dir.glob(f"{module_name}*.pyd"), None)
    if existing is None:
        existing = next(build_dir.glob(f"{module_name}*.so"), None)
    if existing is None:
        existing = _build_cffi_api_submit_result_module(FFI, module_name, build_dir)

    module = _load_compiled_cffi_api_module(module_name, existing)
    if not hasattr(module.lib, "nnrp_py_client_submit_result_compact_batch"):
        existing.unlink(missing_ok=True)
        existing = _build_cffi_api_submit_result_module(FFI, module_name, build_dir)
        module = _load_compiled_cffi_api_module(module_name, existing)
    return module.ffi, module.lib


def _build_cffi_api_submit_result_module(ffi_factory: Callable[[], Any], module_name: str, build_dir: Path) -> Path:
    builder = ffi_factory()
    builder.cdef(
        """
        typedef struct NnrpPyCompactResult {
            unsigned int status_code;
            unsigned int error_family;
            unsigned int protocol_error_code;
            unsigned int detail_code;
            unsigned char has_result;
            unsigned int event_kind;
            unsigned int result_state;
            unsigned long long operation_id;
            unsigned int frame_id;
            size_t payload_len;
        } NnrpPyCompactResult;

        int nnrp_py_client_submit_result_compact(
            const char *library_path,
            unsigned int session_kind,
            unsigned long long session_id,
            unsigned int session_generation,
            unsigned int session_flags,
            unsigned long long operation_id,
            unsigned int frame_id,
            const unsigned char *payload,
            size_t payload_len,
            NnrpPyCompactResult *out_result
        );

        int nnrp_py_client_submit_result_compact_batch(
            const char *library_path,
            unsigned int session_kind,
            unsigned long long session_id,
            unsigned int session_generation,
            unsigned int session_flags,
            unsigned long long operation_id_start,
            unsigned int frame_id_start,
            unsigned int frame_id_stride,
            const unsigned char *payload,
            size_t payload_len,
            size_t max_events,
            size_t iterations,
            NnrpPyCompactResult *out_result,
            size_t *out_completed
        );
        """
    )
    builder.set_source(module_name, _CFFI_API_SUBMIT_RESULT_SOURCE)
    return Path(builder.compile(tmpdir=str(build_dir), verbose=False))


def _load_compiled_cffi_api_module(module_name: str, existing: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, existing)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load compiled cffi api module: {existing}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_CFFI_API_SUBMIT_RESULT_SOURCE = r"""
#include <stdint.h>
#include <stddef.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h>
static void *nnrp_py_load_symbol(const char *library_path, const char *symbol_name) {
    static HMODULE library = NULL;
    if (library == NULL) {
        library = LoadLibraryA(library_path);
        if (library == NULL) {
            return NULL;
        }
    }
    return (void *)GetProcAddress(library, symbol_name);
}
#else
#include <dlfcn.h>
static void *nnrp_py_load_symbol(const char *library_path, const char *symbol_name) {
    static void *library = NULL;
    if (library == NULL) {
        library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
        if (library == NULL) {
            return NULL;
        }
    }
    return dlsym(library, symbol_name);
}
#endif

typedef struct NnrpFfiStatus {
    uint32_t status_code;
    uint32_t error_family;
    uint32_t protocol_error_code;
    uint32_t detail_code;
} NnrpFfiStatus;

typedef struct NnrpHandle {
    uint32_t kind;
    uint64_t id;
    uint32_t generation;
    uint32_t flags;
} NnrpHandle;

typedef struct NnrpBufferView {
    const uint8_t *ptr;
    size_t len;
} NnrpBufferView;

typedef struct NnrpFfiDiagnostic {
    NnrpFfiStatus status;
    uint64_t related_connection_id;
    uint32_t related_session_id;
    uint64_t related_operation_id;
    uint32_t related_frame_id;
} NnrpFfiDiagnostic;

typedef struct NnrpClientSubmitResultRequest {
    NnrpHandle session;
    uint64_t operation_id;
    uint32_t frame_id;
    NnrpBufferView submit_payload;
    NnrpBufferView result_payload;
    size_t max_events;
} NnrpClientSubmitResultRequest;

typedef struct NnrpClientSubmitResultBatchRequest {
    NnrpHandle session;
    uint64_t operation_id_start;
    uint32_t frame_id_start;
    uint32_t frame_id_stride;
    NnrpBufferView submit_payload;
    NnrpBufferView result_payload;
    size_t max_events;
    size_t iterations;
} NnrpClientSubmitResultBatchRequest;

typedef struct NnrpCompactResult {
    NnrpFfiStatus status;
    uint8_t has_result;
    uint32_t event_kind;
    uint32_t result_state;
    NnrpHandle operation;
    uint64_t operation_id;
    uint32_t frame_id;
    NnrpBufferView payload;
    NnrpFfiDiagnostic diagnostic;
} NnrpCompactResult;

typedef struct NnrpPyCompactResult {
    unsigned int status_code;
    unsigned int error_family;
    unsigned int protocol_error_code;
    unsigned int detail_code;
    unsigned char has_result;
    unsigned int event_kind;
    unsigned int result_state;
    unsigned long long operation_id;
    unsigned int frame_id;
    size_t payload_len;
} NnrpPyCompactResult;

typedef NnrpFfiStatus (*nnrp_client_submit_result_compact_fn)(
    NnrpClientSubmitResultRequest request,
    NnrpCompactResult *out_result
);

typedef NnrpFfiStatus (*nnrp_client_submit_result_compact_batch_fn)(
    NnrpClientSubmitResultBatchRequest request,
    NnrpCompactResult *out_last_result,
    size_t *out_completed
);

int nnrp_py_client_submit_result_compact(
    const char *library_path,
    unsigned int session_kind,
    unsigned long long session_id,
    unsigned int session_generation,
    unsigned int session_flags,
    unsigned long long operation_id,
    unsigned int frame_id,
    const unsigned char *payload,
    size_t payload_len,
    NnrpPyCompactResult *out_result
) {
    if (library_path == NULL || out_result == NULL) {
        return -1;
    }
    nnrp_client_submit_result_compact_fn submit_result =
        (nnrp_client_submit_result_compact_fn)nnrp_py_load_symbol(library_path, "nnrp_client_submit_result_compact");
    if (submit_result == NULL) {
        return -2;
    }

    NnrpClientSubmitResultRequest request;
    memset(&request, 0, sizeof(request));
    request.session.kind = (uint32_t)session_kind;
    request.session.id = (uint64_t)session_id;
    request.session.generation = (uint32_t)session_generation;
    request.session.flags = (uint32_t)session_flags;
    request.operation_id = (uint64_t)operation_id;
    request.frame_id = (uint32_t)frame_id;
    request.submit_payload.ptr = payload;
    request.submit_payload.len = payload_len;
    request.result_payload.ptr = payload;
    request.result_payload.len = payload_len;
    request.max_events = 2;

    NnrpCompactResult native_result;
    memset(&native_result, 0, sizeof(native_result));
    NnrpFfiStatus status = submit_result(request, &native_result);
    out_result->status_code = status.status_code;
    out_result->error_family = status.error_family;
    out_result->protocol_error_code = status.protocol_error_code;
    out_result->detail_code = status.detail_code;
    if (status.status_code != 0) {
        out_result->has_result = 0;
        return 0;
    }

    out_result->status_code = native_result.status.status_code;
    out_result->error_family = native_result.status.error_family;
    out_result->protocol_error_code = native_result.status.protocol_error_code;
    out_result->detail_code = native_result.status.detail_code;
    out_result->has_result = native_result.has_result;
    out_result->event_kind = native_result.event_kind;
    out_result->result_state = native_result.result_state;
    out_result->operation_id = native_result.operation_id;
    out_result->frame_id = native_result.frame_id;
    out_result->payload_len = native_result.payload.len;
    return 0;
}

int nnrp_py_client_submit_result_compact_batch(
    const char *library_path,
    unsigned int session_kind,
    unsigned long long session_id,
    unsigned int session_generation,
    unsigned int session_flags,
    unsigned long long operation_id_start,
    unsigned int frame_id_start,
    unsigned int frame_id_stride,
    const unsigned char *payload,
    size_t payload_len,
    size_t max_events,
    size_t iterations,
    NnrpPyCompactResult *out_result,
    size_t *out_completed
) {
    if (library_path == NULL || out_result == NULL || out_completed == NULL) {
        return -1;
    }
    nnrp_client_submit_result_compact_batch_fn submit_result_batch =
        (nnrp_client_submit_result_compact_batch_fn)nnrp_py_load_symbol(
            library_path,
            "nnrp_client_submit_result_compact_batch"
        );
    if (submit_result_batch == NULL) {
        return -2;
    }

    NnrpClientSubmitResultBatchRequest request;
    memset(&request, 0, sizeof(request));
    request.session.kind = (uint32_t)session_kind;
    request.session.id = (uint64_t)session_id;
    request.session.generation = (uint32_t)session_generation;
    request.session.flags = (uint32_t)session_flags;
    request.operation_id_start = (uint64_t)operation_id_start;
    request.frame_id_start = (uint32_t)frame_id_start;
    request.frame_id_stride = (uint32_t)frame_id_stride;
    request.submit_payload.ptr = payload;
    request.submit_payload.len = payload_len;
    request.result_payload.ptr = payload;
    request.result_payload.len = payload_len;
    request.max_events = max_events;
    request.iterations = iterations;

    NnrpCompactResult native_result;
    memset(&native_result, 0, sizeof(native_result));
    size_t native_completed = 0;
    NnrpFfiStatus status = submit_result_batch(request, &native_result, &native_completed);
    *out_completed = native_completed;
    out_result->status_code = status.status_code;
    out_result->error_family = status.error_family;
    out_result->protocol_error_code = status.protocol_error_code;
    out_result->detail_code = status.detail_code;
    if (status.status_code != 0) {
        out_result->has_result = 0;
        return 0;
    }

    out_result->status_code = native_result.status.status_code;
    out_result->error_family = native_result.status.error_family;
    out_result->protocol_error_code = native_result.status.protocol_error_code;
    out_result->detail_code = native_result.status.detail_code;
    out_result->has_result = native_result.has_result;
    out_result->event_kind = native_result.event_kind;
    out_result->result_state = native_result.result_state;
    out_result->operation_id = native_result.operation_id;
    out_result->frame_id = native_result.frame_id;
    out_result->payload_len = native_result.payload.len;
    return 0;
}
"""


def _run_native_artifact_probe(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=1_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(100, iterations))
    artifact_path = workload.get("artifact_path")
    root = workload.get("artifact_root")
    if artifact_path is not None and not isinstance(artifact_path, str):
        raise ValueError("benchmark workload artifact_path must be a string")
    if root is not None and not isinstance(root, str):
        raise ValueError("benchmark workload artifact_root must be a string")

    try:
        probe_native_artifact(artifact_path, root=root)
    except NativeArtifactError as error:
        return _skip_result(scenario_id, f"native artifact probe unavailable: {error}")

    def operation() -> None:
        probe_native_artifact(artifact_path, root=root)

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    return _measured_latency_result(scenario_id, samples)


def _run_session_lifecycle(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    iterations = _positive_int(workload.get("iterations"), default=100_000)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=min(10_000, iterations))
    client_hello = _build_client_hello_metadata()
    server_ack = _build_server_ack_metadata()
    close_packet = NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.CLOSE,
        session_id=41,
    )

    def operation() -> None:
        decoded_hello = ClientHelloMetadata.unpack(client_hello.pack())
        decoded_ack = ServerHelloAckMetadata.unpack(server_ack.pack())
        decoded_close = NnrpPacket.unpack(close_packet.pack())
        if decoded_hello.requested_session_id != decoded_ack.session_id:
            raise RuntimeError("session lifecycle benchmark session mismatch")
        if decoded_close.header.msg_type is not MessageType.CLOSE:
            raise RuntimeError("session lifecycle benchmark decoded wrong close type")

    for _ in range(warmup_iterations):
        operation()

    samples = _measure_microseconds(operation, iterations)
    return _measured_latency_result(scenario_id, samples)


def _run_transport_loopback(scenario_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = _positive_float(workload.get("duration_seconds"), default=10.0)
    warmup_iterations = _non_negative_int(workload.get("warmup_iterations"), default=1_000)
    profile = _bool(workload.get("profile"), default=False)
    payload_bytes = _positive_int(workload.get("probe_payload_bytes"), default=32 * 1024)
    probe = build_transport_probe_packet(
        metadata=_transport_probe_metadata(probe_id=7, probe_payload_bytes=payload_bytes),
        body=b"x" * payload_bytes,
        trace_id=19,
    ).pack()
    ack = build_transport_probe_ack_packet(
        metadata=TransportProbeAckMetadata(probe_id=7, reserved=0, server_recv_ts_us=123456),
        trace_id=19,
    ).pack()

    def operation() -> None:
        decoded_probe = NnrpPacket.unpack(probe)
        decoded_ack = NnrpPacket.unpack(ack)
        if decoded_probe.header.msg_type is not MessageType.TRANSPORT_PROBE:
            raise RuntimeError("transport benchmark decoded wrong probe type")
        if decoded_ack.header.msg_type is not MessageType.TRANSPORT_PROBE_ACK:
            raise RuntimeError("transport benchmark decoded wrong ack type")

    for _ in range(warmup_iterations):
        operation()

    metrics = _measure_throughput_metrics(operation, duration_seconds, profile=profile)
    return _measured_throughput_result(scenario_id, metrics)


def _build_submit_result_packets() -> tuple[bytes, bytes]:
    submit, result = _build_submit_result_packet_views()
    return submit.pack(), result.pack()


def _build_submit_result_packet_views() -> tuple[NnrpPacket, NnrpPacket]:
    section = _build_tensor_section()
    tile_ids = (0, 1, 2, 3)
    submit = build_frame_submit_packet(
        session_id=41,
        frame_id=303,
        src_width=64,
        src_height=64,
        tile_width=32,
        tile_height=32,
        tile_ids=tile_ids,
        sections=(section,),
        camera_block=b"NNRP-BENCHMARK-CAMERA",
        input_profile=InputProfile.CHANGED_TILES_LUMA,
        tile_index_mode=TileIndexMode.RAW_U16,
        latency_budget_ms=16,
    )
    result = build_result_push_packet(
        session_id=41,
        frame_id=303,
        tile_ids=tile_ids,
        sections=(section,),
        tile_index_mode=TileIndexMode.RAW_U16,
        inference_ms=4,
        queue_ms=1,
        server_total_ms=5,
    )
    return submit, result


def _build_tensor_section() -> TensorSectionData:
    tile_payload = b"\x07" * 1024
    return TensorSectionData(
        role_id=5,
        default_codec_id=0,
        dtype_id=TensorDType.UINT8,
        layout_id=TensorLayout.NHWC,
        tile_payloads=(tile_payload, tile_payload, tile_payload, tile_payload),
    )


def _build_client_hello_metadata() -> ClientHelloMetadata:
    return ClientHelloMetadata(
        min_version_major=1,
        max_version_major=1,
        supported_wire_format_bitmap=1,
        supported_profile_bitmap=1,
        supported_payload_kind_bitmap=1,
        supported_codec_bitmap=1,
        supported_compression_bitmap=1,
        supported_dtype_bitmap=1 << int(TensorDType.UINT8),
        supported_layout_bitmap=1 << int(TensorLayout.NHWC),
        cache_digest_bitmap=0,
        cache_object_bitmap=0,
        cache_namespace_count=0,
        max_lane_count=2,
        max_cache_entries=0,
        max_cache_bytes=0,
        target_cadence_x100=6000,
        latency_budget_ms=16,
        quality_tier=1,
        degrade_policy=0,
        requested_session_id=41,
        auth_bytes=0,
        control_extension_bytes=0,
    )


def _build_server_ack_metadata() -> ServerHelloAckMetadata:
    return ServerHelloAckMetadata(
        selected_version_major=1,
        selected_wire_format=int(WireFormat.CURRENT),
        auth_status=0,
        session_id=41,
        accepted_profile_bitmap=1,
        accepted_payload_kind_bitmap=1,
        accepted_codec_bitmap=1,
        accepted_compression_bitmap=1,
        accepted_dtype_bitmap=1 << int(TensorDType.UINT8),
        accepted_layout_bitmap=1 << int(TensorLayout.NHWC),
        cache_digest_bitmap=0,
        cache_object_bitmap=0,
        max_cache_entries=0,
        max_cache_bytes=0,
        max_lane_count=2,
        max_concurrent_frames=4,
        target_cadence_x100=6000,
        latency_budget_ms=16,
        quality_tier=1,
        degrade_policy=0,
        max_body_bytes=1 << 20,
        token_ttl_ms=30_000,
        retry_after_ms=0,
        control_extension_bytes=0,
        server_flags=0,
    )


def _transport_probe_metadata(*, probe_id: int, probe_payload_bytes: int):
    from nnrp.core.messages.control import TransportProbeMetadata

    return TransportProbeMetadata(
        probe_id=probe_id,
        probe_payload_bytes=probe_payload_bytes,
        client_send_ts_us=123000,
    )


def _measure_microseconds(operation: Callable[[], None], iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - start) / 1_000)
    return samples


def _measure_throughput_metrics(
    operation: Callable[[], None],
    duration_seconds: float,
    *,
    profile: bool,
    include_completed: bool = False,
) -> dict[str, float | int]:
    if profile:
        gc.collect()
        tracemalloc.start()
        process_start = time.process_time()
    deadline = time.perf_counter() + duration_seconds
    completed = 0
    try:
        while time.perf_counter() < deadline:
            operation()
            completed += 1
        if not profile:
            metrics: dict[str, float | int] = {"throughput_ops_per_sec": completed / duration_seconds}
            if include_completed:
                metrics["completed_operations"] = completed
            return metrics
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        if profile:
            tracemalloc.stop()
    process_seconds = time.process_time() - process_start
    metrics = {
        "throughput_ops_per_sec": completed / duration_seconds,
        "cpu_percent": (process_seconds / duration_seconds) * 100,
        "peak_memory_bytes": int(peak_bytes),
    }
    if include_completed:
        metrics["completed_operations"] = completed
    return metrics


def _measure_counted_throughput_metrics(
    operation: Callable[[], int],
    duration_seconds: float,
    *,
    profile: bool,
) -> dict[str, float | int]:
    if profile:
        gc.collect()
        tracemalloc.start()
        process_start = time.process_time()
    deadline = time.perf_counter() + duration_seconds
    completed = 0
    try:
        while time.perf_counter() < deadline:
            completed += operation()
        if not profile:
            return {
                "throughput_ops_per_sec": completed / duration_seconds,
                "completed_operations": completed,
            }
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        if profile:
            tracemalloc.stop()
    process_seconds = time.process_time() - process_start
    return {
        "throughput_ops_per_sec": completed / duration_seconds,
        "completed_operations": completed,
        "cpu_percent": (process_seconds / duration_seconds) * 100,
        "peak_memory_bytes": int(peak_bytes),
    }


def _measure_allocation_smoke(operation: Callable[[], None], iterations: int) -> dict[str, float]:
    gc.collect()
    before_blocks = sys.getallocatedblocks()
    tracemalloc.start()
    try:
        for _ in range(iterations):
            operation()
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    gc.collect()
    after_blocks = sys.getallocatedblocks()
    allocated_block_delta = max(0, after_blocks - before_blocks)
    return {
        "allocated_blocks_delta_per_op": allocated_block_delta / iterations,
        "peak_traced_bytes_per_op": peak_bytes / iterations,
    }


def _measured_latency_result(scenario_id: str, samples: list[float]) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "outcome": "measured",
        "metrics": {
            "p50_us": _percentile(samples, 50),
            "p95_us": _percentile(samples, 95),
            "p99_us": _percentile(samples, 99),
        },
    }


def _measured_throughput_result(scenario_id: str, metrics: dict[str, float | int]) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "outcome": "measured",
        "metrics": metrics,
    }


def _percentile(samples: list[float], percentile: int) -> float:
    if not samples:
        raise ValueError("benchmark samples must not be empty")
    if len(samples) == 1:
        return samples[0]
    sorted_samples = sorted(samples)
    if percentile == 50:
        return float(statistics.median(sorted_samples))
    rank = round((percentile / 100) * (len(sorted_samples) - 1))
    return float(sorted_samples[rank])


def _skip_result(scenario_id: str, message: str = _DEFAULT_SKIP_MESSAGE) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "outcome": "skip",
        "message": message,
    }


def _build_environment() -> dict[str, str]:
    return {
        "host_runtime": platform.python_version(),
        "os": platform.system().lower() or "unknown",
        "arch": platform.machine().lower() or "unknown",
        "cpu": platform.processor() or platform.machine() or "unknown",
    }


def _positive_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value <= 0:
        raise ValueError("benchmark workload iterations must be a positive integer")
    return value


def _positive_float(value: object, *, default: float) -> float:
    if value is None:
        return default
    if not isinstance(value, int | float) or value <= 0:
        raise ValueError("benchmark workload duration_seconds must be a positive number")
    return float(value)


def _non_negative_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value < 0:
        raise ValueError("benchmark workload warmup_iterations must be a non-negative integer")
    return value


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError("benchmark workload profile must be a boolean")
    return value


def _require_string(document: dict[str, Any], field_name: str) -> str:
    value = document.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"benchmark document field '{field_name}' must be a non-empty string")
    return value


def _require_scenario_list(document: dict[str, Any]) -> list[dict[str, Any]]:
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("benchmark execution plan must contain a scenarios list")

    normalized_scenarios: list[dict[str, Any]] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("benchmark execution plan scenarios must be JSON objects")
        normalized_scenarios.append(scenario)
    return normalized_scenarios


_SCENARIO_RUNNERS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "header_encode_decode": _run_header_encode_decode,
    "metadata_encode_decode": _run_metadata_encode_decode,
    "submit_result_metadata_encode_decode": _run_submit_result_metadata_encode_decode,
    "typed_payload_pack_unpack": _run_typed_payload_pack_unpack,
    "native_schema_descriptor_roundtrip": _run_native_schema_descriptor_roundtrip,
    "native_event_polling": _run_native_event_polling,
    "native_batch_event_polling_throughput": _run_native_batch_event_polling_throughput,
    "native_artifact_probe": _run_native_artifact_probe,
    "native_submit_result_loop": _run_native_submit_result_loop,
    "native_submit_result_cffi_api_loop": _run_native_submit_result_cffi_api_loop,
    "native_submit_result_allocation_smoke": _run_native_submit_result_allocation_smoke,
    "runtime_probe": _run_runtime_probe,
    "session_lifecycle": _run_session_lifecycle,
    "submit_result_loop": _run_submit_result_loop,
    "transport_loopback": _run_transport_loopback,
}


if __name__ == "__main__":
    raise SystemExit(main())
