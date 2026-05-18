from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import re
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PACKAGE_INIT_PATH = REPO_ROOT / "src" / "nnrp" / "__init__.py"


def read_release_version() -> str:
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return data["project"]["version"]


def build_package_version(release_version: str, version_date: str | None, run_number: str | None) -> str:
    if not run_number:
        return release_version

    normalized_date = version_date or dt.datetime.utcnow().strftime("%Y%m%d")
    normalized_run = str(int(run_number))
    return f"{release_version}.dev{normalized_date}{normalized_run}"


def write_outputs(values: dict[str, str], github_output: bool) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    if github_output:
        output_path = os.environ["GITHUB_OUTPUT"]
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return

    print("\n".join(lines))


def replace_once(path: pathlib.Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"Expected exactly one replacement in {path}")
    path.write_text(updated, encoding="utf-8")


def cmd_show(args: argparse.Namespace) -> None:
    release_version = read_release_version()
    package_version = build_package_version(release_version, args.version_date, args.run_number)
    write_outputs(
        {
            "release_version": release_version,
            "package_version": package_version,
            "tag_name": f"v{release_version}",
        },
        github_output=args.github_output,
    )


def cmd_apply(args: argparse.Namespace) -> None:
    package_version = args.package_version
    replace_once(PYPROJECT_PATH, r'^version = "[^"]+"$', f'version = "{package_version}"')
    replace_once(
        PACKAGE_INIT_PATH,
        r'^__version__ = "[^"]+"$',
        f'__version__ = "{package_version}"',
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve and apply Python package versions for CI releases.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--version-date", default=os.environ.get("VERSION_DATE"))
    show_parser.add_argument("--run-number", default=os.environ.get("GITHUB_RUN_NUMBER"))
    show_parser.add_argument("--github-output", action="store_true")
    show_parser.set_defaults(func=cmd_show)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--package-version", required=True)
    apply_parser.set_defaults(func=cmd_apply)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
