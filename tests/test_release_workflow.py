from pathlib import Path

RELEASE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def test_release_workflow_manual_ref_defaults_to_main() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "description: Git ref to release when running manually" in workflow
    assert "default: main" in workflow
    assert "default: develop" not in workflow


def test_release_workflow_pins_preview4_rust_native_artifacts() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "default: 1.0.0-preview.4.10" in workflow
    assert "vars.NNRP_RS_NATIVE_VERSION || '1.0.0-preview.4.10'" in workflow
    assert "1.0.0-preview.3.8" not in workflow


def test_release_workflow_downloads_all_preview4_native_transport_artifacts() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    for transport in ("tcp", "quic", "ipc", "websocket"):
        assert f'--pattern "nnrp-ffi-transport-{transport}-native-*-${{version}}.zip"' in workflow


def test_release_workflow_rejects_non_preview4_native_artifact_shape() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "--require-preview4-native-artifacts" in workflow
    assert "--require-abi-version 3.0.0" in workflow


def test_release_workflow_smokes_preview4_ipc_and_websocket_artifacts() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "Smoke native IPC and WebSocket artifacts" in workflow
    assert "scripts/smoke_native_transport_artifacts.py" in workflow
    assert "--root src/nnrp/native_artifacts" in workflow
    assert "--transport ipc" in workflow
    assert "--transport websocket" in workflow


def test_release_workflow_runs_native_runtime_benchmark_thresholds() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "Run native runtime benchmark smoke thresholds" in workflow
    assert "doc/benchmarks/native-runtime-benchmark-plan.json" in workflow
    assert "scripts/check_benchmark_thresholds.py" in workflow
    assert "doc/benchmarks/native-runtime-smoke-thresholds.json" in workflow
    assert "python -m pip install --force-reinstall --no-deps \"$candidate_wheel\"" in workflow
    assert 'NNRP_BENCHMARK_RUST_ARTIFACT_VERSION="$NNRP_RS_NATIVE_VERSION"' in workflow
    assert 'NNRP_BENCHMARK_SDK_COMMIT="$GITHUB_SHA"' in workflow
    assert workflow.index("Verify packaged native artifacts") < workflow.index(
        "Run native runtime benchmark smoke thresholds"
    )


def test_release_workflow_creates_tag_only_after_all_release_validation() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("- name: Create git tag") == 1
    assert workflow.index("Run native runtime benchmark smoke thresholds") < workflow.index("Create git tag")
    assert workflow.index("Upload workflow artifacts") < workflow.index("Create git tag")
    assert workflow.index("Create git tag") < workflow.index("Publish GitHub release")


def test_release_workflow_rejects_polluted_source_distributions() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/verify_sdist.py --dist dist --max-bytes 5000000" in workflow
