#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from cffi import FFI

from nnrp.native import current_native_platform
from nnrp.tools.benchmark import _CFFI_API_SUBMIT_RESULT_SOURCE

MODULE_NAME = "nnrp._nnrp_cffi_api_submit_result"

_CDEF = """
typedef struct NnrpPyCompactResult {
    unsigned int status_code;
    unsigned int error_family;
    unsigned int protocol_error_code;
    unsigned int detail_code;
    unsigned char has_result;
    unsigned int event_kind;
    unsigned int result_state;
    unsigned long long operation_id;
    unsigned int frame_id;
    size_t payload_len;
} NnrpPyCompactResult;

int nnrp_py_client_submit_result_compact(
    const char *library_path,
    unsigned int session_kind,
    unsigned long long session_id,
    unsigned int session_generation,
    unsigned int session_flags,
    unsigned long long operation_id,
    unsigned int frame_id,
    const unsigned char *payload,
    size_t payload_len,
    NnrpPyCompactResult *out_result
);
"""


def build_cffi_api(output: Path, *, artifact_tag: str | None = None, clean: bool = False) -> Path:
    tag = artifact_tag or current_native_platform().tag
    target_root = output / tag
    package_dir = target_root / "nnrp"
    if clean and package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    builder = FFI()
    builder.cdef(_CDEF)
    builder.set_source(MODULE_NAME, _CFFI_API_SUBMIT_RESULT_SOURCE)
    built = Path(builder.compile(tmpdir=str(target_root), verbose=False))

    target = package_dir / built.name
    if built.resolve() != target.resolve():
        shutil.copy2(built, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the packaged NNRP native cffi API fast-path module.")
    parser.add_argument("--output", type=Path, default=Path("artifacts") / "cffi-api")
    parser.add_argument("--artifact-tag", default=None)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    print(build_cffi_api(args.output, artifact_tag=args.artifact_tag, clean=args.clean))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
