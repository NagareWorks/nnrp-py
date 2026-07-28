import json
from pathlib import Path

BENCHMARK_DOC = Path(__file__).resolve().parents[1] / "doc" / "benchmarks" / "rs-native-artifacts-migration.md"
BENCHMARK_THRESHOLDS = (
    Path(__file__).resolve().parents[1] / "doc" / "benchmarks" / "native-runtime-smoke-thresholds.json"
)


def test_native_artifact_benchmark_doc_tracks_preview4_contract() -> None:
    document = BENCHMARK_DOC.read_text(encoding="utf-8")

    assert "The current Python package consumes `nnrp-rs` native artifact version `1.0.0-preview.4.17`." in document
    assert "ABI version `4.1.x`" in document
    assert "Transport-scoped native artifacts for TCP, QUIC, IPC, and WebSocket." in document
    assert "ABI 4 Release Validation" in document


def test_native_artifact_benchmark_thresholds_require_real_transport_loopbacks() -> None:
    document = json.loads(BENCHMARK_THRESHOLDS.read_text(encoding="utf-8"))
    thresholds = {case["id"]: case for case in document["thresholds"]}

    for transport in ("ipc", "websocket"):
        case = thresholds[f"l4.transport.{transport}.loopback.throughput"]
        assert case["min"]["native_ffi_calls_per_op"] == 4
        assert case["max"]["native_ffi_calls_per_op"] == 4
