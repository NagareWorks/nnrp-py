from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce benchmark metric thresholds from a result JSON file.")
    parser.add_argument("--results", required=True, help="Path to benchmark result JSON.")
    parser.add_argument("--thresholds", required=True, help="Path to benchmark threshold JSON.")
    return parser.parse_args()


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return document


def load_results_by_id(results_document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = results_document.get("results")
    if not isinstance(results, list):
        raise ValueError("benchmark results document must contain a results list")

    indexed: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("benchmark result entries must be JSON objects")
        result_id = result.get("id")
        if not isinstance(result_id, str) or not result_id:
            raise ValueError("benchmark result entries must contain a non-empty id")
        indexed[result_id] = result
    return indexed


def load_threshold_cases(threshold_document: dict[str, Any]) -> list[dict[str, Any]]:
    cases = threshold_document.get("thresholds")
    if not isinstance(cases, list):
        raise ValueError("benchmark threshold document must contain a thresholds list")
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("benchmark threshold entries must be JSON objects")
    return cases


def evaluate_thresholds(results_by_id: dict[str, dict[str, Any]], threshold_cases: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for case in threshold_cases:
        scenario_id = _required_string(case, "id")
        result = results_by_id.get(scenario_id)
        if result is None:
            failures.append(f"{scenario_id}: missing benchmark result")
            continue

        allow_skip = bool(case.get("allow_skip", False))
        outcome = result.get("outcome")
        if outcome != "measured":
            if allow_skip and outcome == "skip":
                continue
            failures.append(f"{scenario_id}: expected measured outcome, got {outcome!r}")
            continue

        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            failures.append(f"{scenario_id}: measured result does not contain metrics")
            continue

        for metric_name, minimum in _metric_bounds(case, "min").items():
            actual = _numeric_metric(metrics, metric_name, scenario_id, failures)
            if actual is not None and actual < minimum:
                failures.append(f"{scenario_id}: {metric_name}={actual:g} is below minimum {minimum:g}")

        for metric_name, maximum in _metric_bounds(case, "max").items():
            actual = _numeric_metric(metrics, metric_name, scenario_id, failures)
            if actual is not None and actual > maximum:
                failures.append(f"{scenario_id}: {metric_name}={actual:g} is above maximum {maximum:g}")

    return failures


def _required_string(document: dict[str, Any], field_name: str) -> str:
    value = document.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"benchmark threshold field '{field_name}' must be a non-empty string")
    return value


def _metric_bounds(case: dict[str, Any], field_name: str) -> dict[str, float]:
    raw_bounds = case.get(field_name, {})
    if not isinstance(raw_bounds, dict):
        raise ValueError(f"benchmark threshold field '{field_name}' must be an object when present")
    bounds: dict[str, float] = {}
    for metric_name, value in raw_bounds.items():
        if not isinstance(metric_name, str) or not metric_name:
            raise ValueError(f"benchmark threshold field '{field_name}' contains an invalid metric name")
        if not isinstance(value, int | float):
            raise ValueError(f"benchmark threshold for metric '{metric_name}' must be numeric")
        bounds[metric_name] = float(value)
    return bounds


def _numeric_metric(
    metrics: dict[str, Any],
    metric_name: str,
    scenario_id: str,
    failures: list[str],
) -> float | None:
    value = metrics.get(metric_name)
    if not isinstance(value, int | float):
        failures.append(f"{scenario_id}: metric {metric_name!r} is missing or non-numeric")
        return None
    return float(value)


def main() -> int:
    args = parse_args()
    results = load_json_object(Path(args.results), label="benchmark results")
    thresholds = load_json_object(Path(args.thresholds), label="benchmark thresholds")
    failures = evaluate_thresholds(load_results_by_id(results), load_threshold_cases(thresholds))
    if not failures:
        print("Benchmark thresholds passed.")
        return 0

    print("Benchmark threshold failures:")
    for failure in failures:
        print(f"- {failure}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
