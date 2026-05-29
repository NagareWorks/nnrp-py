from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "write_cffi_api_build_project.py"
_SPEC = spec_from_file_location("write_cffi_api_build_project", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

write_cffi_api_build_project = _MODULE.write_cffi_api_build_project
write_cffi_api_build_project_main = _MODULE.main


def test_write_cffi_api_build_project_writes_abi3_cffi_package(tmp_path: Path) -> None:
    output = tmp_path / "project"

    write_cffi_api_build_project(output, package_version="1.0.0", abi3_python_tag="cp312")

    assert (output / "nnrp" / "__init__.py").read_text(encoding="utf-8") == ""
    assert 'name = "nnrp-py-cffi-api"' in (output / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.0.0"' in (output / "pyproject.toml").read_text(encoding="utf-8")
    assert "py_limited_api = cp312" in (output / "setup.cfg").read_text(encoding="utf-8")
    assert 'cffi_modules=["nnrp_cffi_build.py:ffi"]' in (output / "setup.py").read_text(encoding="utf-8")
    cffi_build = (output / "nnrp_cffi_build.py").read_text(encoding="utf-8")
    assert "ffi.set_source(" in cffi_build
    assert "py_limited_api=True" in cffi_build


def test_write_cffi_api_build_project_cli(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "project"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "write_cffi_api_build_project.py",
            "--output",
            str(output),
            "--package-version",
            "1.0.0",
            "--abi3-python-tag",
            "cp313",
        ],
    )

    assert write_cffi_api_build_project_main() == 0

    assert "py_limited_api = cp313" in (output / "setup.cfg").read_text(encoding="utf-8")
