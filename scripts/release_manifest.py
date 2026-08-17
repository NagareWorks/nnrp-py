from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "https://raw.githubusercontent.com/NagareWorks/nnrp-py/main/schemas/release-manifest-v1.schema.json"


def file_record(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    distributions = sorted(
        (path for path in args.dist.iterdir() if path.is_file() and path.name.endswith((".whl", ".tar.gz"))),
        key=lambda path: path.name,
    )
    if not distributions:
        raise ValueError(f"no Python distributions found under {args.dist}")
    return {
        "$schema": SCHEMA,
        "package": {"name": args.package, "version": args.version, "tag": args.tag},
        "source": {
            "repository": args.repository,
            "commit": args.source_commit,
            "rust": {
                "version": args.rust_version,
                "source_commit": args.rust_source_commit,
                "release_run_id": args.rust_release_run_id,
            },
            "conformance_commit": args.conformance_commit,
            "documentation_commit": args.documentation_commit,
        },
        "distributions": [file_record(path) for path in distributions],
    }


def write_manifest(args: argparse.Namespace) -> None:
    manifest = build_manifest(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_distribution(manifest_path: Path, distribution_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = next(
        (record for record in manifest.get("distributions", []) if record.get("name") == distribution_path.name),
        None,
    )
    if expected is None:
        raise ValueError(f"{distribution_path.name} is absent from {manifest_path}")
    actual = file_record(distribution_path)
    if actual != expected:
        raise ValueError(f"{distribution_path.name} does not match the release manifest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or verify the NNRP Python release manifest.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--dist", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--package", required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--tag", required=True)
    build.add_argument("--repository", required=True)
    build.add_argument("--source-commit", required=True)
    build.add_argument("--rust-version", required=True)
    build.add_argument("--rust-source-commit", required=True)
    build.add_argument("--rust-release-run-id", required=True)
    build.add_argument("--conformance-commit", required=True)
    build.add_argument("--documentation-commit", required=True)
    build.set_defaults(func=write_manifest)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--distribution", type=Path, required=True)
    verify.set_defaults(func=lambda args: verify_distribution(args.manifest, args.distribution))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
