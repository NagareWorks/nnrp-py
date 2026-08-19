from pathlib import Path

RELEASE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def test_release_workflow_manual_ref_defaults_to_main() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "description: Git ref to release when running manually" in workflow
    assert "default: main" in workflow
    assert "default: develop" not in workflow


def test_release_workflow_pins_preview4_rust_native_artifacts() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "default: 1.0.0-preview.4.23" in workflow
    assert "vars.NNRP_RS_NATIVE_VERSION || '1.0.0-preview.4.23'" in workflow
    assert "1.0.0-preview.3.8" not in workflow


def test_release_workflow_downloads_all_preview4_native_transport_artifacts() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/download_nnrp_rs_workflow_artifacts.py" in workflow
    assert 'NNRP_RS_RELEASE_RUN_ID: "32009630987"' in workflow
    assert "NNRP_RS_SOURCE_COMMIT: 00074cf3c09002de940f011e229de729aa377e88" in workflow
    assert '--workflow-run-id "$NNRP_RS_RELEASE_RUN_ID"' in workflow
    assert '--workflow-commit "$NNRP_RS_SOURCE_COMMIT"' in workflow
    assert "gh release download" not in workflow


def test_release_workflow_rejects_non_preview4_native_artifact_shape() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "--require-preview4-native-artifacts" in workflow
    assert "--require-abi-version 4.4.0" in workflow
    assert "Run installed wheel native role E2E" in workflow
    assert '"$candidate_wheel"' in workflow
    assert "pytest-asyncio" in workflow
    assert "cryptography" in workflow
    assert (
        "cp tests/test_native_artifact_e2e.py "
        "artifacts/installed-wheel-e2e-case/test_native_artifact_e2e.py"
    ) in workflow
    assert "NNRP_NATIVE_E2E=1 ../installed-wheel-e2e-venv/bin/python -m pytest" in workflow


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


def test_release_workflow_rejects_reused_identity_and_records_a_bom() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "Validate immutable release identity" in workflow
    assert "scripts/check_release_identity.py" in workflow
    assert "--expected-ref origin/main" in workflow
    assert 'arguments+=(--check-pypi)' in workflow
    assert "scripts/release_manifest.py build" in workflow
    assert "NNRP_CONFORMANCE_SOURCE_COMMIT: 4f1632d9deb924ce8d90d4f7212dc2310d936320" in workflow
    assert "NNRP_DOC_SOURCE_COMMIT: 4319692b4c0a697fe5d360e55bafa2b83f5bbb3d" in workflow
    assert "artifacts/release/release-manifest.json" in workflow
    assert "prerelease: ${{ steps.version.outputs.is_prerelease }}" in workflow


def test_release_workflow_verifies_public_pypi_bytes_and_native_runtime() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "Verify public PyPI release from a fresh environment" in workflow
    assert "--index-url https://pypi.org/simple" in workflow
    assert "scripts/release_manifest.py verify" in workflow
    assert "artifacts/public-pypi-venv" in workflow
    assert "NNRP_NATIVE_E2E=1 ../public-pypi-venv/bin/python -m pytest" in workflow
    assert workflow.index("Publish to PyPI with API token") < workflow.index(
        "Verify public PyPI release from a fresh environment"
    )
    assert workflow.index("Verify public PyPI release from a fresh environment") < workflow.index(
        "Publish GitHub release"
    )


def test_release_workflow_rejects_polluted_source_distributions() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/verify_sdist.py --dist dist --max-bytes 5000000" in workflow


def test_source_distribution_excludes_local_build_caches() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert '"/.venv*"' in pyproject
    assert '"/.uv-cache"' in pyproject
