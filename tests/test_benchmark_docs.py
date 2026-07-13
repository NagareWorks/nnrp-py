from pathlib import Path

BENCHMARK_DOC = Path(__file__).resolve().parents[1] / "doc" / "benchmarks" / "rs-native-artifacts-migration.md"


def test_native_artifact_benchmark_doc_tracks_preview4_contract() -> None:
    document = BENCHMARK_DOC.read_text(encoding="utf-8")

    assert "The current Python package consumes `nnrp-rs` native artifact version `1.0.0-preview.4.1`." in document
    assert "ABI version `1.12.0`" in document
    assert "Transport-scoped native artifacts for TCP, QUIC, IPC, and WebSocket." in document
    assert "Preview4 Hot-Path Comparison" in document
