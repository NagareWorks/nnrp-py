from __future__ import annotations

from types import SimpleNamespace

import nnrp._nnrp_cffi_api_submit_result as cffi_api


class FakeNative:
    def __init__(self, *, status_code: int = 0) -> None:
        self.status_code = status_code
        self.requests = []

    def nnrp_client_submit_result_compact(self, request, out_result):
        self.requests.append(request)
        status = cffi_api.ffi.new("NnrpFfiStatus *")
        status.status_code = self.status_code
        status.error_family = 3
        status.protocol_error_code = 4
        status.detail_code = 5
        if self.status_code == 0:
            out_result.status.status_code = 0
            out_result.has_result = 1
            out_result.event_kind = 7
            out_result.result_state = 8
            out_result.operation_id = request.operation_id
            out_result.frame_id = request.frame_id
            out_result.payload.len = request.result_payload.len
        return status[0]


def test_cffi_abi_submit_result_wrapper_fills_compact_result(monkeypatch) -> None:
    native = FakeNative()
    monkeypatch.setattr(cffi_api, "_load_native_library", lambda _path: native)
    payload = b"payload"
    out_result = cffi_api.ffi.new("NnrpPyCompactResult *")

    wrapper_status = cffi_api.lib.nnrp_py_client_submit_result_compact_v2(
        b"native",
        2,
        11,
        3,
        0,
        99,
        7,
        cffi_api.ffi.from_buffer(payload),
        len(payload),
        4,
        out_result,
    )

    assert wrapper_status == 0
    assert out_result.status_code == 0
    assert out_result.has_result == 1
    assert out_result.event_kind == 7
    assert out_result.result_state == 8
    assert out_result.operation_id == 99
    assert out_result.frame_id == 7
    assert out_result.payload_len == len(payload)
    request = native.requests[0]
    assert request.session.kind == 2
    assert request.session.id == 11
    assert request.session.generation == 3
    assert request.max_events == 4


def test_cffi_abi_submit_result_wrapper_preserves_native_status(monkeypatch) -> None:
    monkeypatch.setattr(cffi_api, "_load_native_library", lambda _path: FakeNative(status_code=9))
    out_result = cffi_api.ffi.new("NnrpPyCompactResult *")

    wrapper_status = cffi_api.lib.nnrp_py_client_submit_result_compact(
        "native",
        2,
        11,
        3,
        0,
        99,
        7,
        cffi_api.ffi.from_buffer(b"payload"),
        7,
        out_result,
    )

    assert wrapper_status == 0
    assert out_result.status_code == 9
    assert out_result.error_family == 3
    assert out_result.protocol_error_code == 4
    assert out_result.detail_code == 5
    assert out_result.has_result == 0


def test_cffi_abi_submit_result_wrapper_rejects_missing_inputs(monkeypatch) -> None:
    monkeypatch.setattr(cffi_api, "_load_native_library", lambda _path: SimpleNamespace())

    assert (
        cffi_api.lib.nnrp_py_client_submit_result_compact(
            b"native",
            2,
            11,
            3,
            0,
            99,
            7,
            cffi_api.ffi.from_buffer(b"payload"),
            7,
            cffi_api.ffi.NULL,
        )
        == -1
    )
    out_result = cffi_api.ffi.new("NnrpPyCompactResult *")
    assert (
        cffi_api.lib.nnrp_py_client_submit_result_compact(
            b"native",
            2,
            11,
            3,
            0,
            99,
            7,
            cffi_api.ffi.from_buffer(b"payload"),
            7,
            out_result,
        )
        == -2
    )
