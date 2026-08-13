from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "download_nnrp_rs_workflow_artifacts.py"


def load_module():
    spec = importlib.util.spec_from_file_location("download_nnrp_rs_workflow_artifacts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_verify_run_requires_the_exact_successful_commit() -> None:
    module = load_module()
    completed = type(
        "Completed",
        (),
        {
            "stdout": json.dumps(
                {
                    "headSha": "295f5b65ac71885b5c1b54d927b2595005038481",
                    "status": "completed",
                    "conclusion": "success",
                }
            )
        },
    )()
    with patch.object(module.subprocess, "run", return_value=completed):
        module.verify_run(
            "NagareWorks/nnrp-rs",
            "31666415612",
            "295f5b65ac71885b5c1b54d927b2595005038481",
        )


@pytest.mark.parametrize(
    ("run_id", "commit", "message"),
    [
        ("run-1", "a" * 40, "decimal digits"),
        ("123", "A" * 40, "lowercase 40-character hash"),
    ],
)
def test_verify_run_rejects_non_exact_identifiers(run_id: str, commit: str, message: str) -> None:
    module = load_module()
    with pytest.raises(ValueError, match=message):
        module.verify_run("NagareWorks/nnrp-rs", run_id, commit)


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            {"headSha": "b" * 40, "status": "completed", "conclusion": "success"},
            "belongs to",
        ),
        (
            {"headSha": "a" * 40, "status": "in_progress", "conclusion": None},
            "not a completed success",
        ),
    ],
)
def test_verify_run_rejects_wrong_or_incomplete_workflow(metadata: dict[str, object], message: str) -> None:
    module = load_module()
    completed = type("Completed", (), {"stdout": json.dumps(metadata)})()
    with patch.object(module.subprocess, "run", return_value=completed), pytest.raises(ValueError, match=message):
        module.verify_run("NagareWorks/nnrp-rs", "123", "a" * 40)


def test_read_checksums_rejects_malformed_lines(tmp_path: Path) -> None:
    module = load_module()
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text("not-a-checksum\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed checksum"):
        module.read_checksums(checksums)


def test_stage_artifacts_requires_every_transport_and_valid_checksums(tmp_path: Path) -> None:
    module = load_module()
    version = "1.0.0-preview.4.22"
    checksum_lines: list[str] = []
    for transport in module.TRANSPORTS:
        archive = tmp_path / f"nnrp-ffi-transport-{transport}-native-linux-x86_64-{version}.zip"
        with zipfile.ZipFile(archive, "w") as payload:
            payload.writestr("manifest.json", "{}")
        checksum_lines.append(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}")
    (tmp_path / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    staged = module.stage_artifacts(tmp_path, tmp_path / "output", version)

    assert len(staged) == 4
    assert all(path.is_file() for path in staged)


def test_stage_artifacts_rejects_missing_transport(tmp_path: Path) -> None:
    module = load_module()
    (tmp_path / "SHA256SUMS").write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="tcp"):
        module.stage_artifacts(tmp_path, tmp_path / "output", "1.0.0-preview.4.22")


def test_stage_artifacts_rejects_missing_or_wrong_checksum(tmp_path: Path) -> None:
    module = load_module()
    version = "1.0.0-preview.4.22"
    archives: list[Path] = []
    for transport in module.TRANSPORTS:
        archive = tmp_path / f"nnrp-ffi-transport-{transport}-native-linux-x86_64-{version}.zip"
        archive.write_bytes(transport.encode())
        archives.append(archive)

    checksum_path = tmp_path / "SHA256SUMS"
    checksum_path.write_text(
        "\n".join(
            f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}" for archive in archives[1:]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not contain"):
        module.stage_artifacts(tmp_path, tmp_path / "output", version)

    checksum_path.write_text(
        "\n".join(f"{'0' * 64}  {archive.name}" for archive in archives),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        module.stage_artifacts(tmp_path, tmp_path / "output", version)


def test_main_downloads_verified_workflow_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    version = "1.0.0-preview.4.22"
    output = tmp_path / "staged"
    verified: list[tuple[str, str, str]] = []

    def fake_verify(repo: str, run_id: str, commit: str) -> None:
        verified.append((repo, run_id, commit))

    def fake_run(arguments: list[str], **_kwargs: object) -> object:
        download_dir = Path(arguments[arguments.index("--dir") + 1])
        lines: list[str] = []
        for transport in module.TRANSPORTS:
            archive = download_dir / f"nnrp-ffi-transport-{transport}-native-linux-x86_64-{version}.zip"
            archive.write_bytes(transport.encode())
            lines.append(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}")
        (download_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return type("Completed", (), {"stdout": ""})()

    monkeypatch.setattr(module, "verify_run", fake_verify)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--version",
            version,
            "--workflow-run-id",
            "31666415612",
            "--workflow-commit",
            "a" * 40,
            "--output",
            str(output),
        ],
    )

    assert module.main() == 0
    assert verified == [("NagareWorks/nnrp-rs", "31666415612", "a" * 40)]
    assert len(list(output.glob("*.zip"))) == 4
