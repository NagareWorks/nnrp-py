from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TRANSPORTS = ("tcp", "quic", "ipc", "websocket")


def verify_run(repo: str, run_id: str, expected_commit: str) -> None:
    if not run_id.isdigit():
        raise ValueError("workflow run id must contain only decimal digits")
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        raise ValueError("workflow commit must be an exact lowercase 40-character hash")
    completed = subprocess.run(
        ["gh", "run", "view", run_id, "--repo", repo, "--json", "headSha,status,conclusion"],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(completed.stdout)
    if metadata.get("headSha") != expected_commit:
        raise ValueError(
            f"nnrp-rs workflow run {run_id} belongs to {metadata.get('headSha')!r}, not {expected_commit}"
        )
    if metadata.get("status") != "completed" or metadata.get("conclusion") != "success":
        raise ValueError(f"nnrp-rs workflow run {run_id} is not a completed success")


def read_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ValueError(f"malformed checksum entry: {line!r}")
        digest, name = match.groups()
        checksums[name] = digest
    return checksums


def stage_artifacts(source: Path, output: Path, version: str) -> list[Path]:
    checksum_matches = list(source.rglob("SHA256SUMS"))
    if len(checksum_matches) != 1:
        raise FileNotFoundError(f"expected one SHA256SUMS, found {len(checksum_matches)}")
    checksums = read_checksums(checksum_matches[0])
    selected: list[Path] = []
    for transport in TRANSPORTS:
        matches = list(source.rglob(f"nnrp-ffi-transport-{transport}-native-*-{version}.zip"))
        if not matches:
            raise FileNotFoundError(f"workflow artifact contains no {transport} native archives")
        selected.extend(matches)

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    staged: list[Path] = []
    for archive in selected:
        expected = checksums.get(archive.name)
        if expected is None:
            raise ValueError(f"SHA256SUMS does not contain {archive.name}")
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"checksum mismatch for {archive.name}")
        target = output / archive.name
        shutil.copy2(archive, target)
        staged.append(target)
    return staged


def main() -> int:
    parser = argparse.ArgumentParser(description="Download a commit-pinned nnrp-rs release workflow artifact.")
    parser.add_argument("--repo", default="NagareWorks/nnrp-rs")
    parser.add_argument("--version", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    verify_run(args.repo, args.workflow_run_id, args.workflow_commit)
    with tempfile.TemporaryDirectory(prefix="nnrp-rs-workflow-") as temp_dir:
        source = Path(temp_dir)
        subprocess.run(
            [
                "gh",
                "run",
                "download",
                args.workflow_run_id,
                "--repo",
                args.repo,
                "--name",
                f"nnrp-rs-release-{args.version}",
                "--dir",
                str(source),
            ],
            check=True,
        )
        staged = stage_artifacts(source, args.output, args.version)
    for path in staged:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
