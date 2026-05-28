from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_cffi_api.py"
_SPEC = spec_from_file_location("build_cffi_api", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

build_cffi_api = _MODULE.build_cffi_api
build_cffi_api_main = _MODULE.main


class FakeFFI:
    def __init__(self) -> None:
        self.cdefs: list[str] = []
        self.sources: list[tuple[str, str]] = []

    def cdef(self, source: str) -> None:
        self.cdefs.append(source)

    def set_source(self, module_name: str, source: str) -> None:
        self.sources.append((module_name, source))

    def compile(self, *, tmpdir: str, verbose: bool) -> str:
        assert verbose is False
        built = Path(tmpdir) / "nnrp" / "_nnrp_cffi_api_submit_result.cp311-test.pyd"
        built.parent.mkdir(parents=True, exist_ok=True)
        built.write_bytes(b"compiled")
        return str(built)


def test_build_cffi_api_writes_platform_package_entry(tmp_path: Path, monkeypatch) -> None:
    fake_builders: list[FakeFFI] = []

    def fake_ffi_factory() -> FakeFFI:
        builder = FakeFFI()
        fake_builders.append(builder)
        return builder

    monkeypatch.setattr(_MODULE, "FFI", fake_ffi_factory)
    monkeypatch.setattr(_MODULE, "current_native_platform", lambda: SimpleNamespace(tag="windows-x86_64"))

    built = build_cffi_api(tmp_path / "out")

    assert built == tmp_path / "out" / "windows-x86_64" / "nnrp" / "_nnrp_cffi_api_submit_result.cp311-test.pyd"
    assert built.read_bytes() == b"compiled"
    assert fake_builders[0].sources[0][0] == "nnrp._nnrp_cffi_api_submit_result"


def test_build_cffi_api_cleans_previous_package_dir_and_copies_external_compile_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stale = tmp_path / "out" / "linux-x86_64" / "nnrp" / "stale.pyd"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")

    class ExternalBuildFFI(FakeFFI):
        def compile(self, *, tmpdir: str, verbose: bool) -> str:
            built = tmp_path / "external" / "_nnrp_cffi_api_submit_result.cp311-test.pyd"
            built.parent.mkdir()
            built.write_bytes(b"external")
            return str(built)

    monkeypatch.setattr(_MODULE, "FFI", ExternalBuildFFI)

    built = build_cffi_api(tmp_path / "out", artifact_tag="linux-x86_64", clean=True)

    assert not stale.exists()
    assert built.read_bytes() == b"external"


def test_build_cffi_api_cli_prints_built_path(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(_MODULE, "FFI", FakeFFI)
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_cffi_api.py", "--output", str(tmp_path / "out"), "--artifact-tag", "linux-x86_64"],
    )

    assert build_cffi_api_main() == 0

    assert "linux-x86_64" in capsys.readouterr().out
