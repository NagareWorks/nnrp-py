from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from cffi import FFI

ffi = FFI()
ffi.cdef(
    """
    typedef struct NnrpFfiStatus {
        unsigned int status_code;
        unsigned int error_family;
        unsigned int protocol_error_code;
        unsigned int detail_code;
    } NnrpFfiStatus;

    typedef struct NnrpHandle {
        unsigned int kind;
        unsigned long long id;
        unsigned int generation;
        unsigned int flags;
    } NnrpHandle;

    typedef struct NnrpBufferView {
        const unsigned char *ptr;
        size_t len;
    } NnrpBufferView;

    typedef struct NnrpFfiDiagnostic {
        NnrpFfiStatus status;
        unsigned long long related_connection_id;
        unsigned int related_session_id;
        unsigned long long related_operation_id;
        unsigned int related_frame_id;
    } NnrpFfiDiagnostic;

    typedef struct NnrpClientSubmitResultRequest {
        NnrpHandle session;
        unsigned long long operation_id;
        unsigned int frame_id;
        NnrpBufferView submit_payload;
        NnrpBufferView result_payload;
        size_t max_events;
    } NnrpClientSubmitResultRequest;

    typedef struct NnrpCompactResult {
        NnrpFfiStatus status;
        unsigned char has_result;
        unsigned int event_kind;
        unsigned int result_state;
        NnrpHandle operation;
        unsigned long long operation_id;
        unsigned int frame_id;
        NnrpBufferView payload;
        NnrpFfiDiagnostic diagnostic;
    } NnrpCompactResult;

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

    NnrpFfiStatus nnrp_client_submit_result_compact(
        NnrpClientSubmitResultRequest request,
        NnrpCompactResult *out_result
    );
    """
)


class _CffiAbiSubmitResultApi:
    def nnrp_py_client_submit_result_compact(
        self,
        library_path: bytes | str | os.PathLike[str],
        session_kind: int,
        session_id: int,
        session_generation: int,
        session_flags: int,
        operation_id: int,
        frame_id: int,
        payload: Any,
        payload_len: int,
        out_result: Any,
    ) -> int:
        return self.nnrp_py_client_submit_result_compact_v2(
            library_path,
            session_kind,
            session_id,
            session_generation,
            session_flags,
            operation_id,
            frame_id,
            payload,
            payload_len,
            2,
            out_result,
        )

    def nnrp_py_client_submit_result_compact_v2(
        self,
        library_path: bytes | str | os.PathLike[str],
        session_kind: int,
        session_id: int,
        session_generation: int,
        session_flags: int,
        operation_id: int,
        frame_id: int,
        payload: Any,
        payload_len: int,
        max_events: int,
        out_result: Any,
    ) -> int:
        if out_result == ffi.NULL:
            return -1
        native = _load_native_library(library_path)
        if not hasattr(native, "nnrp_client_submit_result_compact"):
            return -2

        request = ffi.new("NnrpClientSubmitResultRequest *")
        request.session.kind = session_kind
        request.session.id = session_id
        request.session.generation = session_generation
        request.session.flags = session_flags
        request.operation_id = operation_id
        request.frame_id = frame_id
        request.submit_payload.ptr = ffi.cast("const unsigned char *", payload)
        request.submit_payload.len = payload_len
        request.result_payload.ptr = ffi.cast("const unsigned char *", payload)
        request.result_payload.len = payload_len
        request.max_events = max_events

        native_result = ffi.new("NnrpCompactResult *")
        status = native.nnrp_client_submit_result_compact(request[0], native_result)
        out_result.status_code = status.status_code
        out_result.error_family = status.error_family
        out_result.protocol_error_code = status.protocol_error_code
        out_result.detail_code = status.detail_code
        if status.status_code != 0:
            out_result.has_result = 0
            return 0

        out_result.status_code = native_result.status.status_code
        out_result.error_family = native_result.status.error_family
        out_result.protocol_error_code = native_result.status.protocol_error_code
        out_result.detail_code = native_result.status.detail_code
        out_result.has_result = native_result.has_result
        out_result.event_kind = native_result.event_kind
        out_result.result_state = native_result.result_state
        out_result.operation_id = native_result.operation_id
        out_result.frame_id = native_result.frame_id
        out_result.payload_len = native_result.payload.len
        return 0


@lru_cache(maxsize=16)
def _load_native_library(library_path: bytes | str | os.PathLike[str]) -> Any:
    if isinstance(library_path, bytes):
        resolved = os.fsdecode(library_path)
    else:
        resolved = os.fspath(Path(library_path))
    return ffi.dlopen(resolved)


lib = _CffiAbiSubmitResultApi()
