"""NNRP/1 benchmark wrapper for suite-owned execution plans."""

from __future__ import annotations

import argparse
import gc
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
    load_native_client,
    load_native_schema_codec,
    probe_native_artifact,
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

_RESULTS_SCHEMA_URL = "https://raw.githubusercontent.com/NagareWorks/nnrp-conformance/main/schemas/benchmark-results.schema.json"
_DEFAULT_IMPLEMENTATION_NAME = "nnrp-py"
_DEFAULT_SKIP_MESSAGE = "This benchmark scenario is not implemented in the current Python baseline runner."


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
        submitted = session.submit_operation(
            operation_id=counter,
            frame_id=counter,
            payload=payload,
        )
        session.complete_operation(submitted, payload)
        session.poll_result(submitted, max_events=2)

    try:
        for _ in range(warmup_iterations):
            operation()

        metrics = _measure_throughput_metrics(operation, duration_seconds, profile=profile)
        return _measured_throughput_result(scenario_id, metrics)
    except NativeWouldBlockError as error:
        return _skip_result(scenario_id, f"native submit/result loop unavailable: {error}")
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
        submitted = session.submit_operation(
            operation_id=counter,
            frame_id=counter,
            payload=payload,
        )
        session.complete_operation(submitted, payload)
        session.poll_result(submitted, max_events=2)

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
            return {"throughput_ops_per_sec": completed / duration_seconds}
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        if profile:
            tracemalloc.stop()
    process_seconds = time.process_time() - process_start
    return {
        "throughput_ops_per_sec": completed / duration_seconds,
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
    "native_submit_result_allocation_smoke": _run_native_submit_result_allocation_smoke,
    "runtime_probe": _run_runtime_probe,
    "session_lifecycle": _run_session_lifecycle,
    "submit_result_loop": _run_submit_result_loop,
    "transport_loopback": _run_transport_loopback,
}


if __name__ == "__main__":
    raise SystemExit(main())
