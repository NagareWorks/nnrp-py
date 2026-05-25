"""Preview3 benchmark wrapper for suite-owned execution plans."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from nnrp.core.enums import HeaderFlags, MessageType, WireFormat
from nnrp.core.header import NnrpHeader

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


def _measure_microseconds(operation: Callable[[], None], iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - start) / 1_000)
    return samples


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


def _skip_result(scenario_id: str) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "outcome": "skip",
        "message": _DEFAULT_SKIP_MESSAGE,
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


def _non_negative_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value < 0:
        raise ValueError("benchmark workload warmup_iterations must be a non-negative integer")
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
}


if __name__ == "__main__":
    raise SystemExit(main())
