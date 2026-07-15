from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_sdist.py"
_SPEC = importlib.util.spec_from_file_location("verify_sdist", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

resolve_sdist = _MODULE.resolve_sdist
verify_sdist = _MODULE.verify_sdist
verify_sdist_main = _MODULE.main

_ROOT = "nnrp_py-1.0.0rc4.post4"
_REQUIRED = {
    "LICENSE": b"license",
    "README.md": b"readme",
    "pyproject.toml": b"[project]",
    "src/nnrp/__init__.py": b'__version__ = "1.0.0rc4.post4"',
}


def _write_sdist(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, mode="w:gz") as archive:
        for relative_name, payload in members.items():
            info = tarfile.TarInfo(f"{_ROOT}/{relative_name}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def _write_special_member_sdist(path: Path, member: tarfile.TarInfo) -> Path:
    with tarfile.open(path, mode="w:gz") as archive:
        for relative_name, payload in _REQUIRED.items():
            info = tarfile.TarInfo(f"{_ROOT}/{relative_name}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        archive.addfile(member)
    return path


def test_verify_sdist_accepts_source_only_payload(tmp_path: Path) -> None:
    sdist = _write_sdist(tmp_path / "nnrp_py.tar.gz", _REQUIRED | {"tests/test_version.py": b"pass"})

    members = verify_sdist(sdist)

    assert "src/nnrp/__init__.py" in members


@pytest.mark.parametrize(
    "member",
    [
        "artifacts/native-downloads/nnrp.zip",
        "src/nnrp/native_artifacts/windows-x86_64/tcp/nnrp_ffi.dll",
        ".venv/Lib/site-packages/example.py",
    ],
)
def test_verify_sdist_rejects_release_and_native_payloads(tmp_path: Path, member: str) -> None:
    sdist = _write_sdist(tmp_path / "nnrp_py.tar.gz", _REQUIRED | {member: b"payload"})

    with pytest.raises(ValueError, match="forbidden payloads"):
        verify_sdist(sdist)


def test_verify_sdist_rejects_oversized_archive(tmp_path: Path) -> None:
    sdist = _write_sdist(tmp_path / "nnrp_py.tar.gz", _REQUIRED)

    with pytest.raises(ValueError, match="exceeds 1 bytes"):
        verify_sdist(sdist, max_bytes=1)


def test_verify_sdist_rejects_missing_archive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        verify_sdist(tmp_path / "missing.tar.gz")


@pytest.mark.parametrize("member", [f"{_ROOT}/../artifacts/x", "/absolute/x"])
def test_verify_sdist_rejects_unsafe_member_paths(tmp_path: Path, member: str) -> None:
    sdist = _write_special_member_sdist(tmp_path / "nnrp_py.tar.gz", tarfile.TarInfo(member))

    with pytest.raises(ValueError, match="unsafe member path"):
        verify_sdist(sdist)


def test_verify_sdist_rejects_archive_links(tmp_path: Path) -> None:
    link = tarfile.TarInfo(f"{_ROOT}/src/nnrp/linked.py")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../artifacts/payload.py"
    sdist = _write_special_member_sdist(tmp_path / "nnrp_py.tar.gz", link)

    with pytest.raises(ValueError, match="forbidden archive member types"):
        verify_sdist(sdist)


def test_verify_sdist_normalizes_corrupt_archive_errors(tmp_path: Path) -> None:
    sdist = tmp_path / "nnrp_py.tar.gz"
    sdist.write_bytes(b"not-a-gzip-tar")

    with pytest.raises(ValueError, match="not a readable gzip tar archive"):
        verify_sdist(sdist)


def test_verify_sdist_rejects_missing_required_members(tmp_path: Path) -> None:
    sdist = _write_sdist(tmp_path / "nnrp_py.tar.gz", {"pyproject.toml": b"[project]"})

    with pytest.raises(ValueError, match="missing required members"):
        verify_sdist(sdist)


def test_resolve_sdist_requires_exactly_one_archive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="found 0"):
        resolve_sdist(tmp_path)

    _write_sdist(tmp_path / "one.tar.gz", _REQUIRED)
    assert resolve_sdist(tmp_path).name == "one.tar.gz"

    _write_sdist(tmp_path / "two.tar.gz", _REQUIRED)
    with pytest.raises(ValueError, match="found 2"):
        resolve_sdist(tmp_path)


def test_verify_sdist_cli_accepts_explicit_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sdist = _write_sdist(tmp_path / "nnrp_py.tar.gz", _REQUIRED)
    monkeypatch.setattr(sys, "argv", ["verify_sdist.py", "--sdist", str(sdist), "--max-bytes", "5000000"])

    assert verify_sdist_main() == 0
    assert f"verified {sdist}" in capsys.readouterr().out
