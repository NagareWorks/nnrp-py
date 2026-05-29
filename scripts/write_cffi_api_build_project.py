#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_cffi_api import _CDEF, _CFFI_API_SUBMIT_RESULT_SOURCE


def write_cffi_api_build_project(
    output: Path,
    *,
    package_version: str = "0.0.0",
    abi3_python_tag: str = "cp311",
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "nnrp").mkdir(exist_ok=True)
    (output / "nnrp" / "__init__.py").write_text("", encoding="utf-8")
    (output / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [build-system]
            requires = ["setuptools>=82.0.1", "wheel", "cffi>=2.0.0"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "nnrp-py-cffi-api"
            version = "{package_version}"
            requires-python = ">=3.11"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (output / "setup.cfg").write_text(
        textwrap.dedent(
            f"""
            [bdist_wheel]
            py_limited_api = {abi3_python_tag}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (output / "setup.py").write_text(
        textwrap.dedent(
            """
            from setuptools import setup

            setup(
                packages=["nnrp"],
                cffi_modules=["nnrp_cffi_build.py:ffi"],
                zip_safe=False,
            )
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (output / "nnrp_cffi_build.py").write_text(
        "from cffi import FFI\n\n"
        "ffi = FFI()\n"
        f"ffi.cdef({_CDEF!r})\n"
        "ffi.set_source(\n"
        "    'nnrp._nnrp_cffi_api_submit_result',\n"
        f"    {_CFFI_API_SUBMIT_RESULT_SOURCE!r},\n"
        "    py_limited_api=True,\n"
        ")\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a temporary package for cibuildwheel cffi API builds.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-version", default="0.0.0")
    parser.add_argument(
        "--abi3-python-tag",
        default="cp311",
        help="Minimum abi3 Python tag to advertise from built cffi API wheels.",
    )
    args = parser.parse_args()

    write_cffi_api_build_project(
        args.output,
        package_version=args.package_version,
        abi3_python_tag=args.abi3_python_tag,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
