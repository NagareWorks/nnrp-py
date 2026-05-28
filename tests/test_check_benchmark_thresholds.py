from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_benchmark_thresholds.py"
SPEC = importlib.util.spec_from_file_location("check_benchmark_thresholds", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
check_benchmark_thresholds = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_benchmark_thresholds)


def test_parse_args_reads_required_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", [str(SCRIPT_PATH), "--results", "results.json", "--thresholds", "limits.json"])

    args = check_benchmark_thresholds.parse_args()

    assert args.results == "results.json"
    assert args.thresholds == "limits.json"


def test_load_json_object_requires_object(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="benchmark results must be a JSON object"):
        check_benchmark_thresholds.load_json_object(path, label="benchmark results")


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ({}, "results list"),
        ({"results": ["bad"]}, "result entries must be JSON objects"),
        ({"results": [{"outcome": "measured"}]}, "non-empty id"),
    ],
)
def test_load_results_by_id_rejects_invalid_shapes(document: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        check_benchmark_thresholds.load_results_by_id(document)


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ({}, "thresholds list"),
        ({"thresholds": ["bad"]}, "threshold entries must be JSON objects"),
    ],
)
def test_load_threshold_cases_rejects_invalid_shapes(document: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        check_benchmark_thresholds.load_threshold_cases(document)


def test_evaluate_thresholds_passes_min_max_and_allows_skip() -> None:
    results_by_id = check_benchmark_thresholds.load_results_by_id(
        {
            "results": [
                {
                    "id": "native.throughput",
                    "outcome": "measured",
                    "metrics": {"throughput_ops_per_sec": 120.0, "peak_memory_bytes": 64},
                },
                {"id": "native.optional", "outcome": "skip"},
            ]
        }
    )
    threshold_cases = check_benchmark_thresholds.load_threshold_cases(
        {
            "thresholds": [
                {
                    "id": "native.throughput",
                    "min": {"throughput_ops_per_sec": 100.0},
                    "max": {"peak_memory_bytes": 128},
                },
                {"id": "native.optional", "allow_skip": True, "min": {"throughput_ops_per_sec": 1}},
            ]
        }
    )

    assert check_benchmark_thresholds.evaluate_thresholds(results_by_id, threshold_cases) == []


def test_evaluate_thresholds_reports_non_measured_and_missing_metrics() -> None:
    results_by_id = check_benchmark_thresholds.load_results_by_id(
        {
            "results": [
                {"id": "native.skip", "outcome": "skip"},
                {"id": "native.no_metrics", "outcome": "measured"},
            ]
        }
    )

    failures = check_benchmark_thresholds.evaluate_thresholds(
        results_by_id,
        [
            {"id": "native.skip", "min": {"ops": 1}},
            {"id": "native.no_metrics", "min": {"ops": 1}},
        ],
    )

    assert "native.skip: expected measured outcome, got 'skip'" in failures
    assert "native.no_metrics: measured result does not contain metrics" in failures


def test_evaluate_thresholds_reports_metric_failures() -> None:
    results_by_id = check_benchmark_thresholds.load_results_by_id(
        {
            "results": [
                {
                    "id": "native.throughput",
                    "outcome": "measured",
                    "metrics": {"throughput_ops_per_sec": 80.0, "peak_memory_bytes": 256},
                }
            ]
        }
    )

    failures = check_benchmark_thresholds.evaluate_thresholds(
        results_by_id,
        [
            {
                "id": "native.throughput",
                "min": {"throughput_ops_per_sec": 100.0, "cpu_percent": 1.0},
                "max": {"peak_memory_bytes": 128},
            },
            {"id": "native.missing", "min": {"throughput_ops_per_sec": 1}},
        ],
    )

    assert "native.throughput: throughput_ops_per_sec=80 is below minimum 100" in failures
    assert "native.throughput: metric 'cpu_percent' is missing or non-numeric" in failures
    assert "native.throughput: peak_memory_bytes=256 is above maximum 128" in failures
    assert "native.missing: missing benchmark result" in failures


@pytest.mark.parametrize(
    ("case", "match"),
    [
        ({}, "must be a non-empty string"),
        ({"id": "native.throughput", "min": []}, "field 'min' must be an object"),
        ({"id": "native.throughput", "min": {"": 1}}, "invalid metric name"),
        ({"id": "native.throughput", "min": {"ops": "fast"}}, "must be numeric"),
    ],
)
def test_evaluate_thresholds_rejects_invalid_threshold_shapes(case: dict[str, object], match: str) -> None:
    results_by_id = {
        "native.throughput": {
            "id": "native.throughput",
            "outcome": "measured",
            "metrics": {"ops": 1},
        }
    }
    with pytest.raises(ValueError, match=match):
        check_benchmark_thresholds.evaluate_thresholds(results_by_id, [case])


def test_main_fails_when_thresholds_do_not_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    results_path = tmp_path / "results.json"
    thresholds_path = tmp_path / "thresholds.json"
    results_path.write_text(
        json.dumps({"results": [{"id": "native.throughput", "outcome": "measured", "metrics": {"ops": 1}}]}),
        encoding="utf-8",
    )
    thresholds_path.write_text(
        json.dumps({"thresholds": [{"id": "native.throughput", "min": {"ops": 2}}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        check_benchmark_thresholds,
        "parse_args",
        lambda: argparse.Namespace(results=str(results_path), thresholds=str(thresholds_path)),
    )

    assert check_benchmark_thresholds.main() == 1
    assert "Benchmark threshold failures" in capsys.readouterr().out


def test_main_passes_when_thresholds_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    results_path = tmp_path / "results.json"
    thresholds_path = tmp_path / "thresholds.json"
    results_path.write_text(
        json.dumps({"results": [{"id": "native.throughput", "outcome": "measured", "metrics": {"ops": 3}}]}),
        encoding="utf-8",
    )
    thresholds_path.write_text(
        json.dumps({"thresholds": [{"id": "native.throughput", "min": {"ops": 2}}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        check_benchmark_thresholds,
        "parse_args",
        lambda: argparse.Namespace(results=str(results_path), thresholds=str(thresholds_path)),
    )

    assert check_benchmark_thresholds.main() == 0
    assert "Benchmark thresholds passed." in capsys.readouterr().out
