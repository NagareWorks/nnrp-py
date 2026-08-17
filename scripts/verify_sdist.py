from __future__ import annotations

import argparse
import tarfile
from pathlib import Path, PurePosixPath

DEFAULT_MAX_BYTES = 5_000_000
REQUIRED_MEMBERS = frozenset({"LICENSE", "README.md", "pyproject.toml", "src/nnrp/__init__.py"})
REJECTED_PREFIXES = (
    ".git",
    ".uv-cache",
    ".venv",
    "artifacts",
    "build",
    "dist",
    "src/nnrp/native_artifacts",
)
REJECTED_SUFFIXES = (
    ".a",
    ".dll",
    ".dylib",
    ".exp",
    ".lib",
    ".o",
    ".obj",
    ".pdb",
    ".pyd",
    ".so",
)


def _relative_member(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"source distribution contains an unsafe member path: {name}")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if len(parts) < 2:
        return ""
    return PurePosixPath(*parts[1:]).as_posix()


def verify_sdist(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[str, ...]:
    if not path.is_file():
        raise ValueError(f"source distribution does not exist: {path}")
    if path.stat().st_size > max_bytes:
        raise ValueError(
            f"source distribution exceeds {max_bytes} bytes: {path.name} is {path.stat().st_size} bytes"
        )

    try:
        with tarfile.open(path, mode="r:gz") as archive:
            archive_members = archive.getmembers()
            invalid_types = sorted(member.name for member in archive_members if not (member.isfile() or member.isdir()))
            if invalid_types:
                raise ValueError(
                    f"source distribution contains forbidden archive member types: {', '.join(invalid_types)}"
                )
            members = tuple(filter(None, (_relative_member(member.name) for member in archive_members)))
    except (OSError, tarfile.TarError) as error:
        raise ValueError(f"source distribution is not a readable gzip tar archive: {path}") from error

    missing = sorted(REQUIRED_MEMBERS.difference(members))
    if missing:
        raise ValueError(f"source distribution is missing required members: {', '.join(missing)}")

    rejected = sorted(
        member
        for member in members
        if any(member == prefix or member.startswith(f"{prefix}/") for prefix in REJECTED_PREFIXES)
        or member.lower().endswith(REJECTED_SUFFIXES)
    )
    if rejected:
        raise ValueError(f"source distribution contains forbidden payloads: {', '.join(rejected)}")

    return members


def resolve_sdist(dist: Path) -> Path:
    candidates = sorted(dist.glob("*.tar.gz"))
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one source distribution in {dist}, found {len(candidates)}")
    return candidates[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the NNRP Python source distribution boundary.")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = args.sdist or resolve_sdist(args.dist)
    members = verify_sdist(path, max_bytes=args.max_bytes)
    print(f"verified {path}: {len(members)} members, {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
