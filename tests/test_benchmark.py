import json
from pathlib import Path

import pytest

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


def test_build_benchmark_results_report_measures_configured_scenarios() -> None:
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

    metadata_result = results["l4.metadata.session_open_ack.latency"]
    assert metadata_result["outcome"] == "measured"
    assert metadata_result["metrics"]["p50_us"] >= 0

    transport_result = results["l4.transport.quic.loopback.throughput"]
    assert transport_result["outcome"] == "measured"
    assert transport_result["metrics"]["throughput_ops_per_sec"] > 0

    assert results["l4.metadata.submit_result.latency"]["outcome"] == "measured"
    assert results["l4.typed_payload.tensor_pack_unpack.latency"]["outcome"] == "measured"
    assert results["l4.runtime.probe.latency"]["outcome"] == "measured"
    assert results["l4.session.lifecycle.latency"]["outcome"] == "measured"
    assert results["l4.transport.tcp.loopback.throughput"]["outcome"] == "measured"


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
        ({"operation": "transport_loopback", "probe_payload_bytes": 0}, "positive integer"),
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
    assert len(report["results"]) == 9


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
