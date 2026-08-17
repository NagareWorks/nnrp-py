from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_manifest.py"
SPEC = importlib.util.spec_from_file_location("release_manifest", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
release_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_manifest)


def build_args(tmp_path: Path) -> argparse.Namespace:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "nnrp_py-1.0.0rc4.post15-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "nnrp_py-1.0.0rc4.post15.tar.gz").write_bytes(b"sdist")
    (dist / "ignored.txt").write_text("ignored", encoding="utf-8")
    return argparse.Namespace(
        dist=dist,
        output=tmp_path / "release" / "manifest.json",
        package="nnrp-py",
        version="1.0.0rc4.post15",
        tag="v1.0.0-preview.4.post15",
        repository="NagareWorks/nnrp-py",
        source_commit="a" * 40,
        rust_version="1.0.0-preview.4.23",
        rust_source_commit="b" * 40,
        rust_release_run_id="32009630987",
        conformance_commit="c" * 40,
        documentation_commit="d" * 40,
    )


def test_build_manifest_records_provenance_and_distribution_hashes(tmp_path: Path) -> None:
    args = build_args(tmp_path)

    manifest = release_manifest.build_manifest(args)

    assert manifest["package"] == {
        "name": "nnrp-py",
        "version": "1.0.0rc4.post15",
        "tag": "v1.0.0-preview.4.post15",
    }
    assert manifest["source"]["rust"]["version"] == "1.0.0-preview.4.23"
    assert [item["name"] for item in manifest["distributions"]] == [
        "nnrp_py-1.0.0rc4.post15-py3-none-any.whl",
        "nnrp_py-1.0.0rc4.post15.tar.gz",
    ]
    assert manifest["distributions"][0]["sha256"] == hashlib.sha256(b"wheel").hexdigest()
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "release-manifest-v1.schema.json"
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(manifest)


def test_manifest_write_and_distribution_verification(tmp_path: Path) -> None:
    args = build_args(tmp_path)
    release_manifest.write_manifest(args)
    wheel = args.dist / "nnrp_py-1.0.0rc4.post15-py3-none-any.whl"

    release_manifest.verify_distribution(args.output, wheel)

    wheel.write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not match"):
        release_manifest.verify_distribution(args.output, wheel)
    missing = args.dist / "missing.whl"
    missing.write_bytes(b"missing")
    with pytest.raises(ValueError, match="is absent"):
        release_manifest.verify_distribution(args.output, missing)


def test_build_manifest_requires_distributions(tmp_path: Path) -> None:
    args = build_args(tmp_path)
    for path in args.dist.iterdir():
        path.unlink()

    with pytest.raises(ValueError, match="no Python distributions"):
        release_manifest.build_manifest(args)


def test_main_builds_manifest_from_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = build_args(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_manifest.py",
            "build",
            "--dist",
            str(args.dist),
            "--output",
            str(args.output),
            "--package",
            args.package,
            "--version",
            args.version,
            "--tag",
            args.tag,
            "--repository",
            args.repository,
            "--source-commit",
            args.source_commit,
            "--rust-version",
            args.rust_version,
            "--rust-source-commit",
            args.rust_source_commit,
            "--rust-release-run-id",
            args.rust_release_run_id,
            "--conformance-commit",
            args.conformance_commit,
            "--documentation-commit",
            args.documentation_commit,
        ],
    )

    release_manifest.main()

    assert json.loads(args.output.read_text(encoding="utf-8"))["source"]["commit"] == "a" * 40
