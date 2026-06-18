from __future__ import annotations

import asyncio
import ctypes
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import nnrp.native as native_module
from nnrp.cache import CacheLeaseOutcome, CacheObjectIdentity
from nnrp.core.messages.control import (
    ResultHintBudgetPolicy,
    ResultHintCongestionState,
    ResultHintMetadata,
    ResultHintReason,
    SessionMigrateAckMetadata,
    TransportId,
    TransportPolicy,
)
from nnrp.native import (
    CACHE_ERROR_DEPENDENCY_INVALID,
    CACHE_ERROR_MISS,
    DEFAULT_ARTIFACT_ROOT_ENV,
    ERROR_FAMILY_CACHE,
    ERROR_FAMILY_SCHEMA,
    ERROR_FAMILY_SESSION,
    EVENT_KIND_CONTROL,
    EVENT_KIND_ERROR,
    EVENT_KIND_FLOW_UPDATED,
    EVENT_KIND_RESULT_DROPPED,
    EVENT_KIND_RESULT_HINT,
    EVENT_KIND_RESULT_PUSHED,
    EVENT_KIND_SUBMIT_ACCEPTED,
    FFI_STATUS_CALLBACK_REJECTED,
    FFI_STATUS_INTERNAL_ERROR,
    FFI_STATUS_INVALID_ARGUMENT,
    FFI_STATUS_INVALID_HANDLE,
    FFI_STATUS_INVALID_STATE,
    FFI_STATUS_OK,
    FFI_STATUS_PROTOCOL_ERROR,
    FFI_STATUS_WOULD_BLOCK,
    HANDLE_KIND_BUFFER,
    HANDLE_KIND_CACHE_LEASE,
    HANDLE_KIND_CONNECTION,
    HANDLE_KIND_EVENT_PUMP,
    HANDLE_KIND_OPERATION,
    HANDLE_KIND_SCHEMA_REGISTRY,
    HANDLE_KIND_SESSION,
    NATIVE_BINDING_MODE_ENV,
    REQUIRED_RUNTIME_FEATURES,
    RESULT_STATE_COMPLETED,
    SCHEMA_ERROR_HASH_CONFLICT,
    SESSION_ERROR_PRIORITY_REJECTED,
    SESSION_RECOVERY_OUTCOME_RESUME_ENABLED,
    SESSION_RECOVERY_OUTCOME_RESUMED,
    TRANSPORT_SLOT_IPC,
    TRANSPORT_SLOT_TCP,
    TRANSPORT_SLOT_WEBSOCKET,
    NativeArtifactError,
    NativeBufferHandle,
    NativeBufferView,
    NativeCacheLeaseBackend,
    NativeCacheLeaseHandle,
    NativeCallbackRejectedError,
    NativeConnectionHandle,
    NativeCreditUpdateEvent,
    NativeEventPumpHandle,
    NativeHandle,
    NativeHandleError,
    NativeInternalError,
    NativeInvalidArgumentError,
    NativeInvalidHandleError,
    NativeInvalidStateError,
    NativeMutableBufferView,
    NativeOperationHandle,
    NativeOperationLifecycle,
    NativeOperationSchedulingHint,
    NativePayloadFamilyEvent,
    NativePlatform,
    NativeProtocolError,
    NativeRecoveryCodec,
    NativeResultHintEvent,
    NativeRuntimeBackend,
    NativeRuntimeClient,
    NativeRuntimeConnection,
    NativeRuntimeDiagnostic,
    NativeRuntimeEntrypoints,
    NativeRuntimeEvent,
    NativeRuntimeOperation,
    NativeRuntimePollResult,
    NativeRuntimeResult,
    NativeRuntimeSession,
    NativeSchemaCodec,
    NativeSchemaRegistry,
    NativeSchemaRegistryHandle,
    NativeSessionHandle,
    NativeSessionPriorityClass,
    NativeSessionRecoveryOutcome,
    NativeStatus,
    NativeStructuredDiagnostic,
    NativeTransportEndpoint,
    NativeTransportProbeSample,
    NativeTransportProvider,
    NativeWouldBlockError,
    _load_native_cffi_submit_result_api,
    _NativeCffiSubmitResultApi,
    _NnrpBufferView,
    _NnrpBufferViewMut,
    _NnrpCacheLeaseRequest,
    _NnrpCacheLeaseResult,
    _NnrpCacheObjectId,
    _NnrpCallbackSink,
    _NnrpClientCancelRequest,
    _NnrpClientCompleteOperationRequest,
    _NnrpClientConnectRequest,
    _NnrpClientDropOperationRequest,
    _NnrpClientSubmitResultRequest,
    _NnrpCompactResult,
    _NnrpConnectionBootstrap,
    _NnrpControlRequest,
    _NnrpEvent,
    _NnrpFfiStatus,
    _NnrpHandle,
    _NnrpPollResult,
    _NnrpProtocolVersion,
    _NnrpRuntimeCapabilities,
    _NnrpSchemaDescriptorHeader,
    _NnrpServerAcceptRequest,
    _NnrpServerBindRequest,
    _NnrpServerFlowUpdateRequest,
    _NnrpServerReceiveSubmitRequest,
    _NnrpServerSendResultRequest,
    _NnrpSessionOpenRequest,
    _NnrpSessionRecoveryOutcome,
    _NnrpSessionResumeRequest,
    _NnrpSubmitRequest,
    _NnrpTypedPayloadDescriptor,
    _normalize_arch,
    current_native_platform,
    default_artifact_root,
    discover_native_transport_providers,
    load_native_client,
    load_native_library,
    load_native_recovery_codec,
    load_native_runtime,
    load_native_schema_codec,
    native_library_name,
    native_transport_slot_names,
    parse_native_transport_endpoint,
    probe_native_artifact,
    raise_for_native_status,
    resolve_native_artifact,
    resolve_native_transport_provider,
    select_native_runtime_backend,
    select_native_transport_provider,
)
from nnrp.schema import (
    Preview3TypedPayloadDescriptor,
    SchemaDescriptorHeader,
    SchemaRegistryAction,
    StandardProfile,
    StreamSemantics,
    TypedPayloadDescriptorFlags,
    token_delta_schema_descriptor,
)


class FakeLibrary:
    def __init__(
        self,
        *,
        abi_major: int = 1,
        abi_minor: int = 5,
        abi_patch: int = 0,
        protocol_major: int = 1,
        wire_format: int = 0,
        transport_slots: int = TRANSPORT_SLOT_TCP,
        feature_flags: int = REQUIRED_RUNTIME_FEATURES,
    ) -> None:
        self._capabilities = _NnrpRuntimeCapabilities(
            abi_major,
            abi_minor,
            abi_patch,
            0,
            _NnrpProtocolVersion(protocol_major, wire_format),
            1,
            0,
            0,
            3,
            6,
            0,
            transport_slots,
            feature_flags,
        )

    def nnrp_runtime_capabilities(self) -> _NnrpRuntimeCapabilities:
        return self._capabilities


class InvalidCapabilitiesLibrary:
    def nnrp_runtime_capabilities(self) -> object:
        return object()


class FakeFunction:
    def __init__(self, value: object | None = None, handler: object | None = None) -> None:
        self.value = value
        self.handler = handler
        self.calls: list[tuple[object, ...]] = []
        self.restype: object | None = None
        self.argtypes: list[object] | None = None

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        if self.handler is not None:
            return self.handler(*args)
        return self.value


class FakeEntrypointLibrary:
    def __init__(self, *, missing_symbol: str | None = None) -> None:
        for symbol in RUNTIME_ENTRYPOINT_SYMBOLS:
            if symbol != missing_symbol:
                setattr(self, symbol, FakeFunction())


class FakeRuntimeLibrary(FakeEntrypointLibrary):
    def __init__(
        self,
        *,
        status: _NnrpFfiStatus | None = None,
        event_payload: bytes = b"",
        event_kind: int = 6,
        await_event_delay_seconds: float = 0.0,
    ) -> None:
        super().__init__()
        self.status = status or NativeStatus.ok().to_ffi()
        self.event_kind = event_kind
        self.await_event_delay_seconds = await_event_delay_seconds
        self._event_payload_owner = (
            ctypes.create_string_buffer(event_payload, len(event_payload)) if event_payload else None
        )
        self.nnrp_runtime_capabilities.value = FakeLibrary().nnrp_runtime_capabilities()
        self.nnrp_client_connect.handler = self._client_connect
        self.nnrp_connection_bootstrap.handler = self._connection_bootstrap
        self.nnrp_client_open_session.handler = self._open_session
        self.nnrp_client_resume_session.handler = self._resume_session
        self.nnrp_client_submit.handler = self._submit
        self.nnrp_client_submit_result.handler = self._submit_result
        self.nnrp_client_submit_result_compact.handler = self._submit_result_compact
        self.nnrp_client_close.handler = self._close
        self.nnrp_client_close_connection.handler = self._close_connection
        self.nnrp_client_cancel.handler = self._cancel
        self.nnrp_client_complete_operation.handler = self._complete_operation
        self.nnrp_client_drop_operation.handler = self._drop_operation
        self.nnrp_client_send_flow_update.handler = self._send_flow_update
        self.nnrp_client_send_result_hint.handler = self._send_result_hint
        self.nnrp_client_await_event.handler = self._await_event
        self.nnrp_client_await_events.handler = self._await_events
        self.nnrp_control.handler = self._control
        self.nnrp_schema_descriptor_parse.handler = self._schema_descriptor_parse
        self.nnrp_schema_descriptor_write.handler = self._schema_descriptor_write
        self.nnrp_token_delta_schema_descriptor.handler = self._token_delta_schema_descriptor
        self.nnrp_typed_payload_descriptor_parse.handler = self._typed_payload_descriptor_parse
        self.nnrp_typed_payload_descriptor_write.handler = self._typed_payload_descriptor_write
        self.nnrp_typed_payload_validate_binding.handler = self._typed_payload_validate_binding
        self.nnrp_schema_registry_create.handler = self._schema_registry_create
        self.nnrp_schema_registry_install.handler = self._schema_registry_install
        self.nnrp_schema_registry_lookup.handler = self._schema_registry_lookup
        self.nnrp_schema_registry_invalidate.handler = self._schema_registry_invalidate
        self.nnrp_schema_registry_validate_binding.handler = self._schema_registry_validate_binding
        self.nnrp_schema_registry_release.handler = self._schema_registry_release
        self.nnrp_session_recovery_request_validate.handler = self._session_recovery_request_validate
        self.nnrp_session_recovery_ack_validate.handler = self._session_recovery_ack_validate
        self.nnrp_migration_recovery_validate.handler = self._migration_recovery_validate
        self.nnrp_migration_should_replay_frame.handler = self._migration_should_replay_frame
        self.nnrp_buffer_acquire_copy.handler = self._buffer_acquire_copy
        self.nnrp_buffer_view.handler = self._buffer_view
        self.nnrp_buffer_release.handler = self._buffer_release
        self.nnrp_cache_query.handler = self._cache_query
        self.nnrp_cache_touch.handler = self._cache_touch
        self.nnrp_cache_prefetch.handler = self._cache_prefetch
        self.nnrp_cache_release.handler = self._cache_release
        self.submitted_payloads: list[bytes] = []
        self._schema_registry: dict[tuple[int, int], SchemaDescriptorHeader] = {}
        self._buffers: dict[int, ctypes.Array[ctypes.c_char]] = {}
        self._cache_leases: dict[tuple[int, int, int, int], _NnrpHandle] = {}

    def _client_connect(self, request: _NnrpClientConnectRequest, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_CONNECTION, request.connection_id, request.generation, 0))
        return self.status

    def _connection_bootstrap(self, request: _NnrpConnectionBootstrap, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_CONNECTION, request.connection_id, request.generation, 0))
        return self.status

    def _open_session(self, request: _NnrpSessionOpenRequest, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_SESSION, request.requested_session_id, request.generation, 0))
        return self.status

    def _resume_session(
        self,
        request: _NnrpSessionResumeRequest,
        out_handle: object,
        out_outcome: object,
    ) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_SESSION, request.requested_session_id, request.generation, 0))
        target = getattr(out_outcome, "_obj", None)
        if target is None:
            target = ctypes.cast(out_outcome, ctypes.POINTER(_NnrpSessionRecoveryOutcome)).contents
        target.outcome_code = SESSION_RECOVERY_OUTCOME_RESUMED
        target.resume_window_ms = request.resume_token_bytes * 10
        return self.status

    def _submit(self, request: _NnrpSubmitRequest, out_handle: object) -> _NnrpFfiStatus:
        self.submitted_payloads.append(_read_buffer_view(request.payload))
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_OPERATION, request.operation_id, 1, 0))
        return self.status

    def _close(self, handle: _NnrpHandle) -> _NnrpFfiStatus:
        return self.status if handle.kind == HANDLE_KIND_SESSION else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)

    def _close_connection(self, handle: _NnrpHandle) -> _NnrpFfiStatus:
        return (
            self.status if handle.kind == HANDLE_KIND_CONNECTION else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        )

    def _cancel(self, request: _NnrpClientCancelRequest) -> _NnrpFfiStatus:
        if request.session.kind != HANDLE_KIND_SESSION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        return self.status

    def _complete_operation(self, request: _NnrpClientCompleteOperationRequest) -> _NnrpFfiStatus:
        if request.operation.kind != HANDLE_KIND_OPERATION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        payload = _read_buffer_view(request.payload)
        self._event_payload_owner = ctypes.create_string_buffer(payload, len(payload)) if payload else None
        self.event_kind = EVENT_KIND_RESULT_PUSHED
        return self.status

    def _submit_result(
        self,
        request: _NnrpClientSubmitResultRequest,
        out_operation: object,
        out_result: object,
    ) -> _NnrpFfiStatus:
        if request.session.kind != HANDLE_KIND_SESSION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        submit_payload = _read_buffer_view(request.submit_payload)
        result_payload = _read_buffer_view(request.result_payload)
        self.submitted_payloads.append(submit_payload)
        operation = _NnrpHandle(HANDLE_KIND_OPERATION, request.operation_id, 1, 0)
        _write_handle(out_operation, operation)
        self._event_payload_owner = (
            ctypes.create_string_buffer(result_payload, len(result_payload)) if result_payload else None
        )
        result_target = getattr(out_result, "_obj", None)
        if result_target is None:
            result_target = ctypes.cast(out_result, ctypes.POINTER(_NnrpPollResult)).contents
        result_target.status = NativeStatus.ok().to_ffi()
        result_target.has_event = 1
        result_target.event.kind = EVENT_KIND_RESULT_PUSHED
        result_target.event.connection = _NnrpHandle(HANDLE_KIND_CONNECTION, 12, 2, 0)
        result_target.event.session = request.session
        result_target.event.operation = operation
        result_target.event.frame_id = request.frame_id
        result_target.event.payload = _NnrpBufferView(
            ctypes.cast(self._event_payload_owner, ctypes.c_void_p) if self._event_payload_owner else None,
            len(result_payload),
        )
        result_target.event.diagnostic.status = NativeStatus.ok().to_ffi()
        return self.status

    def _submit_result_compact(
        self,
        request: _NnrpClientSubmitResultRequest,
        out_result: object,
    ) -> _NnrpFfiStatus:
        if request.session.kind != HANDLE_KIND_SESSION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        submit_payload = _read_buffer_view(request.submit_payload)
        result_payload = _read_buffer_view(request.result_payload)
        self.submitted_payloads.append(submit_payload)
        self._event_payload_owner = (
            ctypes.create_string_buffer(result_payload, len(result_payload)) if result_payload else None
        )
        result_target = getattr(out_result, "_obj", None)
        if result_target is None:
            result_target = ctypes.cast(out_result, ctypes.POINTER(_NnrpCompactResult)).contents
        result_target.status = NativeStatus.ok().to_ffi()
        result_target.has_result = 1
        result_target.event_kind = EVENT_KIND_RESULT_PUSHED
        result_target.result_state = RESULT_STATE_COMPLETED
        result_target.operation = _NnrpHandle(HANDLE_KIND_OPERATION, request.operation_id, 1, 0)
        result_target.operation_id = request.operation_id
        result_target.frame_id = request.frame_id
        result_target.payload = _NnrpBufferView(
            ctypes.cast(self._event_payload_owner, ctypes.c_void_p) if self._event_payload_owner else None,
            len(result_payload),
        )
        result_target.diagnostic.status = NativeStatus.ok().to_ffi()
        return self.status

    def _drop_operation(self, request: _NnrpClientDropOperationRequest) -> _NnrpFfiStatus:
        if request.operation.kind != HANDLE_KIND_OPERATION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        self._event_payload_owner = ctypes.create_string_buffer(b"drop", 4)
        self.event_kind = EVENT_KIND_RESULT_DROPPED
        return self.status

    def _send_flow_update(self, request: _NnrpServerFlowUpdateRequest) -> _NnrpFfiStatus:
        if request.session.kind != HANDLE_KIND_SESSION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        self._event_payload_owner = ctypes.create_string_buffer(b"flow", 4)
        self.event_kind = EVENT_KIND_FLOW_UPDATED
        return self.status

    def _send_result_hint(self, request: _NnrpControlRequest) -> _NnrpFfiStatus:
        if request.handle.kind != HANDLE_KIND_SESSION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        self._event_payload_owner = ctypes.create_string_buffer(_read_buffer_view(request.payload), request.payload.len)
        self.event_kind = EVENT_KIND_RESULT_HINT
        return self.status

    def _control(self, request: _NnrpControlRequest) -> _NnrpFfiStatus:
        return self.status if request.handle.kind != 0 else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)

    def _schema_descriptor_parse(
        self,
        source: _NnrpBufferView,
        out_descriptor: object,
    ) -> _NnrpFfiStatus:
        _write_schema_descriptor(out_descriptor, SchemaDescriptorHeader.unpack(_read_buffer_view(source)))
        return self.status

    def _schema_descriptor_write(
        self,
        descriptor: _NnrpSchemaDescriptorHeader,
        destination: _NnrpBufferViewMut,
    ) -> _NnrpFfiStatus:
        payload = _schema_descriptor_from_ffi(descriptor).pack()
        ctypes.memmove(destination.ptr, payload, len(payload))
        return self.status

    def _token_delta_schema_descriptor(self, out_descriptor: object) -> _NnrpFfiStatus:
        _write_schema_descriptor(out_descriptor, token_delta_schema_descriptor())
        return self.status

    def _typed_payload_descriptor_parse(
        self,
        source: _NnrpBufferView,
        out_descriptor: object,
    ) -> _NnrpFfiStatus:
        descriptor = Preview3TypedPayloadDescriptor.unpack(_read_buffer_view(source))
        _write_typed_payload_descriptor(out_descriptor, descriptor)
        return self.status

    def _typed_payload_descriptor_write(
        self,
        descriptor: _NnrpTypedPayloadDescriptor,
        destination: _NnrpBufferViewMut,
    ) -> _NnrpFfiStatus:
        payload = _typed_payload_descriptor_from_ffi(descriptor).pack()
        ctypes.memmove(destination.ptr, payload, len(payload))
        return self.status

    def _typed_payload_validate_binding(
        self,
        schema_descriptors: object,
        schema_count: int,
        descriptor: _NnrpTypedPayloadDescriptor,
    ) -> _NnrpFfiStatus:
        if schema_count == 0:
            return _NnrpFfiStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_SCHEMA, 0x3002, 0x42)
        schemas = ctypes.cast(schema_descriptors, ctypes.POINTER(_NnrpSchemaDescriptorHeader))
        typed = _typed_payload_descriptor_from_ffi(descriptor)
        for index in range(schema_count):
            schema = _schema_descriptor_from_ffi(schemas[index])
            if schema.schema_id == typed.schema_id and schema.schema_version == typed.schema_version:
                return self.status
        return _NnrpFfiStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_SCHEMA, 0x3001, 0x41)

    def _schema_registry_create(self, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_SCHEMA_REGISTRY, 700, 1, 0))
        token = token_delta_schema_descriptor()
        self._schema_registry = {(token.schema_id, token.schema_version): token}
        return self.status

    def _schema_registry_install(
        self,
        registry: _NnrpHandle,
        descriptor: _NnrpSchemaDescriptorHeader,
        out_action: object,
    ) -> _NnrpFfiStatus:
        if registry.kind != HANDLE_KIND_SCHEMA_REGISTRY:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        schema = _schema_descriptor_from_ffi(descriptor)
        key = (schema.schema_id, schema.schema_version)
        action = 1 if self._schema_registry.get(key) == schema else 0
        self._schema_registry[key] = schema
        target = getattr(out_action, "_obj", None)
        if target is None:
            target = ctypes.cast(out_action, ctypes.POINTER(ctypes.c_uint32)).contents
        target.value = action
        return self.status

    def _schema_registry_lookup(
        self,
        registry: _NnrpHandle,
        schema_id: int,
        schema_version: int,
        out_descriptor: object,
    ) -> _NnrpFfiStatus:
        if registry.kind != HANDLE_KIND_SCHEMA_REGISTRY:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        descriptor = self._schema_registry.get((schema_id, schema_version))
        if descriptor is None:
            return _NnrpFfiStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_SCHEMA, 0x3001, 0)
        _write_schema_descriptor(out_descriptor, descriptor)
        return self.status

    def _schema_registry_invalidate(
        self,
        registry: _NnrpHandle,
        schema_id: int,
        schema_version: int,
        out_action: object,
    ) -> _NnrpFfiStatus:
        if registry.kind != HANDLE_KIND_SCHEMA_REGISTRY:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        self._schema_registry.pop((schema_id, schema_version), None)
        target = getattr(out_action, "_obj", None)
        if target is None:
            target = ctypes.cast(out_action, ctypes.POINTER(ctypes.c_uint32)).contents
        target.value = 3
        return self.status

    def _schema_registry_validate_binding(
        self,
        registry: _NnrpHandle,
        descriptor: _NnrpTypedPayloadDescriptor,
    ) -> _NnrpFfiStatus:
        if registry.kind != HANDLE_KIND_SCHEMA_REGISTRY:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        typed = _typed_payload_descriptor_from_ffi(descriptor)
        if (typed.schema_id, typed.schema_version) not in self._schema_registry:
            return _NnrpFfiStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_SCHEMA, 0x3001, 0)
        return self.status

    def _schema_registry_release(self, registry: _NnrpHandle) -> _NnrpFfiStatus:
        return (
            self.status
            if registry.kind == HANDLE_KIND_SCHEMA_REGISTRY
            else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        )

    def _buffer_acquire_copy(
        self,
        source: _NnrpBufferView,
        out_buffer: object,
        out_view: object,
    ) -> _NnrpFfiStatus:
        payload = _read_buffer_view(source)
        buffer_id = len(self._buffers) + 900
        owner = ctypes.create_string_buffer(payload, len(payload))
        self._buffers[buffer_id] = owner
        _write_handle(out_buffer, _NnrpHandle(HANDLE_KIND_BUFFER, buffer_id, 1, 0))
        _write_buffer_view(out_view, owner)
        return self.status

    def _buffer_view(self, buffer: _NnrpHandle, out_view: object) -> _NnrpFfiStatus:
        owner = self._buffers.get(buffer.id)
        if buffer.kind != HANDLE_KIND_BUFFER or owner is None:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        _write_buffer_view(out_view, owner)
        return self.status

    def _buffer_release(self, buffer: _NnrpHandle) -> _NnrpFfiStatus:
        if buffer.kind != HANDLE_KIND_BUFFER:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        self._buffers.pop(buffer.id, None)
        return self.status

    def _cache_query(self, request: _NnrpCacheLeaseRequest, out_result: object) -> _NnrpFfiStatus:
        return self._write_cache_result(request, out_result, outcome=0)

    def _cache_touch(self, request: _NnrpCacheLeaseRequest, out_result: object) -> _NnrpFfiStatus:
        return self._write_cache_result(request, out_result, outcome=0)

    def _cache_prefetch(
        self,
        owner: _NnrpHandle,
        objects: object,
        object_count: int,
        now_ms: int,
        ttl_ms: int,
        out_results: object,
    ) -> _NnrpFfiStatus:
        object_items = ctypes.cast(objects, ctypes.POINTER(_NnrpCacheObjectId))
        result_items = ctypes.cast(out_results, ctypes.POINTER(_NnrpCacheLeaseResult))
        for index in range(object_count):
            request = _NnrpCacheLeaseRequest(owner, object_items[index], 1, now_ms, ttl_ms)
            self._populate_cache_result(result_items[index], request, outcome=0)
        return self.status

    def _cache_release(self, lease_handle: _NnrpHandle, out_result: object) -> _NnrpFfiStatus:
        if lease_handle.kind != HANDLE_KIND_CACHE_LEASE:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        for key, handle in list(self._cache_leases.items()):
            if handle.id == lease_handle.id:
                namespace, key_hi, key_lo, object_kind = key
                result = _cache_result_target(out_result)
                result.outcome_code = 3
                result.lease_handle = lease_handle
                result.object_id = _NnrpCacheObjectId(namespace, key_hi, key_lo, object_kind)
                result.object_version = 1
                result.lease_id = lease_handle.id
                result.expires_at_ms = 0
                self._cache_leases.pop(key, None)
                return self.status
        return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)

    def _write_cache_result(
        self,
        request: _NnrpCacheLeaseRequest,
        out_result: object,
        *,
        outcome: int,
    ) -> _NnrpFfiStatus:
        result = _cache_result_target(out_result)
        self._populate_cache_result(result, request, outcome=outcome)
        return self.status

    def _populate_cache_result(
        self,
        result: _NnrpCacheLeaseResult,
        request: _NnrpCacheLeaseRequest,
        *,
        outcome: int,
    ) -> None:
        key = (
            request.object_id.cache_namespace,
            request.object_id.cache_key_hi,
            request.object_id.cache_key_lo,
            request.object_id.object_kind,
        )
        lease = self._cache_leases.get(key)
        if lease is None:
            lease = _NnrpHandle(HANDLE_KIND_CACHE_LEASE, len(self._cache_leases) + 800, 1, 0)
            self._cache_leases[key] = lease
        result.outcome_code = outcome
        result.lease_handle = lease
        result.object_id = request.object_id
        result.object_version = request.expected_version or 1
        result.lease_id = lease.id
        result.expires_at_ms = request.now_ms + (request.ttl_ms or 30_000)

    def _session_recovery_request_validate(self, session_open_metadata: _NnrpBufferView) -> _NnrpFfiStatus:
        if not _read_buffer_view(session_open_metadata):
            return _NnrpFfiStatus(FFI_STATUS_INVALID_ARGUMENT, 0, 0, 0)
        return self.status

    def _session_recovery_ack_validate(
        self,
        session_open_metadata: _NnrpBufferView,
        session_open_ack_metadata: _NnrpBufferView,
        out_outcome: object,
    ) -> _NnrpFfiStatus:
        if not _read_buffer_view(session_open_metadata) or not _read_buffer_view(session_open_ack_metadata):
            return _NnrpFfiStatus(FFI_STATUS_INVALID_ARGUMENT, 0, 0, 0)
        target = getattr(out_outcome, "_obj", None)
        if target is None:
            target = ctypes.cast(out_outcome, ctypes.POINTER(_NnrpSessionRecoveryOutcome)).contents
        target.outcome_code = SESSION_RECOVERY_OUTCOME_RESUMED
        target.resume_window_ms = 250
        return self.status

    def _migration_recovery_validate(
        self,
        session_migrate_metadata: _NnrpBufferView,
        session_migrate_ack_metadata: _NnrpBufferView,
    ) -> _NnrpFfiStatus:
        if not _read_buffer_view(session_migrate_metadata) or not _read_buffer_view(session_migrate_ack_metadata):
            return _NnrpFfiStatus(FFI_STATUS_INVALID_ARGUMENT, 0, 0, 0)
        return self.status

    def _migration_should_replay_frame(
        self,
        session_migrate_ack_metadata: _NnrpBufferView,
        frame_id: int,
        out_should_replay: object,
    ) -> _NnrpFfiStatus:
        if not _read_buffer_view(session_migrate_ack_metadata):
            return _NnrpFfiStatus(FFI_STATUS_INVALID_ARGUMENT, 0, 0, 0)
        target = getattr(out_should_replay, "_obj", None)
        if target is None:
            target = ctypes.cast(out_should_replay, ctypes.POINTER(ctypes.c_uint8)).contents
        target.value = 1 if frame_id >= 45 else 0
        return self.status

    def _await_event(self, handle: _NnrpHandle, out_result: object) -> _NnrpFfiStatus:
        if self.await_event_delay_seconds:
            time.sleep(self.await_event_delay_seconds)
        target = getattr(out_result, "_obj", None)
        if target is None:
            target = ctypes.cast(out_result, ctypes.POINTER(_NnrpPollResult)).contents
        target.status = NativeStatus.ok().to_ffi()
        target.has_event = 1 if self._event_payload_owner is not None else 0
        if self._event_payload_owner is not None:
            target.event.kind = self.event_kind
            target.event.connection = handle
            target.event.session = _NnrpHandle(HANDLE_KIND_SESSION, 41, 3, 0)
            target.event.operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99, 1, 0)
            target.event.frame_id = 7
            target.event.payload = _NnrpBufferView(
                ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                len(self._event_payload_owner.raw),
            )
            target.event.diagnostic.status = NativeStatus.ok().to_ffi()
        return self.status

    def _await_events(
        self,
        handle: _NnrpHandle,
        out_events: object,
        event_capacity: int,
        out_event_count: object,
    ) -> _NnrpFfiStatus:
        if self.await_event_delay_seconds:
            time.sleep(self.await_event_delay_seconds)
        count_target = getattr(out_event_count, "_obj", None)
        if count_target is None:
            count_target = ctypes.cast(out_event_count, ctypes.POINTER(ctypes.c_size_t)).contents
        count_target.value = 0
        if self._event_payload_owner is None:
            return _NnrpFfiStatus(FFI_STATUS_WOULD_BLOCK, 0, 0, 0)
        if event_capacity <= 0:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_ARGUMENT, 0, 0, 0)

        events = ctypes.cast(out_events, ctypes.POINTER(_NnrpEvent))
        for index in range(event_capacity):
            events[index].kind = self.event_kind
            events[index].connection = handle
            events[index].session = _NnrpHandle(HANDLE_KIND_SESSION, 41 + index, 3, 0)
            events[index].operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99 + index, 1, 0)
            events[index].frame_id = 7 + index
            events[index].payload = _NnrpBufferView(
                ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                len(self._event_payload_owner.raw),
            )
            events[index].diagnostic.status = NativeStatus.ok().to_ffi()
        count_target.value = event_capacity
        return self.status


class ExpiringCacheRuntimeLibrary(FakeRuntimeLibrary):
    def _cache_query(self, request: _NnrpCacheLeaseRequest, out_result: object) -> _NnrpFfiStatus:
        result = _cache_result_target(out_result)
        self._populate_cache_result(result, request, outcome=2)
        result.expires_at_ms = request.now_ms
        return _NnrpFfiStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_CACHE, 0x30002, 0)


def _write_handle(out_handle: object, handle: _NnrpHandle) -> None:
    target = getattr(out_handle, "_obj", None)
    if target is not None:
        target.kind = handle.kind
        target.id = handle.id
        target.generation = handle.generation
        target.flags = handle.flags
        return

    ctypes.cast(out_handle, ctypes.POINTER(_NnrpHandle)).contents = handle


def _read_buffer_view(view: _NnrpBufferView) -> bytes:
    if view.len == 0:
        return b""
    return ctypes.string_at(view.ptr, view.len)


def _write_buffer_view(out_view: object, owner: ctypes.Array[ctypes.c_char]) -> None:
    target = getattr(out_view, "_obj", None)
    if target is None:
        target = ctypes.cast(out_view, ctypes.POINTER(_NnrpBufferView)).contents
    target.ptr = ctypes.cast(owner, ctypes.c_void_p)
    target.len = len(owner.raw)


def _cache_result_target(out_result: object) -> _NnrpCacheLeaseResult:
    target = getattr(out_result, "_obj", None)
    if target is None:
        target = ctypes.cast(out_result, ctypes.POINTER(_NnrpCacheLeaseResult)).contents
    return target


def _schema_descriptor_from_ffi(descriptor: _NnrpSchemaDescriptorHeader) -> SchemaDescriptorHeader:
    return SchemaDescriptorHeader(
        schema_id=descriptor.schema_id,
        schema_version=descriptor.schema_version,
        profile_id=descriptor.profile_id,
        schema_flags=descriptor.schema_flags,
        min_version_major=descriptor.min_version_major,
        max_version_major=descriptor.max_version_major,
        body_bytes=descriptor.body_bytes,
        dependency_count=descriptor.dependency_count,
        default_stream_semantics=descriptor.default_stream_semantics,
        schema_hash=descriptor.schema_hash,
    )


def _write_schema_descriptor(out_descriptor: object, descriptor: SchemaDescriptorHeader) -> None:
    target = getattr(out_descriptor, "_obj", None)
    if target is None:
        target = ctypes.cast(out_descriptor, ctypes.POINTER(_NnrpSchemaDescriptorHeader)).contents
    target.schema_id = descriptor.schema_id
    target.schema_version = descriptor.schema_version
    target.profile_id = int(descriptor.profile_id)
    target.schema_flags = int(descriptor.schema_flags)
    target.min_version_major = descriptor.min_version_major
    target.max_version_major = descriptor.max_version_major
    target.reserved0 = 0
    target.body_bytes = descriptor.body_bytes
    target.dependency_count = descriptor.dependency_count
    target.default_stream_semantics = int(descriptor.default_stream_semantics)
    target.schema_hash = descriptor.schema_hash


def _typed_payload_descriptor_from_ffi(descriptor: _NnrpTypedPayloadDescriptor) -> Preview3TypedPayloadDescriptor:
    return Preview3TypedPayloadDescriptor(
        profile_id=descriptor.profile_id,
        descriptor_flags=descriptor.descriptor_flags,
        schema_id=descriptor.schema_id,
        schema_version=descriptor.schema_version,
        stream_semantics=descriptor.stream_semantics,
        offset=descriptor.offset,
        length=descriptor.length,
    )


def _write_typed_payload_descriptor(out_descriptor: object, descriptor: Preview3TypedPayloadDescriptor) -> None:
    target = getattr(out_descriptor, "_obj", None)
    if target is None:
        target = ctypes.cast(out_descriptor, ctypes.POINTER(_NnrpTypedPayloadDescriptor)).contents
    target.profile_id = int(descriptor.profile_id)
    target.descriptor_flags = int(descriptor.descriptor_flags)
    target.schema_id = descriptor.schema_id
    target.schema_version = descriptor.schema_version
    target.stream_semantics = int(descriptor.stream_semantics)
    target.reserved0 = 0
    target.offset = descriptor.offset
    target.length = descriptor.length


class SlowSubmitRuntimeLibrary(FakeRuntimeLibrary):
    def _submit(self, request: _NnrpSubmitRequest, out_handle: object) -> _NnrpFfiStatus:
        time.sleep(0.2)
        return super()._submit(request, out_handle)


class FakeBackend:
    def __init__(self) -> None:
        self.connections: list[tuple[int, int, int]] = []

    def connect(self, *, connection_id: int, generation: int, transport_id: int) -> NativeRuntimeConnection:
        self.connections.append((connection_id, generation, transport_id))
        raise NotImplementedError("fixture connect")

    def bootstrap_connection(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_id: int,
    ) -> NativeRuntimeConnection:
        self.connections.append((connection_id, generation, transport_id))
        raise NotImplementedError("fixture bootstrap")


RUNTIME_ENTRYPOINT_SYMBOLS = [
    "nnrp_current_protocol_version",
    "nnrp_runtime_capabilities",
    "nnrp_connection_bootstrap",
    "nnrp_client_connect",
    "nnrp_session_open",
    "nnrp_client_open_session",
    "nnrp_client_resume_session",
    "nnrp_submit",
    "nnrp_client_submit",
    "nnrp_client_submit_result",
    "nnrp_client_submit_result_compact",
    "nnrp_session_close",
    "nnrp_client_close",
    "nnrp_client_close_connection",
    "nnrp_client_cancel",
    "nnrp_client_complete_operation",
    "nnrp_client_drop_operation",
    "nnrp_client_send_flow_update",
    "nnrp_client_send_result_hint",
    "nnrp_client_await_event",
    "nnrp_client_await_events",
    "nnrp_server_bind",
    "nnrp_server_accept",
    "nnrp_server_receive_submit",
    "nnrp_server_send_result",
    "nnrp_server_send_flow_update",
    "nnrp_server_close",
    "nnrp_control",
    "nnrp_schema_descriptor_parse",
    "nnrp_schema_descriptor_write",
    "nnrp_token_delta_schema_descriptor",
    "nnrp_typed_payload_descriptor_parse",
    "nnrp_typed_payload_descriptor_write",
    "nnrp_typed_payload_validate_binding",
    "nnrp_schema_registry_create",
    "nnrp_schema_registry_install",
    "nnrp_schema_registry_lookup",
    "nnrp_schema_registry_invalidate",
    "nnrp_schema_registry_validate_binding",
    "nnrp_schema_registry_release",
    "nnrp_session_recovery_request_validate",
    "nnrp_session_recovery_ack_validate",
    "nnrp_migration_recovery_validate",
    "nnrp_migration_should_replay_frame",
    "nnrp_buffer_acquire_copy",
    "nnrp_buffer_view",
    "nnrp_buffer_release",
    "nnrp_cache_query",
    "nnrp_cache_touch",
    "nnrp_cache_prefetch",
    "nnrp_cache_release",
    "nnrp_poll_empty",
    "nnrp_dispatch_event",
]


def test_current_native_platform_normalizes_host_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "aarch64")

    assert current_native_platform() == NativePlatform("macos", "arm64")


def test_default_artifact_root_prefers_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEFAULT_ARTIFACT_ROOT_ENV, str(tmp_path))

    assert default_artifact_root() == tmp_path


def test_default_artifact_root_falls_back_to_package_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEFAULT_ARTIFACT_ROOT_ENV, raising=False)

    assert default_artifact_root().name == "native_artifacts"


def test_native_library_name_matches_supported_platforms() -> None:
    assert native_library_name("windows") == "nnrp_ffi.dll"
    assert native_library_name("linux") == "libnnrp_ffi.so"
    assert native_library_name("android") == "libnnrp_ffi.so"
    assert native_library_name("darwin") == "libnnrp_ffi.dylib"
    assert native_library_name("ios") == "libnnrp_ffi.dylib"


def test_native_platform_rejects_unsupported_values() -> None:
    with pytest.raises(NativeArtifactError, match="unsupported native artifact OS"):
        native_library_name("plan9")
    with pytest.raises(NativeArtifactError, match="unsupported native artifact architecture"):
        _normalize_arch("sparc")


def test_resolve_native_artifact_uses_platform_tag_and_library_name(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "linux-x86_64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "libnnrp_ffi.so"
    artifact.write_bytes(b"not-a-real-shared-library")

    assert resolve_native_artifact(tmp_path, NativePlatform("linux", "x86_64")) == artifact


def test_resolve_native_artifact_supports_split_transport_artifacts(tmp_path: Path) -> None:
    tcp_dir = tmp_path / "linux-x86_64" / "tcp"
    quic_dir = tmp_path / "linux-x86_64" / "quic"
    tcp_dir.mkdir(parents=True)
    quic_dir.mkdir(parents=True)
    tcp_artifact = tcp_dir / "libnnrp_ffi.so"
    quic_artifact = quic_dir / "libnnrp_ffi.so"
    tcp_artifact.write_bytes(b"tcp")
    quic_artifact.write_bytes(b"quic")
    native_platform = NativePlatform("linux", "x86_64")

    assert resolve_native_artifact(tmp_path, native_platform) == tcp_artifact
    assert resolve_native_artifact(tmp_path, native_platform, transport="tcp") == tcp_artifact
    assert resolve_native_artifact(tmp_path, native_platform, transport="quic") == quic_artifact


def test_resolve_native_artifact_supports_preview4_ipc_and_websocket_artifacts(tmp_path: Path) -> None:
    ipc_dir = tmp_path / "linux-x86_64" / "ipc"
    websocket_dir = tmp_path / "linux-x86_64" / "websocket"
    ipc_dir.mkdir(parents=True)
    websocket_dir.mkdir(parents=True)
    ipc_artifact = ipc_dir / "libnnrp_ffi.so"
    websocket_artifact = websocket_dir / "libnnrp_ffi.so"
    ipc_artifact.write_bytes(b"ipc")
    websocket_artifact.write_bytes(b"websocket")
    native_platform = NativePlatform("linux", "x86_64")

    assert resolve_native_artifact(tmp_path, native_platform, transport="ipc") == ipc_artifact
    assert resolve_native_artifact(tmp_path, native_platform, transport="websocket") == websocket_artifact


def test_resolve_native_artifact_rejects_unknown_transport_scope(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError, match="unsupported native transport scope"):
        resolve_native_artifact(tmp_path, NativePlatform("linux", "x86_64"), transport="stdio")


def _write_provider_artifact(root: Path, scope: str, *, slots: list[str] | None = None) -> Path:
    artifact_dir = root / "linux-x86_64" / scope
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "libnnrp_ffi.so"
    artifact.write_bytes(scope.encode("utf-8"))
    (artifact_dir / "manifest.json").write_text(
        json.dumps(
            {
                "package": f"nnrp-ffi-transport-{scope}",
                "transport_scope": scope,
                "transport_slots": slots or [scope],
                "enabled_features": [f"transport-{scope}"],
                "provider_cost": {"latency_bias": 1},
                "provider_preference": {"locality": "node"},
                "platform_limitations": ["loopback-only"],
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _write_provider_artifact_with_manifest(root: Path, scope: str, manifest: object) -> Path:
    artifact_dir = root / "linux-x86_64" / scope
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "libnnrp_ffi.so"
    artifact.write_bytes(scope.encode("utf-8"))
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return artifact


def test_discover_native_transport_providers_reports_preview4_artifact_metadata(tmp_path: Path) -> None:
    tcp_artifact = _write_provider_artifact(tmp_path, "tcp")
    ipc_artifact = _write_provider_artifact(tmp_path, "ipc")
    websocket_artifact = _write_provider_artifact(tmp_path, "websocket")

    providers = discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))

    assert providers == (
        NativeTransportProvider(
            name="tcp",
            artifact_path=tcp_artifact,
            manifest_path=tcp_artifact.with_name("manifest.json"),
            transport_slots=("tcp",),
            enabled_features=("transport-tcp",),
            package="nnrp-ffi-transport-tcp",
            transport_scope="tcp",
            platform_tag="linux-x86_64",
            cost={"latency_bias": 1},
            preference={"locality": "node"},
            limitations=("loopback-only",),
        ),
        NativeTransportProvider(
            name="ipc",
            artifact_path=ipc_artifact,
            manifest_path=ipc_artifact.with_name("manifest.json"),
            transport_slots=("ipc",),
            enabled_features=("transport-ipc",),
            package="nnrp-ffi-transport-ipc",
            transport_scope="ipc",
            platform_tag="linux-x86_64",
            cost={"latency_bias": 1},
            preference={"locality": "node"},
            limitations=("loopback-only",),
        ),
        NativeTransportProvider(
            name="websocket",
            artifact_path=websocket_artifact,
            manifest_path=websocket_artifact.with_name("manifest.json"),
            transport_slots=("websocket",),
            enabled_features=("transport-websocket",),
            package="nnrp-ffi-transport-websocket",
            transport_scope="websocket",
            platform_tag="linux-x86_64",
            cost={"latency_bias": 1},
            preference={"locality": "node"},
            limitations=("loopback-only",),
        ),
    )


def test_discover_native_transport_provider_all_scope_infers_slots_from_manifest(tmp_path: Path) -> None:
    platform_dir = tmp_path / "linux-x86_64"
    platform_dir.mkdir()
    artifact = platform_dir / "libnnrp_ffi.so"
    artifact.write_bytes(b"all")
    (platform_dir / "manifest.json").write_text(json.dumps({"transport_scope": "all"}), encoding="utf-8")

    providers = discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))

    assert providers == (
        NativeTransportProvider(
            name="tcp",
            artifact_path=artifact,
            manifest_path=artifact.with_name("manifest.json"),
            transport_slots=("tcp", "quic", "ipc", "websocket"),
            enabled_features=(),
            package=None,
            transport_scope="all",
            platform_tag="linux-x86_64",
        ),
    )


def test_resolve_native_transport_provider_rejects_unadvertised_provider(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    provider = resolve_native_transport_provider(
        "tcp",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )

    assert provider.name == "tcp"
    with pytest.raises(NativeArtifactError, match="not advertised"):
        resolve_native_transport_provider("ipc", root=tmp_path, native_platform=NativePlatform("linux", "x86_64"))


def test_discover_native_transport_provider_rejects_manifest_scope_slot_mismatch(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "ipc", slots=["tcp"])

    with pytest.raises(NativeArtifactError, match="not listed in transport_slots"):
        discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))


@pytest.mark.parametrize(
    ("manifest_text", "match"),
    [
        ("{", "invalid JSON"),
        ("[]", "must be a JSON object"),
    ],
)
def test_discover_native_transport_provider_rejects_invalid_manifest_document(
    tmp_path: Path,
    manifest_text: str,
    match: str,
) -> None:
    artifact_dir = tmp_path / "linux-x86_64" / "ipc"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "libnnrp_ffi.so").write_bytes(b"ipc")
    (artifact_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")

    with pytest.raises(NativeArtifactError, match=match):
        discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))


@pytest.mark.parametrize(
    ("manifest", "match"),
    [
        ({"transport_scope": 7}, "transport_scope must be a string"),
        ({"transport_scope": "stdio"}, "unsupported native transport scope"),
        ({"transport_scope": "ipc", "transport_slots": []}, "transport_slots must be a non-empty list"),
        ({"transport_scope": "ipc", "transport_slots": [7]}, "transport_slots entries must be strings"),
        ({"transport_scope": "ipc", "transport_slots": ["stdio"]}, "unsupported native transport slot"),
        (
            {"transport_scope": "ipc", "transport_slots": ["ipc"], "enabled_features": "transport-ipc"},
            "enabled_features must be a list",
        ),
        (
            {"transport_scope": "ipc", "transport_slots": ["ipc"], "enabled_features": [""]},
            "enabled_features entries must be non-empty strings",
        ),
        (
            {"transport_scope": "ipc", "transport_slots": ["ipc"], "package": ""},
            "package must be a non-empty string",
        ),
        (
            {"transport_scope": "ipc", "transport_slots": ["ipc"], "provider_cost": []},
            "provider_cost must be an object",
        ),
        (
            {"transport_scope": "ipc", "transport_slots": ["ipc"], "provider_preference": []},
            "provider_preference must be an object",
        ),
        (
            {"transport_scope": "ipc", "transport_slots": ["ipc"], "platform_limitations": "linux"},
            "platform_limitations must be a list",
        ),
    ],
)
def test_discover_native_transport_provider_rejects_invalid_manifest_fields(
    tmp_path: Path,
    manifest: object,
    match: str,
) -> None:
    _write_provider_artifact_with_manifest(tmp_path, "ipc", manifest)

    with pytest.raises(NativeArtifactError, match=match):
        discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))


def test_native_transport_slot_names_maps_preview4_slots() -> None:
    assert native_transport_slot_names(TRANSPORT_SLOT_TCP | TRANSPORT_SLOT_IPC | TRANSPORT_SLOT_WEBSOCKET) == (
        "tcp",
        "ipc",
        "websocket",
    )


@pytest.mark.parametrize(
    ("uri", "scheme", "transport_name", "transport_id", "address", "secure"),
    [
        ("unix:///tmp/nnrp.sock", "unix", "ipc", TransportId.IPC, "/tmp/nnrp.sock", False),
        ("npipe://./pipe/nnrp", "npipe", "ipc", TransportId.IPC, "./pipe/nnrp", False),
        ("ws://127.0.0.1:19091/nnrp", "ws", "websocket", TransportId.WEBSOCKET, "127.0.0.1:19091/nnrp", False),
        (
            "wss://example.test/nnrp?profile=runtime",
            "wss",
            "websocket",
            TransportId.WEBSOCKET,
            "example.test/nnrp?profile=runtime",
            True,
        ),
    ],
)
def test_parse_native_transport_endpoint_maps_preview4_endpoint_schemes(
    uri: str,
    scheme: str,
    transport_name: str,
    transport_id: TransportId,
    address: str,
    secure: bool,
) -> None:
    endpoint = parse_native_transport_endpoint(uri)

    assert endpoint == NativeTransportEndpoint(
        uri=uri,
        scheme=scheme,
        transport_name=transport_name,
        transport_id=transport_id,
        address=address,
        secure=secure,
    )
    assert NativeTransportEndpoint.from_uri(uri) == endpoint


@pytest.mark.parametrize(
    ("uri", "match"),
    [
        ("", "must be non-empty"),
        ("stdio:///tmp/nnrp", "unsupported native transport endpoint scheme"),
        ("unix://host/tmp/nnrp.sock", "unix native transport endpoints"),
        ("ws:///nnrp", "must include an authority"),
        ("wss://example.test/nnrp#fragment", "must not include a fragment"),
    ],
)
def test_parse_native_transport_endpoint_rejects_invalid_endpoint_shapes(uri: str, match: str) -> None:
    with pytest.raises(NativeArtifactError, match=match):
        parse_native_transport_endpoint(uri)


def test_select_native_transport_provider_selects_single_installed_transport(tmp_path: Path) -> None:
    tcp_artifact = _write_provider_artifact(tmp_path, "tcp")

    selection = select_native_transport_provider(root=tmp_path, native_platform=NativePlatform("linux", "x86_64"))

    assert selection.selected_provider.artifact_path == tcp_artifact
    assert selection.selected_transport_name == "tcp"
    assert selection.selected_transport_id is TransportId.TCP
    assert selection.policy is TransportPolicy.AUTO
    assert selection.rejected == ()
    assert selection.diagnostic == "single installed transport selected directly"


def test_select_native_transport_provider_applies_preview4_policy_order(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "quic")
    _write_provider_artifact(tmp_path, "ipc")
    _write_provider_artifact(tmp_path, "websocket")

    auto = select_native_transport_provider(root=tmp_path, native_platform=NativePlatform("linux", "x86_64"))
    preferred_websocket = select_native_transport_provider(
        TransportPolicy.PREFER_WEBSOCKET,
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )
    preferred_tcp = select_native_transport_provider(
        "prefer-tcp",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )

    assert auto.selected_transport_id is TransportId.IPC
    assert preferred_websocket.selected_transport_id is TransportId.WEBSOCKET
    assert preferred_tcp.selected_transport_id is TransportId.TCP


def test_select_native_transport_provider_rejects_missing_forced_transport(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    with pytest.raises(NativeArtifactError, match="forced native transport is not available: ipc"):
        select_native_transport_provider(
            TransportPolicy.FORCE_IPC,
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
        )


def test_select_native_transport_provider_rejects_empty_provider_registry(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError, match="no native transport providers are advertised"):
        select_native_transport_provider(root=tmp_path, native_platform=NativePlatform("linux", "x86_64"))


def test_select_native_transport_provider_accepts_integer_and_auto_string_policy(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "websocket")

    auto = select_native_transport_provider("auto", root=tmp_path, native_platform=NativePlatform("linux", "x86_64"))
    forced = select_native_transport_provider(
        int(TransportPolicy.FORCE_WEBSOCKET),
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )

    assert auto.selected_transport_id is TransportId.WEBSOCKET
    assert forced.selected_transport_id is TransportId.WEBSOCKET


def test_select_native_transport_provider_rejects_invalid_policy(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    with pytest.raises(NativeArtifactError, match="unsupported native transport policy"):
        select_native_transport_provider(
            "prefer-stdio",
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
        )


def test_select_native_transport_provider_rejects_unspecified_supported_transport(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    with pytest.raises(NativeArtifactError, match="unsupported native transport id"):
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            supported_transports=(TransportId.UNSPECIFIED,),
        )


def test_select_native_transport_provider_reports_remote_unsupported_transport(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "ipc")
    _write_provider_artifact(tmp_path, "quic")

    selection = select_native_transport_provider(
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        supported_transports=(TransportId.TCP, TransportId.QUIC),
    )

    assert selection.selected_transport_id is TransportId.QUIC
    assert selection.rejected == (
        native_module.NativeTransportRejection(
            provider_name="ipc",
            transport_name="ipc",
            transport_id=TransportId.IPC,
            reason="remote_unsupported",
            diagnostic="native transport was not declared by the remote endpoint",
        ),
    )


def test_select_native_transport_provider_scores_probe_samples(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "quic")
    _write_provider_artifact(tmp_path, "ipc")

    selection = select_native_transport_provider(
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        probe_samples=[
            NativeTransportProbeSample(
                provider_name="tcp",
                transport_name="tcp",
                elapsed_us=1_500,
                rtt_us=1_500,
                bytes_sent=128,
                bytes_received=128,
            ),
            NativeTransportProbeSample(
                provider_name="quic",
                transport_name="quic",
                elapsed_us=800,
                rtt_us=800,
                bytes_sent=512,
                bytes_received=512,
            ),
            NativeTransportProbeSample(
                provider_name="ipc",
                transport_name="ipc",
                elapsed_us=2_000,
                timed_out=True,
                failed=True,
            ),
        ],
    )

    assert selection.selected_transport_id is TransportId.QUIC
    assert selection.selected_probe_score is not None
    assert selection.selected_probe_score.median_rtt_us == 800
    assert [candidate.transport_name for candidate in selection.probe_candidates] == ["quic", "tcp"]
    assert selection.rejected[-1].reason == "probe_failed"
    assert selection.diagnostic == "native transport selected by probe score"


def test_select_native_transport_provider_requires_probe_samples_for_probe_mode(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "quic")

    with pytest.raises(NativeArtifactError, match="probe_missing"):
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            probe_samples=[],
        )


def test_resolve_native_artifact_uses_current_platform_when_not_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    artifact_dir = tmp_path / "windows-x86_64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    assert resolve_native_artifact(tmp_path) == artifact


def test_resolve_native_artifact_rejects_missing_artifact(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError, match="native artifact was not found"):
        resolve_native_artifact(tmp_path, NativePlatform("linux", "x86_64"))


def test_load_native_library_surfaces_loader_errors(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"not-a-real-dll")

    with pytest.raises(NativeArtifactError, match="failed to load native artifact"):
        load_native_library(artifact)


def test_probe_native_artifact_accepts_matching_protocol(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    result = probe_native_artifact(artifact, library=FakeLibrary())

    assert result.artifact_path == artifact
    assert result.abi_major == 1
    assert result.abi_minor == 5
    assert result.abi_patch == 0
    assert result.protocol_major == 1
    assert result.protocol_wire_format == 0
    assert result.sdk_channel == 3
    assert result.sdk_revision == 6
    assert result.transport_slots == TRANSPORT_SLOT_TCP
    assert result.feature_flags == REQUIRED_RUNTIME_FEATURES


@pytest.mark.parametrize(
    ("transport", "slot"),
    [
        ("ipc", TRANSPORT_SLOT_IPC),
        ("websocket", TRANSPORT_SLOT_WEBSOCKET),
    ],
)
def test_probe_native_artifact_accepts_transport_scoped_preview4_artifacts(
    tmp_path: Path,
    transport: str,
    slot: int,
) -> None:
    artifact = _write_provider_artifact(tmp_path, transport)

    result = probe_native_artifact(artifact, library=FakeLibrary(transport_slots=slot))

    assert result.artifact_path == artifact
    assert result.transport_slots == slot


def test_probe_native_artifact_uses_explicit_transport_slot_for_direct_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "libnnrp_ffi.so"
    artifact.write_bytes(b"fake")

    result = probe_native_artifact(
        artifact,
        transport="websocket",
        library=FakeLibrary(transport_slots=TRANSPORT_SLOT_WEBSOCKET),
    )

    assert result.transport_slots == TRANSPORT_SLOT_WEBSOCKET


def test_probe_native_artifact_resolves_path_from_root(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "linux-arm64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "libnnrp_ffi.so"
    artifact.write_bytes(b"fake")

    result = probe_native_artifact(
        root=tmp_path,
        native_platform=NativePlatform("linux", "arm64"),
        library=FakeLibrary(),
    )

    assert result.artifact_path == artifact


def test_probe_native_artifact_rejects_protocol_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="protocol mismatch"):
        probe_native_artifact(artifact, library=FakeLibrary(protocol_major=2, wire_format=0))


def test_probe_native_artifact_rejects_abi_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="ABI mismatch"):
        probe_native_artifact(artifact, library=FakeLibrary(abi_major=2))


def test_probe_native_artifact_rejects_missing_required_feature(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="required runtime feature flags"):
        probe_native_artifact(artifact, library=FakeLibrary(feature_flags=REQUIRED_RUNTIME_FEATURES & ~1))


def test_probe_native_artifact_rejects_missing_tcp_transport_slot(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="required transport slots"):
        probe_native_artifact(artifact, library=FakeLibrary(transport_slots=0))


def test_probe_native_artifact_rejects_missing_probe_symbol(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="missing nnrp_runtime_capabilities"):
        probe_native_artifact(artifact, library=object())


def test_probe_native_artifact_rejects_invalid_probe_shape(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="invalid runtime capabilities shape"):
        probe_native_artifact(artifact, library=InvalidCapabilitiesLibrary())


def test_load_native_runtime_validates_probe_before_binding_entrypoints(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeEntrypointLibrary()
    library.nnrp_runtime_capabilities.value = FakeLibrary().nnrp_runtime_capabilities()

    runtime = load_native_runtime(artifact, library=library)

    assert runtime.submit is library.nnrp_submit


def test_native_schema_codec_delegates_descriptor_parse_write_and_validation(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    codec = load_native_schema_codec(artifact, library=library)
    schema = SchemaDescriptorHeader(
        schema_id=0x1001,
        schema_version=3,
        profile_id=StandardProfile.TOKEN,
        min_version_major=1,
        max_version_major=1,
        default_stream_semantics=StreamSemantics.APPEND,
        schema_hash=0x6E6E_7270_746F_6B33,
    )
    descriptor = Preview3TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        descriptor_flags=TypedPayloadDescriptorFlags.PARTIAL,
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        stream_semantics=StreamSemantics.APPEND,
        offset=8,
        length=13,
    )

    assert isinstance(codec, NativeSchemaCodec)
    assert codec.parse_schema_descriptor(schema.pack()) == schema
    assert codec.write_schema_descriptor(schema) == schema.pack()
    assert codec.token_delta_schema_descriptor() == token_delta_schema_descriptor()
    assert codec.parse_typed_payload_descriptor(descriptor.pack()) == descriptor
    assert codec.write_typed_payload_descriptor(descriptor) == descriptor.pack()
    codec.validate_typed_payload_binding((schema,), descriptor)

    with pytest.raises(NativeProtocolError) as mismatch:
        codec.validate_typed_payload_binding((), descriptor)

    assert mismatch.value.status.error_family == ERROR_FAMILY_SCHEMA
    assert mismatch.value.status.error_family_name == "schema"
    assert mismatch.value.status.protocol_error_code == 0x3002
    assert mismatch.value.status.detail_code == 0x42


def test_native_schema_codec_preserves_schema_mismatch_status_fields(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    codec = load_native_schema_codec(artifact, library=FakeRuntimeLibrary())
    schema = SchemaDescriptorHeader(
        schema_id=0x1001,
        schema_version=2,
        profile_id=StandardProfile.TOKEN,
        default_stream_semantics=StreamSemantics.APPEND,
        schema_hash=0x1111,
    )
    descriptor = Preview3TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        descriptor_flags=TypedPayloadDescriptorFlags.PARTIAL,
        schema_id=0x1001,
        schema_version=3,
        stream_semantics=StreamSemantics.APPEND,
        offset=8,
        length=13,
    )

    with pytest.raises(NativeProtocolError) as mismatch:
        codec.validate_typed_payload_binding((schema,), descriptor)

    assert mismatch.value.status.error_family == ERROR_FAMILY_SCHEMA
    assert mismatch.value.status.error_family_name == "schema"
    assert mismatch.value.status.protocol_error_code == 0x3001
    assert mismatch.value.status.detail_code == 0x41


def test_native_schema_registry_delegates_handle_lifecycle_and_binding_validation(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    entrypoints = load_native_runtime(artifact, library=library)
    registry = NativeSchemaRegistry.create(entrypoints)
    schema = SchemaDescriptorHeader(
        schema_id=0x1001,
        schema_version=3,
        profile_id=StandardProfile.TOKEN,
        default_stream_semantics=StreamSemantics.APPEND,
        schema_hash=0x1234,
    )
    descriptor = Preview3TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        descriptor_flags=TypedPayloadDescriptorFlags.PARTIAL,
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        stream_semantics=StreamSemantics.APPEND,
        offset=0,
        length=8,
    )

    assert isinstance(registry.handle, NativeSchemaRegistryHandle)
    assert registry.install(schema) is SchemaRegistryAction.INSTALLED
    assert registry.install(schema) is SchemaRegistryAction.ALREADY_INSTALLED
    assert registry.lookup(schema.schema_id, schema.schema_version) == schema
    registry.validate_typed_payload_binding(descriptor)
    assert registry.invalidate(schema.schema_id, schema.schema_version) is SchemaRegistryAction.INVALIDATED

    with pytest.raises(NativeProtocolError):
        registry.lookup(schema.schema_id, schema.schema_version)

    registry.close()
    with pytest.raises(NativeInvalidStateError):
        registry.install(schema)


def test_native_owned_buffer_acquires_views_and_releases_handle(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    client = load_native_client(artifact, library=FakeRuntimeLibrary())
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    buffer = connection.acquire_buffer_copy(b"native-copy")

    assert isinstance(buffer.handle, NativeBufferHandle)
    assert buffer.view.length == len(b"native-copy")
    assert buffer.to_bytes() == b"native-copy"
    assert buffer.refresh_view().length == len(b"native-copy")

    buffer.close()
    with pytest.raises(NativeInvalidStateError):
        buffer.to_bytes()


def test_native_recovery_codec_delegates_resume_and_migration_validation(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    codec = load_native_recovery_codec(artifact, library=library)
    session_open_metadata = b"session-open"
    session_open_ack_metadata = b"session-open-ack"
    migrate_metadata = b"session-migrate"
    migrate_ack = SessionMigrateAckMetadata(
        accept_code=0,
        resume_from_frame_id=45,
        grace_window_ms=250,
        server_migrate_ts_us=4000,
    ).pack()

    codec.validate_session_recovery_request(session_open_metadata)
    outcome = codec.validate_session_recovery_ack(session_open_metadata, session_open_ack_metadata)
    codec.validate_migration_recovery(migrate_metadata, migrate_ack)

    assert isinstance(codec, NativeRecoveryCodec)
    assert isinstance(outcome, NativeSessionRecoveryOutcome)
    assert outcome.outcome_code == SESSION_RECOVERY_OUTCOME_RESUMED
    assert outcome.resume_window_ms == 250
    assert outcome.outcome_name == "resumed"
    assert outcome.resumed is True
    assert outcome.resume_enabled is False
    assert codec.should_replay_frame_after_migration(migrate_ack, 45) is True
    assert codec.should_replay_frame_after_migration(migrate_ack, 44) is False
    assert library.nnrp_session_recovery_request_validate.calls[0][0].len == len(session_open_metadata)
    assert library.nnrp_migration_should_replay_frame.calls[0][1] == 45

    with pytest.raises(ValueError, match="frame_id"):
        codec.should_replay_frame_after_migration(migrate_ack, -1)
    with pytest.raises(NativeInvalidArgumentError):
        codec.validate_session_recovery_request(b"")


def test_native_session_recovery_outcome_exposes_known_state_names() -> None:
    outcome = NativeSessionRecoveryOutcome(SESSION_RECOVERY_OUTCOME_RESUME_ENABLED, 500)

    assert outcome.outcome_name == "resume_enabled"
    assert outcome.resume_enabled is True
    assert outcome.is_fresh is False
    assert outcome.resume_rejected is False
    assert NativeSessionRecoveryOutcome(0xFFFF).outcome_name == "unknown"


def test_load_native_runtime_rejects_probe_mismatch_before_binding_entrypoints(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeEntrypointLibrary(missing_symbol="nnrp_submit")
    library.nnrp_runtime_capabilities.value = FakeLibrary(abi_major=2).nnrp_runtime_capabilities()

    with pytest.raises(NativeArtifactError, match="ABI mismatch"):
        load_native_runtime(artifact, library=library)


def test_select_native_runtime_backend_prefers_valid_native_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    fallback = FakeBackend()
    library = FakeRuntimeLibrary()

    backend = select_native_runtime_backend(artifact, library=library, fallback=fallback)

    assert isinstance(backend, NativeRuntimeClient)
    assert isinstance(backend, NativeRuntimeBackend)
    assert fallback.connections == []


def test_select_native_runtime_backend_uses_fallback_when_native_missing(tmp_path: Path) -> None:
    fallback = FakeBackend()

    backend = select_native_runtime_backend(tmp_path / "missing.dll", fallback=fallback)

    assert backend is fallback


def test_select_native_runtime_backend_can_require_native(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError, match="failed to load native artifact|was not found"):
        select_native_runtime_backend(tmp_path / "missing.dll", fallback=FakeBackend(), require_native=True)


def test_native_runtime_client_runs_connection_session_submit_close_roundtrip(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()

    client = load_native_client(artifact, library=library)
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    session = connection.open_session(
        requested_session_id=41,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
        priority_class=NativeSessionPriorityClass.INTERACTIVE,
    )
    operation = session.submit(operation_id=99, frame_id=7, payload=b"payload")
    operation_scope = session.submit_operation(
        operation_id=100,
        frame_id=8,
        payload=b"payload",
        scheduling_hint=NativeOperationSchedulingHint(
            parent_operation_id=99,
            operation_group_id=1234,
            deadline_ms=250,
        ),
    )
    connection.control(control_code=10, payload=b"connection-control")
    operation_scope.cancel()
    session.cancel(frame_id=7)
    session.control(control_code=11, payload=b"session-control")
    session.close()

    assert isinstance(client, NativeRuntimeClient)
    assert isinstance(connection, NativeRuntimeConnection)
    assert isinstance(session, NativeRuntimeSession)
    assert connection.handle.handle.id == 11
    assert session.connection.handle.id == 11
    assert session.handle.handle.id == 41
    assert session.priority_class is NativeSessionPriorityClass.INTERACTIVE
    assert operation.handle.id == 99
    assert isinstance(operation_scope, NativeRuntimeOperation)
    assert operation_scope.operation_id == 100
    assert operation_scope.frame_id == 8
    assert operation_scope.parent_operation_id == 99
    assert operation_scope.operation_group_id == 1234
    assert operation_scope.scheduling_hint.deadline_ms == 250
    submit_request = library.nnrp_client_submit.calls[0][0]
    assert submit_request.frame_id == 7
    assert submit_request.payload.len == 7
    scheduled_submit_request = library.nnrp_client_submit.calls[1][0]
    assert scheduled_submit_request.operation_id == 100
    assert not hasattr(scheduled_submit_request, "parent_operation_id")
    assert not hasattr(scheduled_submit_request, "operation_group_id")
    assert not hasattr(scheduled_submit_request, "deadline_ms")
    assert library.nnrp_control.calls[0][0].control_code == 10
    assert library.nnrp_control.calls[0][0].payload.len == len(b"connection-control")
    assert library.nnrp_client_cancel.calls[0][0].frame_id == 8
    assert library.nnrp_client_cancel.calls[1][0].frame_id == 7
    assert library.nnrp_control.calls[1][0].control_code == 11
    assert library.nnrp_control.calls[1][0].payload.len == len(b"session-control")


def test_native_submit_request_shape_matches_frozen_ffi_without_private_scheduling_fields() -> None:
    assert [name for name, _field_type in _NnrpSubmitRequest._fields_] == [
        "session",
        "operation_id",
        "frame_id",
        "payload",
    ]


def test_native_connection_resumes_session_through_executable_resume_abi(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    connection = load_native_client(artifact, library=library).connect(
        connection_id=11,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    session, outcome = connection.resume_session(
        requested_session_id=41,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
        resume_token_bytes=24,
    )

    assert isinstance(session, NativeRuntimeSession)
    assert session.handle.handle.id == 41
    assert outcome.outcome_code == SESSION_RECOVERY_OUTCOME_RESUMED
    assert outcome.resume_window_ms == 240
    resume_request = library.nnrp_client_resume_session.calls[0][0]
    assert resume_request.connection.id == 11
    assert resume_request.resume_token_bytes == 24


def test_native_resumed_session_can_submit_operations(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    connection = load_native_client(artifact, library=library).connect(
        connection_id=11,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session, outcome = connection.resume_session(
        requested_session_id=41,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
        resume_token_bytes=24,
    )

    operation = session.submit_operation(operation_id=99, frame_id=7, payload=b"after-resume")

    assert outcome.resumed is True
    assert operation.session == session.handle
    assert operation.operation_id == 99
    submit_request = library.nnrp_client_submit.calls[0][0]
    assert submit_request.session.id == 41
    assert submit_request.payload.len == len(b"after-resume")


def test_native_cache_backend_routes_lease_ops_through_ffi(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(
            connection_id=11,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    backend = session.cache_backend(now_ms=1000, ttl_ms=500, expected_version=9)
    identity = CacheObjectIdentity(namespace=1, object_kind=2, key_hi=3, key_lo=4)

    query = backend.query_cache(identity)
    touch = backend.touch_cache(identity, ttl_ms=750)
    prefetched = backend.prefetch_cache((identity,))
    released = backend.release_cache(identity)
    missing = backend.release_cache(identity)

    assert isinstance(backend, NativeCacheLeaseBackend)
    assert query.outcome is CacheLeaseOutcome.VALID
    assert query.lease is not None
    assert query.object_version is not None
    assert query.object_version.object_version == 9
    assert touch.lease is not None
    assert prefetched[0].identity == identity
    assert released.outcome is CacheLeaseOutcome.RELEASED
    assert missing.outcome is CacheLeaseOutcome.MISSING
    cache_request = library.nnrp_cache_query.calls[0][0]
    assert cache_request.owner.id == 41
    assert cache_request.object_id.cache_namespace == 1
    assert cache_request.object_id.object_kind == 2
    assert library.nnrp_cache_touch.calls[0][0].ttl_ms == 750


def test_native_cache_backend_preserves_expired_lease_result_from_protocol_status(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    session = (
        load_native_client(artifact, library=ExpiringCacheRuntimeLibrary())
        .connect(
            connection_id=11,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    backend = session.cache_backend(now_ms=5000, expected_version=9)
    identity = CacheObjectIdentity(namespace=1, object_kind=2, key_hi=3, key_lo=4)

    result = backend.query_cache(identity)

    assert result.outcome is CacheLeaseOutcome.EXPIRED
    assert result.lease is not None
    assert result.lease.is_expired(5000) is True
    assert result.object_version is not None
    assert result.object_version.object_version == 9


def test_native_scheduling_models_validate_frozen_value_ranges() -> None:
    assert NativeSessionPriorityClass.from_code(0) is NativeSessionPriorityClass.INTERACTIVE
    assert NativeSessionPriorityClass.from_code(1) is NativeSessionPriorityClass.BALANCED
    assert NativeSessionPriorityClass.from_code(2) is NativeSessionPriorityClass.BACKGROUND
    assert NativeSessionPriorityClass.BACKGROUND.code == 2
    assert NativeOperationSchedulingHint(
        parent_operation_id=99,
        operation_group_id=1234,
        deadline_ms=250,
    ).has_scope

    with pytest.raises(NativeHandleError, match="unknown native session priority class"):
        NativeSessionPriorityClass.from_code(3)
    with pytest.raises(NativeHandleError, match="deadline_ms"):
        NativeOperationSchedulingHint(deadline_ms=0x1_0000_0000)
    with pytest.raises(NativeHandleError, match="parent_operation_id"):
        NativeOperationSchedulingHint(parent_operation_id=-1)


def test_native_submit_rejects_conflicting_scheduling_hint_scope(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(
            connection_id=11,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    with pytest.raises(NativeHandleError, match="parent_operation_id conflicts"):
        session.submit_operation(
            operation_id=100,
            frame_id=8,
            parent_operation_id=101,
            scheduling_hint=NativeOperationSchedulingHint(parent_operation_id=99),
        )


def test_native_runtime_connection_can_open_multiple_sessions(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()

    connection = load_native_client(artifact, library=library).connect(
        connection_id=11,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    first_session = connection.open_session(
        requested_session_id=41,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    second_session = connection.open_session(
        requested_session_id=42,
        generation=4,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )

    first_operation = first_session.submit_operation(operation_id=99, frame_id=7)
    second_operation = second_session.submit_operation(operation_id=100, frame_id=8)

    assert first_session.connection == second_session.connection == connection.handle
    assert first_session.handle.handle.id == 41
    assert second_session.handle.handle.id == 42
    assert first_operation.session == first_session.handle
    assert second_operation.session == second_session.handle
    assert library.nnrp_client_open_session.calls[0][0].requested_session_id == 41
    assert library.nnrp_client_open_session.calls[1][0].requested_session_id == 42
    assert library.nnrp_client_submit.calls[0][0].session.id == 41
    assert library.nnrp_client_submit.calls[1][0].session.id == 42


def test_native_runtime_client_bootstraps_and_awaits_empty_event(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()

    connection = load_native_client(artifact, library=library).bootstrap_connection(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result = connection.await_event()

    assert connection.handle.handle.id == 12
    assert isinstance(result, NativeRuntimePollResult)
    assert result.event is None


def test_native_runtime_event_snapshot_copies_payload(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")

    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result = connection.await_event()

    assert result.event is not None
    assert isinstance(result.event, NativeRuntimeEvent)
    assert result.event.kind == 6
    assert result.event.payload == b"result"
    assert result.event.connection.id == 12
    assert result.event.session.id == 41
    assert result.event.operation.id == 99
    assert result.event.diagnostic.status.succeeded is True


def test_native_runtime_event_snapshot_survives_native_buffer_reuse(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")

    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result = connection.await_event()
    assert result.event is not None

    assert library._event_payload_owner is not None
    library._event_payload_owner.value = b"reuse!"

    assert result.event.payload == b"result"


def test_native_submit_payload_boundary_snapshots_mutable_inputs(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = connection.open_session(
        requested_session_id=42,
        generation=3,
        profile_id=0,
        schema_id=0,
        schema_version=0,
    )
    payload = bytearray(b"before")

    session.submit_operation(operation_id=99, frame_id=7, payload=payload)
    payload[:] = b"after!"

    assert library.submitted_payloads == [b"before"]


def test_native_runtime_result_preserves_lifecycle_surface(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    event = connection.poll_event()

    assert event is not None
    result = NativeRuntimeResult.from_event(event)
    partial = NativeRuntimeResult.from_event(event, state=NativeOperationLifecycle.PARTIAL)
    degraded = NativeRuntimeResult.from_event(event, state=NativeOperationLifecycle.DEGRADED)
    stale = NativeRuntimeResult.from_event(event, state=NativeOperationLifecycle.STALE_REUSE)

    assert result.state is NativeOperationLifecycle.COMPLETED
    assert result.operation_id == 99
    assert result.frame_id == 7
    assert result.payload == b"result"
    assert result.diagnostic.status.succeeded is True
    assert result.diagnostic.related_connection_id == 0
    assert partial.state is NativeOperationLifecycle.PARTIAL
    assert degraded.state is NativeOperationLifecycle.DEGRADED
    assert stale.state is NativeOperationLifecycle.STALE_REUSE


def test_native_runtime_result_maps_error_and_drop_events() -> None:
    base_event = NativeRuntimeEvent(
        kind=10,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=7,
        payload=b"",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus(FFI_STATUS_INTERNAL_ERROR), 12, 41, 99, 7),
    )
    drop_event = NativeRuntimeEvent(
        kind=7,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=7,
        payload=b"",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 7),
    )

    assert NativeRuntimeResult.from_event(base_event).state is NativeOperationLifecycle.FAILED
    assert NativeRuntimeResult.from_event(base_event).diagnostic.status_name == "internal_error"
    assert NativeRuntimeResult.from_event(base_event).diagnostic.error_family_name == "none"
    assert NativeRuntimeResult.from_event(drop_event).state is NativeOperationLifecycle.CANCELLED


def test_native_runtime_event_classifies_control_and_credit_updates() -> None:
    flow_event = NativeRuntimeEvent(
        kind=EVENT_KIND_FLOW_UPDATED,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=7,
        payload=b"opaque-native-credit-state",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 7),
    )
    control_event = NativeRuntimeEvent(
        kind=EVENT_KIND_CONTROL,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=8,
        payload=b"opaque-control-state",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 8),
    )

    update = flow_event.to_credit_update()

    assert flow_event.kind_name == "flow_updated"
    assert flow_event.is_flow_update is True
    assert flow_event.is_control_event is True
    assert flow_event.is_result_event is False
    assert isinstance(update, NativeCreditUpdateEvent)
    assert update.connection.id == 12
    assert update.session.id == 41
    assert update.operation.id == 99
    assert update.frame_id == 7
    assert update.diagnostic.related_session_id == 41
    assert control_event.kind_name == "control"
    assert control_event.is_control_event is True
    assert control_event.is_flow_update is False
    with pytest.raises(NativeHandleError, match="expected native flow update event"):
        control_event.to_credit_update()


def test_native_runtime_event_wraps_result_hint_metadata() -> None:
    metadata = ResultHintMetadata(
        applied_budget_policy=ResultHintBudgetPolicy.PARTIAL,
        congestion_state=ResultHintCongestionState.ELEVATED,
        reason=ResultHintReason.SERVER_BUSY,
        retry_after_ms=125,
    )
    hint_event = NativeRuntimeEvent(
        kind=EVENT_KIND_RESULT_HINT,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=7,
        payload=metadata.pack(),
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 7),
    )
    control_event = NativeRuntimeEvent(
        kind=EVENT_KIND_CONTROL,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=8,
        payload=b"opaque-control-state",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 8),
    )

    hint = NativeResultHintEvent.from_event(hint_event)

    assert hint.connection.id == 12
    assert hint.session.id == 41
    assert hint.operation.id == 99
    assert hint.frame_id == 7
    assert hint.payload == metadata.pack()
    assert hint.event is hint_event
    assert hint.diagnostic.related_operation_id == 99
    assert hint.metadata == metadata
    with pytest.raises(NativeHandleError, match="expected native result hint event"):
        NativeResultHintEvent.from_event(control_event)


def test_native_runtime_connection_polls_event_delivery_model(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    event = connection.poll_event()
    events = connection.poll_events(max_events=1)
    async_event = asyncio.run(connection.async_poll_event())
    async_events = asyncio.run(_collect_async_events(connection))

    assert event is not None
    assert event.payload == b"result"
    assert [polled.payload for polled in events] == [b"result"]
    assert library.nnrp_client_await_events.calls[0][2] == 1
    assert [polled.session.id for polled in connection.poll_events_batch(max_events=2)] == [41, 42]
    assert connection.poll_events_batch(max_events=2, event_kind=EVENT_KIND_CONTROL) == ()
    assert async_event is not None
    assert async_event.payload == b"result"
    assert [polled.payload for polled in async_events] == [b"result"]

    with pytest.raises(ValueError, match="max_events"):
        connection.poll_events(max_events=-1)
    with pytest.raises(ValueError, match="max_events"):
        connection.poll_events_batch(max_events=-1)
    assert connection.poll_events_batch(max_events=0) == ()


def test_native_runtime_connection_batch_poll_maps_would_block_to_empty(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    assert connection.poll_events_batch(max_events=4) == ()
    assert library.nnrp_client_await_events.calls[0][2] == 4


def test_native_runtime_connection_poll_events_without_limit_filters_until_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    connection = load_native_client(artifact, library=FakeRuntimeLibrary(event_payload=b"ignored")).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    control_event = NativeRuntimeEvent(
        kind=EVENT_KIND_CONTROL,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=7,
        payload=b"control",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 7),
    )
    result_event = NativeRuntimeEvent(
        kind=EVENT_KIND_RESULT_PUSHED,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 42, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 100, 1),
        frame_id=8,
        payload=b"result",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 42, 100, 8),
    )
    queued_events: list[NativeRuntimeEvent | None] = [control_event, result_event, None]

    def poll_event_once(self: NativeRuntimeConnection) -> NativeRuntimeEvent | None:
        return queued_events.pop(0)

    monkeypatch.setattr(NativeRuntimeConnection, "poll_event", poll_event_once)

    assert connection.poll_events(event_kind=EVENT_KIND_RESULT_PUSHED) == (result_event,)


def test_native_runtime_connection_filters_credit_update_events(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"credits", event_kind=EVENT_KIND_FLOW_UPDATED)
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    updates = connection.poll_credit_updates(max_events=1)
    async_updates = asyncio.run(_collect_async_credit_updates(connection))
    async_control_events = asyncio.run(_collect_async_events_by_kind(connection, EVENT_KIND_CONTROL))

    assert len(updates) == 1
    assert updates[0].connection.id == 12
    assert updates[0].session.id == 41
    assert updates[0].operation.id == 99
    assert updates[0].frame_id == 7
    assert updates[0].diagnostic.status.succeeded is True
    assert len(async_updates) == 1
    assert async_updates[0].session.id == 41
    assert async_control_events == []
    assert not hasattr(updates[0], "credits")


def test_native_runtime_connection_filters_result_hint_events(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    metadata = ResultHintMetadata(
        applied_budget_policy=ResultHintBudgetPolicy.PARTIAL,
        congestion_state=ResultHintCongestionState.SATURATED,
        reason=ResultHintReason.BUDGET_EXCEEDED,
        retry_after_ms=250,
    )
    library = FakeRuntimeLibrary(event_payload=metadata.pack(), event_kind=EVENT_KIND_RESULT_HINT)
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    hints = connection.poll_result_hints(max_events=1)
    async_hints = asyncio.run(_collect_async_result_hints(connection))

    assert len(hints) == 1
    assert hints[0].metadata == metadata
    assert hints[0].connection.id == 12
    assert hints[0].session.id == 41
    assert hints[0].operation.id == 99
    assert hints[0].frame_id == 7
    assert len(async_hints) == 1
    assert async_hints[0].metadata.retry_after_ms == 250


def test_native_runtime_connection_wraps_payload_family_events(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b'{"delta":"ok"}', event_kind=EVENT_KIND_RESULT_PUSHED)
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    structured = connection.poll_structured_events(max_events=1)
    tool_deltas = connection.poll_tool_deltas(max_events=1)
    workflow_states = connection.poll_workflow_states(max_events=1)
    async_structured = asyncio.run(_collect_async_structured_events(connection))
    async_tool_deltas = asyncio.run(_collect_async_tool_deltas(connection))
    async_workflow_states = asyncio.run(_collect_async_workflow_states(connection))

    assert structured[0].payload_family == "structured_event"
    assert structured[0].is_structured_event is True
    assert structured[0].payload == b'{"delta":"ok"}'
    assert isinstance(structured[0], NativePayloadFamilyEvent)
    assert tool_deltas[0].payload_family == "tool_delta"
    assert tool_deltas[0].is_tool_delta is True
    assert workflow_states[0].payload_family == "workflow_state"
    assert workflow_states[0].is_workflow_state is True
    assert [event.payload for event in async_structured] == [b'{"delta":"ok"}']
    assert [event.is_structured_event for event in async_structured] == [True]
    assert [event.payload_family for event in async_tool_deltas] == ["tool_delta"]
    assert [event.payload for event in async_tool_deltas] == [b'{"delta":"ok"}']
    assert [event.is_tool_delta for event in async_tool_deltas] == [True]
    assert [event.payload_family for event in async_workflow_states] == ["workflow_state"]


def test_native_runtime_connection_dispatches_callbacks(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    result_library = FakeRuntimeLibrary(event_payload=b'{"delta":"ok"}', event_kind=EVENT_KIND_RESULT_PUSHED)
    result_connection = load_native_client(artifact, library=result_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    credit_library = FakeRuntimeLibrary(event_payload=b"credits", event_kind=EVENT_KIND_FLOW_UPDATED)
    credit_connection = load_native_client(artifact, library=credit_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    raw_payloads: list[bytes] = []
    structured_payloads: list[bytes] = []
    tool_payloads: list[bytes] = []
    credit_frames: list[int] = []
    hint_retries: list[int] = []

    raw_count = result_connection.dispatch_events(lambda event: raw_payloads.append(event.payload), max_events=1)
    structured_count = result_connection.dispatch_structured_events(
        lambda event: structured_payloads.append(event.payload),
        max_events=1,
    )
    tool_count = result_connection.dispatch_tool_deltas(
        lambda event: tool_payloads.append(event.payload),
        max_events=1,
    )
    credit_count = credit_connection.dispatch_credit_updates(
        lambda update: credit_frames.append(update.frame_id),
        max_events=1,
    )
    hint_library = FakeRuntimeLibrary(
        event_payload=ResultHintMetadata(retry_after_ms=75).pack(),
        event_kind=EVENT_KIND_RESULT_HINT,
    )
    hint_connection = load_native_client(artifact, library=hint_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    hint_count = hint_connection.dispatch_result_hints(
        lambda hint: hint_retries.append(hint.metadata.retry_after_ms),
        max_events=1,
    )

    assert raw_count == 1
    assert structured_count == 1
    assert tool_count == 1
    assert credit_count == 1
    assert hint_count == 1
    assert raw_payloads == [b'{"delta":"ok"}']
    assert structured_payloads == [b'{"delta":"ok"}']
    assert tool_payloads == [b'{"delta":"ok"}']
    assert credit_frames == [7]
    assert hint_retries == [75]


def test_native_runtime_connection_dispatches_payload_family_callbacks_by_event_kind(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    result_library = FakeRuntimeLibrary(event_payload=b'{"result":true}', event_kind=EVENT_KIND_RESULT_PUSHED)
    result_connection = load_native_client(artifact, library=result_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    control_library = FakeRuntimeLibrary(event_payload=b'{"control":true}', event_kind=EVENT_KIND_CONTROL)
    control_connection = load_native_client(artifact, library=control_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    structured_events: list[tuple[str, bytes, int]] = []
    tool_deltas: list[tuple[str, bytes, int]] = []

    structured_count = control_connection.dispatch_payload_family_events(
        "structured_event",
        lambda event: structured_events.append((event.payload_family, event.payload, event.event.kind)),
        max_events=1,
        event_kind=EVENT_KIND_CONTROL,
    )
    tool_count = result_connection.dispatch_payload_family_events(
        "tool_delta",
        lambda event: tool_deltas.append((event.payload_family, event.payload, event.event.kind)),
        max_events=1,
        event_kind=EVENT_KIND_RESULT_PUSHED,
    )

    assert structured_count == 1
    assert tool_count == 1
    assert structured_events == [("structured_event", b'{"control":true}', EVENT_KIND_CONTROL)]
    assert tool_deltas == [("tool_delta", b'{"result":true}', EVENT_KIND_RESULT_PUSHED)]


def test_native_runtime_connection_maps_callback_rejection(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"payload", event_kind=EVENT_KIND_RESULT_PUSHED)
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    def reject(_event: NativePayloadFamilyEvent) -> None:
        raise ValueError("host rejected payload")

    with pytest.raises(NativeCallbackRejectedError) as captured:
        connection.dispatch_tool_deltas(reject, max_events=1)

    assert captured.value.status.status_code == FFI_STATUS_CALLBACK_REJECTED
    assert isinstance(captured.value.__cause__, ValueError)


def test_native_payload_family_event_rejects_unknown_family_and_non_payload_event(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    result_library = FakeRuntimeLibrary(event_payload=b"payload", event_kind=EVENT_KIND_RESULT_PUSHED)
    result_connection = load_native_client(artifact, library=result_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    with pytest.raises(NativeHandleError, match="unknown native payload family"):
        result_connection.poll_payload_family_events("private_family", max_events=1)

    flow_library = FakeRuntimeLibrary(event_payload=b"credits", event_kind=EVENT_KIND_FLOW_UPDATED)
    flow_connection = load_native_client(artifact, library=flow_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    with pytest.raises(NativeHandleError, match="expected native result/control event"):
        flow_connection.poll_payload_family_events(
            "structured_event",
            max_events=1,
            event_kind=EVENT_KIND_FLOW_UPDATED,
        )


def test_native_control_event_iterator_propagates_cancellation(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(
        event_payload=b"credits",
        event_kind=EVENT_KIND_FLOW_UPDATED,
        await_event_delay_seconds=0.05,
    )
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    asyncio.run(_cancel_async_credit_updates(connection))


def test_native_runtime_connection_rejects_use_after_close(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    connection.close()

    assert library.nnrp_client_close_connection.calls[0][0].id == 12
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.poll_event()
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.control(control_code=10)
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.close()


def test_native_runtime_session_submits_and_polls_result(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    result = session.submit_and_poll_result(
        operation_id=99,
        frame_id=7,
        payload=b"payload",
        result_payload=b"result",
        state=NativeOperationLifecycle.PARTIAL,
        max_events=1,
    )
    async_result = asyncio.run(
        session.async_submit_and_poll_result(
            operation_id=99,
            frame_id=7,
            payload=b"payload",
            result_payload=b"result",
            max_events=1,
        )
    )

    assert result.state is NativeOperationLifecycle.PARTIAL
    assert result.operation_id == 99
    assert result.frame_id == 7
    assert result.payload == b"result"
    assert result.event is result.event
    assert result.event.operation.id == 99
    assert result.event.payload == b"result"
    assert async_result.state is NativeOperationLifecycle.COMPLETED
    assert async_result.payload == b"result"
    assert library.nnrp_client_submit_result_compact.calls[0][0].operation_id == 99
    assert library.nnrp_client_submit_result.calls == []
    assert library.nnrp_client_submit.calls == []
    assert library.nnrp_client_complete_operation.calls == []
    assert library.nnrp_client_await_events.calls == []


def test_native_runtime_session_submit_result_prefers_cffi_api_when_available(tmp_path: Path) -> None:
    class FakeCffi:
        def from_buffer(self, payload: bytes) -> bytes:
            return payload

        def new(self, type_name: str) -> SimpleNamespace:
            assert type_name == "NnrpPyCompactResult *"
            return SimpleNamespace(
                status_code=0,
                error_family=0,
                protocol_error_code=0,
                detail_code=0,
                has_result=0,
                event_kind=0,
                result_state=0,
                operation_id=0,
                frame_id=0,
                payload_len=0,
            )

    class FakeCffiLibrary:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def nnrp_py_client_submit_result_compact(self, *args: object) -> int:
            self.calls.append(args)
            out_result = args[-1]
            out_result.status_code = FFI_STATUS_OK
            out_result.error_family = 0
            out_result.protocol_error_code = 0
            out_result.detail_code = 0
            out_result.has_result = 1
            out_result.event_kind = EVENT_KIND_RESULT_PUSHED
            out_result.result_state = RESULT_STATE_COMPLETED
            out_result.operation_id = args[5]
            out_result.frame_id = args[6]
            out_result.payload_len = args[8]
            return 0

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"ctypes-result")
    cffi_library = FakeCffiLibrary()
    cffi_api = _NativeCffiSubmitResultApi(FakeCffi(), cffi_library, b"fake-native")
    session = (
        load_native_client(artifact, library=library, cffi_submit_result_api=cffi_api)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    payload = b"payload"

    result = session.submit_result(
        operation_id=99,
        frame_id=7,
        payload=payload,
        result_payload=payload,
        max_events=2,
    )

    assert session.entrypoints.binding_mode == "cffi_api"
    assert result.state is NativeOperationLifecycle.COMPLETED
    assert result.operation_id == 99
    assert result.frame_id == 7
    assert result.payload == payload
    assert cffi_library.calls[0][0] == b"fake-native"
    assert cffi_library.calls[0][5:9] == (99, 7, payload, len(payload))
    assert library.nnrp_client_submit_result_compact.calls == []


def test_native_runtime_session_submit_result_falls_back_when_cffi_api_cannot_preserve_semantics(
    tmp_path: Path,
) -> None:
    class FakeCffi:
        def from_buffer(self, payload: bytes) -> bytes:
            return payload

        def new(self, _type_name: str) -> SimpleNamespace:
            return SimpleNamespace()

    class FakeCffiLibrary:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def nnrp_py_client_submit_result_compact(self, *args: object) -> int:
            self.calls.append(args)
            return 0

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    cffi_library = FakeCffiLibrary()
    cffi_api = _NativeCffiSubmitResultApi(FakeCffi(), cffi_library, b"fake-native")

    for kwargs in (
        {"payload": b"payload", "result_payload": b"result", "max_events": 2},
        {"payload": b"payload", "result_payload": b"payload", "max_events": 1},
    ):
        library = FakeRuntimeLibrary(event_payload=b"result")
        session = (
            load_native_client(artifact, library=library, cffi_submit_result_api=cffi_api)
            .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
            .open_session(
                requested_session_id=41,
                generation=3,
                profile_id=4,
                schema_id=5,
                schema_version=6,
            )
        )

        result = session.submit_result(operation_id=99, frame_id=7, **kwargs)

        assert result.payload == kwargs["result_payload"]
        assert library.nnrp_client_submit_result_compact.calls[0][0].operation_id == 99

    assert cffi_library.calls == []


def test_native_cffi_api_loader_respects_binding_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    calls: list[str] = []

    def fail_import(name: str) -> object:
        calls.append(name)
        raise ImportError("missing cffi API")

    monkeypatch.setattr(native_module.importlib, "import_module", fail_import)
    monkeypatch.delenv(NATIVE_BINDING_MODE_ENV, raising=False)

    assert _load_native_cffi_submit_result_api(artifact) is None
    assert calls == ["nnrp._nnrp_cffi_api_submit_result", "_nnrp_cffi_api_submit_result"]

    calls.clear()
    monkeypatch.setenv(NATIVE_BINDING_MODE_ENV, "ctypes")
    assert _load_native_cffi_submit_result_api(artifact) is None
    assert calls == []

    monkeypatch.setenv(NATIVE_BINDING_MODE_ENV, "cffi_api")
    with pytest.raises(NativeArtifactError, match="native cffi API binding is unavailable"):
        _load_native_cffi_submit_result_api(artifact)

    class FakeCffiLibrary:
        def nnrp_py_client_submit_result_compact(self, *_args: object) -> int:
            return 0

    monkeypatch.setattr(
        native_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(ffi=object(), lib=FakeCffiLibrary()),
    )

    api = _load_native_cffi_submit_result_api(artifact)

    assert api is not None
    assert api.library.nnrp_py_client_submit_result_compact() == 0


def test_native_runtime_session_polls_result_with_batch_when_event_budget_allows(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(operation_id=99, frame_id=7)
    result = session.poll_result(operation, max_events=2)
    second_result = session.poll_result(operation, max_events=2)

    assert result.payload == b"result"
    assert second_result.payload == b"result"
    assert library.nnrp_client_await_events.calls[0][2] == 2
    assert library.nnrp_client_await_event.calls == []


def test_native_runtime_session_submit_result_reports_would_block_when_no_event(tmp_path: Path) -> None:
    class EmptySubmitResultRuntimeLibrary(FakeRuntimeLibrary):
        def _submit_result_compact(
            self,
            request: _NnrpClientSubmitResultRequest,
            out_result: object,
        ) -> _NnrpFfiStatus:
            result_target = getattr(out_result, "_obj", None)
            if result_target is None:
                result_target = ctypes.cast(out_result, ctypes.POINTER(_NnrpCompactResult)).contents
            result_target.status = NativeStatus.ok().to_ffi()
            result_target.has_result = 0
            return self.status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = EmptySubmitResultRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    with pytest.raises(NativeWouldBlockError):
        session.submit_result(operation_id=99, frame_id=7, payload=b"payload", max_events=1)

    with pytest.raises(ValueError, match="max_events"):
        session.submit_result(operation_id=99, frame_id=7, max_events=-1)


def test_native_runtime_session_submit_result_preserves_related_diagnostic_ids(tmp_path: Path) -> None:
    class RelatedDiagnosticRuntimeLibrary(FakeRuntimeLibrary):
        def _submit_result_compact(
            self,
            request: _NnrpClientSubmitResultRequest,
            out_result: object,
        ) -> _NnrpFfiStatus:
            status = super()._submit_result_compact(request, out_result)
            result_target = getattr(out_result, "_obj", None)
            if result_target is None:
                result_target = ctypes.cast(out_result, ctypes.POINTER(_NnrpCompactResult)).contents
            result_target.diagnostic.related_connection_id = 12
            result_target.diagnostic.related_session_id = 41
            result_target.diagnostic.related_operation_id = request.operation_id
            result_target.diagnostic.related_frame_id = request.frame_id
            return status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = RelatedDiagnosticRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    result = session.submit_result(operation_id=99, frame_id=7, payload=b"payload", result_payload=b"result")

    assert result.diagnostic.related_connection_id == 12
    assert result.diagnostic.related_session_id == 41
    assert result.diagnostic.related_operation_id == 99
    assert result.diagnostic.related_frame_id == 7
    assert result.event.diagnostic.related_operation_id == 99


def test_native_runtime_session_submit_result_handles_compact_non_ok_statuses(tmp_path: Path) -> None:
    class WrapperFailureRuntimeLibrary(FakeRuntimeLibrary):
        def _submit_result_compact(
            self,
            request: _NnrpClientSubmitResultRequest,
            out_result: object,
        ) -> _NnrpFfiStatus:
            return _NnrpFfiStatus(FFI_STATUS_INTERNAL_ERROR, 0, 0, 0)

    class ResultFailureRuntimeLibrary(FakeRuntimeLibrary):
        def _submit_result_compact(
            self,
            request: _NnrpClientSubmitResultRequest,
            out_result: object,
        ) -> _NnrpFfiStatus:
            status = super()._submit_result_compact(request, out_result)
            result_target = getattr(out_result, "_obj", None)
            if result_target is None:
                result_target = ctypes.cast(out_result, ctypes.POINTER(_NnrpCompactResult)).contents
            result_target.status = _NnrpFfiStatus(FFI_STATUS_INTERNAL_ERROR, 0, 0, 0)
            return status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    for library in (WrapperFailureRuntimeLibrary(), ResultFailureRuntimeLibrary()):
        session = (
            load_native_client(artifact, library=library)
            .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
            .open_session(
                requested_session_id=41,
                generation=3,
                profile_id=4,
                schema_id=5,
                schema_version=6,
            )
        )

        with pytest.raises(NativeInternalError):
            session.submit_result(operation_id=99, frame_id=7, payload=b"payload", result_payload=b"result")


def test_native_runtime_session_submit_result_falls_back_for_unknown_compact_states(tmp_path: Path) -> None:
    class UnknownStateRuntimeLibrary(FakeRuntimeLibrary):
        def _submit_result_compact(
            self,
            request: _NnrpClientSubmitResultRequest,
            out_result: object,
        ) -> _NnrpFfiStatus:
            status = super()._submit_result_compact(request, out_result)
            result_target = getattr(out_result, "_obj", None)
            if result_target is None:
                result_target = ctypes.cast(out_result, ctypes.POINTER(_NnrpCompactResult)).contents
            result_target.result_state = 999
            return status

    class UnknownErrorStateRuntimeLibrary(UnknownStateRuntimeLibrary):
        def _submit_result_compact(
            self,
            request: _NnrpClientSubmitResultRequest,
            out_result: object,
        ) -> _NnrpFfiStatus:
            status = super()._submit_result_compact(request, out_result)
            result_target = getattr(out_result, "_obj", None)
            if result_target is None:
                result_target = ctypes.cast(out_result, ctypes.POINTER(_NnrpCompactResult)).contents
            result_target.event_kind = EVENT_KIND_ERROR
            return status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    completed_session = (
        load_native_client(artifact, library=UnknownStateRuntimeLibrary())
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    failed_session = (
        load_native_client(artifact, library=UnknownErrorStateRuntimeLibrary())
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    completed = completed_session.submit_result(
        operation_id=99,
        frame_id=7,
        payload=b"payload",
        result_payload=bytearray(b"result"),
    )
    failed = failed_session.submit_result(
        operation_id=99,
        frame_id=7,
        payload=b"payload",
        result_payload=bytearray(b"result"),
    )

    assert completed.state is NativeOperationLifecycle.COMPLETED
    assert completed.payload == b"result"
    assert failed.state is NativeOperationLifecycle.FAILED


def test_native_runtime_session_batch_poll_reports_would_block_when_no_result(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(operation_id=99, frame_id=7)

    with pytest.raises(NativeWouldBlockError):
        session.poll_result(operation, max_events=2)


def test_native_runtime_session_batch_poll_skips_mismatched_events(tmp_path: Path) -> None:
    class MismatchedRuntimeLibrary(FakeRuntimeLibrary):
        def _await_events(
            self,
            handle: _NnrpHandle,
            out_events: object,
            event_capacity: int,
            out_event_count: object,
        ) -> _NnrpFfiStatus:
            count_target = getattr(out_event_count, "_obj", None)
            if count_target is None:
                count_target = ctypes.cast(out_event_count, ctypes.POINTER(ctypes.c_size_t)).contents
            events = ctypes.cast(out_events, ctypes.POINTER(_NnrpEvent))
            events[0].kind = EVENT_KIND_RESULT_PUSHED
            events[0].connection = handle
            events[0].session = _NnrpHandle(HANDLE_KIND_SESSION, 42, 3, 0)
            events[0].operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99, 1, 0)
            events[0].frame_id = 7
            events[0].payload = _NnrpBufferView(
                ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                len(self._event_payload_owner.raw),
            )
            events[0].diagnostic.status = NativeStatus.ok().to_ffi()
            count_target.value = 1
            return self.status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = MismatchedRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(operation_id=99, frame_id=7)

    with pytest.raises(NativeWouldBlockError):
        session.poll_result(operation, max_events=2)


def test_native_runtime_session_batch_poll_skips_submit_accepted_events(tmp_path: Path) -> None:
    class MixedEventRuntimeLibrary(FakeRuntimeLibrary):
        def _await_events(
            self,
            handle: _NnrpHandle,
            out_events: object,
            event_capacity: int,
            out_event_count: object,
        ) -> _NnrpFfiStatus:
            if event_capacity < 2:
                return _NnrpFfiStatus(FFI_STATUS_INVALID_ARGUMENT, 0, 0, 0)
            count_target = getattr(out_event_count, "_obj", None)
            if count_target is None:
                count_target = ctypes.cast(out_event_count, ctypes.POINTER(ctypes.c_size_t)).contents
            events = ctypes.cast(out_events, ctypes.POINTER(_NnrpEvent))
            for index, kind in enumerate((EVENT_KIND_SUBMIT_ACCEPTED, EVENT_KIND_RESULT_PUSHED)):
                events[index].kind = kind
                events[index].connection = handle
                events[index].session = _NnrpHandle(HANDLE_KIND_SESSION, 41, 3, 0)
                events[index].operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99, 1, 0)
                events[index].frame_id = 7
                events[index].payload = _NnrpBufferView(
                    ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                    len(self._event_payload_owner.raw),
                )
                events[index].diagnostic.status = NativeStatus.ok().to_ffi()
            count_target.value = 2
            return self.status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = MixedEventRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(operation_id=99, frame_id=7)
    result = session.poll_result(operation, max_events=2)

    assert result.state is NativeOperationLifecycle.COMPLETED
    assert result.payload == b"result"


def test_native_runtime_session_accepts_read_only_memoryview_payloads(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    session.submit_operation(operation_id=99, frame_id=7, payload=memoryview(b"payload"))

    assert library.submitted_payloads[-1] == b"payload"


def test_native_runtime_session_completes_and_drops_operations_through_client_abi(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    completed = session.submit_operation(operation_id=99, frame_id=7, payload=b"submit")
    session.complete_operation(completed, b"result")
    completed_result = session.poll_result(completed, max_events=1)
    dropped = session.submit_operation(operation_id=99, frame_id=7, payload=b"submit")
    session.drop_operation(dropped)
    dropped_result = session.poll_result(dropped, max_events=1)

    assert completed_result.state is NativeOperationLifecycle.COMPLETED
    assert completed_result.payload == b"result"
    assert dropped_result.state is NativeOperationLifecycle.CANCELLED
    assert library.nnrp_client_complete_operation.calls[0][0].operation.id == 99
    assert _read_buffer_view(library.nnrp_client_complete_operation.calls[0][0].payload) == b"result"
    assert library.nnrp_client_drop_operation.calls[0][0].operation.id == 99


def test_native_runtime_session_sends_flow_update_and_result_hint_through_client_aliases(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = connection.open_session(
        requested_session_id=41,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    hint_payload = ResultHintMetadata(retry_after_ms=55).pack()

    session.send_flow_update(frame_id=7)
    flow_updates = connection.poll_credit_updates(max_events=1)
    session.send_result_hint(hint_payload)
    result_hints = connection.poll_result_hints(max_events=1)

    assert library.nnrp_client_send_flow_update.calls[0][0].frame_id == 7
    assert library.nnrp_client_send_flow_update.calls[0][0].session.id == 41
    assert library.nnrp_client_send_result_hint.calls[0][0].control_code == 0x18
    assert _read_buffer_view(library.nnrp_client_send_result_hint.calls[0][0].payload) == hint_payload
    assert flow_updates[0].frame_id == 7
    assert result_hints[0].metadata.retry_after_ms == 55


def test_native_runtime_session_raises_when_result_is_not_available(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    with pytest.raises(NativeWouldBlockError):
        session.submit_and_poll_result(
            operation_id=99,
            frame_id=7,
            payload=b"payload",
            parent_operation_id=1,
            max_events=1,
        )

    with pytest.raises(ValueError, match="max_events"):
        session.poll_result(session.submit_operation(operation_id=99, frame_id=7), max_events=-1)


def test_native_runtime_session_ignores_result_for_different_session(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=42,
            generation=4,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(operation_id=99, frame_id=7)

    with pytest.raises(NativeWouldBlockError):
        session.poll_result(operation, max_events=1)


def test_native_runtime_session_rejects_use_after_close(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    operation = session.submit_operation(operation_id=99, frame_id=7)

    session.close()

    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.submit(operation_id=100, frame_id=8)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.poll_result(operation, max_events=1)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.cancel(frame_id=7)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.control(control_code=11)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.close()


def test_native_runtime_async_submit_cancels_native_frame(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = SlowSubmitRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    asyncio.run(_cancel_async_submit(session))

    assert library.nnrp_client_cancel.calls
    assert library.nnrp_client_cancel.calls[0][0].frame_id == 9


def test_native_runtime_client_raises_mapped_status_errors(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(status=_NnrpFfiStatus(FFI_STATUS_INVALID_STATE, 0, 0, 0))

    with pytest.raises(NativeInvalidStateError):
        load_native_client(artifact, library=library).connect(
            connection_id=11,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )


async def _collect_async_events(connection: NativeRuntimeConnection) -> list[NativeRuntimeEvent]:
    return [event async for event in connection.iter_events(max_events=1)]


async def _collect_async_events_by_kind(
    connection: NativeRuntimeConnection,
    event_kind: int,
) -> list[NativeRuntimeEvent]:
    return [event async for event in connection.iter_events(max_events=1, event_kind=event_kind)]


async def _collect_async_credit_updates(connection: NativeRuntimeConnection) -> list[NativeCreditUpdateEvent]:
    return [event async for event in connection.iter_credit_updates(max_events=1)]


async def _collect_async_result_hints(connection: NativeRuntimeConnection) -> list[NativeResultHintEvent]:
    return [event async for event in connection.iter_result_hints(max_events=1)]


async def _collect_async_structured_events(connection: NativeRuntimeConnection) -> list[NativePayloadFamilyEvent]:
    return [event async for event in connection.iter_structured_events(max_events=1)]


async def _collect_async_tool_deltas(connection: NativeRuntimeConnection) -> list[NativePayloadFamilyEvent]:
    return [event async for event in connection.iter_tool_deltas(max_events=1)]


async def _collect_async_workflow_states(connection: NativeRuntimeConnection) -> list[NativePayloadFamilyEvent]:
    return [event async for event in connection.iter_workflow_states(max_events=1)]


async def _cancel_async_credit_updates(connection: NativeRuntimeConnection) -> None:
    task = asyncio.create_task(_collect_async_credit_updates(connection))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def _cancel_async_submit(session: NativeRuntimeSession) -> None:
    task = asyncio.create_task(session.async_submit_operation(operation_id=101, frame_id=9, payload=b"payload"))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_native_handle_roundtrips_ffi_shape() -> None:
    handle = NativeHandle(HANDLE_KIND_CONNECTION, 7, 2, 0)

    ffi = handle.to_ffi()
    decoded = NativeHandle.from_ffi(ffi)

    assert (ffi.kind, ffi.id, ffi.generation, ffi.flags) == (HANDLE_KIND_CONNECTION, 7, 2, 0)
    assert decoded == handle
    assert decoded.is_valid is True


def test_native_handle_invalid_shape_is_zero_only() -> None:
    assert NativeHandle.invalid().to_ffi().kind == 0

    with pytest.raises(NativeHandleError, match="invalid handles"):
        NativeHandle(0, 1, 0)


def test_native_handle_requires_valid_kind_id_and_generation() -> None:
    with pytest.raises(NativeHandleError, match="uint32"):
        NativeHandle(-1, 1, 1)
    with pytest.raises(NativeHandleError, match="non-zero id"):
        NativeHandle(HANDLE_KIND_SESSION, 0, 1)
    with pytest.raises(NativeHandleError, match="non-zero id"):
        NativeHandle(HANDLE_KIND_SESSION, 1, 0)


@pytest.mark.parametrize(
    ("wrapper_type", "kind"),
    [
        (NativeConnectionHandle, HANDLE_KIND_CONNECTION),
        (NativeSessionHandle, HANDLE_KIND_SESSION),
        (NativeOperationHandle, HANDLE_KIND_OPERATION),
        (NativeEventPumpHandle, HANDLE_KIND_EVENT_PUMP),
        (NativeBufferHandle, HANDLE_KIND_BUFFER),
        (NativeSchemaRegistryHandle, HANDLE_KIND_SCHEMA_REGISTRY),
        (NativeCacheLeaseHandle, HANDLE_KIND_CACHE_LEASE),
    ],
)
def test_typed_native_handles_accept_only_matching_kind(wrapper_type: type, kind: int) -> None:
    wrapper = wrapper_type.from_ffi(_NnrpHandle(kind, 11, 3, 0))

    assert wrapper.to_ffi().kind == kind

    with pytest.raises(NativeHandleError, match="expected native handle kind"):
        mismatched_kind = HANDLE_KIND_CONNECTION if kind != HANDLE_KIND_CONNECTION else HANDLE_KIND_SESSION
        wrapper_type(NativeHandle(mismatched_kind, 11, 3))


def test_native_buffer_views_roundtrip_ffi_shape() -> None:
    view = NativeBufferView(0x1000, 64)
    mutable_view = NativeMutableBufferView(0x2000, 128)

    assert NativeBufferView.from_ffi(view.to_ffi()) == view
    assert NativeMutableBufferView.from_ffi(mutable_view.to_ffi()) == mutable_view
    assert NativeBufferView.empty().to_ffi().ptr is None
    assert NativeMutableBufferView.empty().to_ffi().ptr is None
    assert NativeBufferView.from_ffi(_NnrpBufferView(None, 0)) == NativeBufferView.empty()
    assert NativeMutableBufferView.from_ffi(_NnrpBufferViewMut(None, 0)) == NativeMutableBufferView.empty()


def test_native_buffer_views_reject_non_empty_null_pointer() -> None:
    with pytest.raises(NativeHandleError, match="non-null pointer"):
        NativeBufferView(0, 1)
    with pytest.raises(NativeHandleError, match="non-null pointer"):
        NativeMutableBufferView(0, 1)


def test_native_status_roundtrips_ffi_shape() -> None:
    status = NativeStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_CACHE, 0x22, 0x33)

    ffi = status.to_ffi()
    decoded = NativeStatus.from_ffi(ffi)

    assert (ffi.status_code, ffi.error_family, ffi.protocol_error_code, ffi.detail_code) == (
        FFI_STATUS_PROTOCOL_ERROR,
        ERROR_FAMILY_CACHE,
        0x22,
        0x33,
    )
    assert decoded == status
    assert decoded.succeeded is False
    assert decoded.status_name == "protocol_error"
    assert decoded.error_family_name == "cache"
    assert decoded.is_protocol_error is True
    assert NativeStatus.ok().succeeded is True


def test_native_status_preserves_unknown_status_and_family_names() -> None:
    status = NativeStatus(0x1234, 0x4321, 7, 9)

    assert status.status_name == "unknown"
    assert status.error_family_name == "unknown"
    assert status.protocol_error_code == 7
    assert status.detail_code == 9


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (FFI_STATUS_INVALID_ARGUMENT, NativeInvalidArgumentError),
        (FFI_STATUS_INVALID_HANDLE, NativeInvalidHandleError),
        (FFI_STATUS_INVALID_STATE, NativeInvalidStateError),
        (FFI_STATUS_PROTOCOL_ERROR, NativeProtocolError),
        (FFI_STATUS_WOULD_BLOCK, NativeWouldBlockError),
        (FFI_STATUS_CALLBACK_REJECTED, NativeCallbackRejectedError),
        (FFI_STATUS_INTERNAL_ERROR, NativeInternalError),
    ],
)
def test_raise_for_native_status_maps_stable_status_codes(status_code: int, error_type: type[Exception]) -> None:
    status = NativeStatus(status_code, ERROR_FAMILY_CACHE, 7, 9)

    with pytest.raises(error_type) as captured:
        raise_for_native_status(status)

    assert captured.value.status == status
    assert "status_code=" in str(captured.value)


def test_raise_for_native_status_accepts_ffi_status_and_ignores_ok() -> None:
    raise_for_native_status(NativeStatus.ok())
    raise_for_native_status(_NnrpFfiStatus(FFI_STATUS_OK, 0, 0, 0))

    with pytest.raises(NativeInvalidHandleError):
        raise_for_native_status(_NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 5, 0, 2))


def test_raise_for_native_status_maps_unknown_status_to_internal_error() -> None:
    with pytest.raises(NativeInternalError):
        raise_for_native_status(NativeStatus(0x1234))


def test_native_structured_diagnostic_preserves_status_family_detail_and_related_ids() -> None:
    status = NativeStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_CACHE, 0x22, 0x33)
    runtime_diagnostic = NativeRuntimeDiagnostic(status, 12, 41, 99, 7)

    diagnostic = NativeStructuredDiagnostic.from_runtime_diagnostic(runtime_diagnostic)

    assert diagnostic.status is status
    assert diagnostic.status_name == "protocol_error"
    assert diagnostic.error_family_name == "cache"
    assert diagnostic.protocol_error_name == "unknown"
    assert diagnostic.failed is True
    assert diagnostic.to_report() == {
        "status_code": FFI_STATUS_PROTOCOL_ERROR,
        "status_name": "protocol_error",
        "error_family": ERROR_FAMILY_CACHE,
        "error_family_name": "cache",
        "protocol_error_code": 0x22,
        "protocol_error_name": "unknown",
        "detail_code": 0x33,
        "failed": True,
        "retryable": False,
        "downgrade": False,
        "related_connection_id": 12,
        "related_session_id": 41,
        "related_operation_id": 99,
        "related_frame_id": 7,
    }


def test_native_diagnostic_helpers_classify_stable_protocol_error_families() -> None:
    cache_status = NativeStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_CACHE, CACHE_ERROR_MISS, 0x11)
    schema_status = NativeStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_SCHEMA, SCHEMA_ERROR_HASH_CONFLICT, 0x22)
    downgrade_status = NativeStatus(
        FFI_STATUS_PROTOCOL_ERROR,
        ERROR_FAMILY_SESSION,
        SESSION_ERROR_PRIORITY_REJECTED,
        0x33,
    )

    cache = NativeStructuredDiagnostic.from_status(cache_status)
    schema = NativeStructuredDiagnostic.from_status(schema_status)
    downgrade = NativeStructuredDiagnostic.from_status(downgrade_status)

    assert cache.is_cache_error is True
    assert cache.is_schema_error is False
    assert cache.protocol_error_name == "cache.miss"
    assert cache.is_retryable is True
    assert schema.is_schema_error is True
    assert schema.protocol_error_name == "schema.hash_conflict"
    assert schema.is_retryable is False
    assert downgrade.is_session_error is True
    assert downgrade.protocol_error_name == "session.priority_rejected"
    assert downgrade.is_downgrade is True
    assert downgrade.to_report()["downgrade"] is True
    assert NativeStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_CACHE, CACHE_ERROR_DEPENDENCY_INVALID).is_retryable
    assert NativeStatus(FFI_STATUS_WOULD_BLOCK).is_retryable


def test_native_runtime_entrypoints_bind_frozen_symbol_table() -> None:
    library = FakeEntrypointLibrary()

    entrypoints = NativeRuntimeEntrypoints(library)

    assert entrypoints.current_protocol_version is library.nnrp_current_protocol_version
    assert library.nnrp_current_protocol_version.restype is _NnrpProtocolVersion
    assert library.nnrp_current_protocol_version.argtypes == []
    assert library.nnrp_runtime_capabilities.restype is _NnrpRuntimeCapabilities
    assert library.nnrp_connection_bootstrap.argtypes == [
        _NnrpConnectionBootstrap,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_client_connect.argtypes == [
        _NnrpClientConnectRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_session_open.argtypes == [
        _NnrpSessionOpenRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_client_open_session.argtypes == [
        _NnrpSessionOpenRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_client_resume_session.argtypes == [
        _NnrpSessionResumeRequest,
        ctypes.POINTER(_NnrpHandle),
        ctypes.POINTER(_NnrpSessionRecoveryOutcome),
    ]
    assert library.nnrp_submit.argtypes == [_NnrpSubmitRequest, ctypes.POINTER(_NnrpHandle)]
    assert library.nnrp_client_submit.argtypes == [_NnrpSubmitRequest, ctypes.POINTER(_NnrpHandle)]
    assert library.nnrp_client_submit_result.argtypes == [
        _NnrpClientSubmitResultRequest,
        ctypes.POINTER(_NnrpHandle),
        ctypes.POINTER(_NnrpPollResult),
    ]
    assert library.nnrp_client_submit_result_compact.argtypes == [
        _NnrpClientSubmitResultRequest,
        ctypes.POINTER(_NnrpCompactResult),
    ]
    assert library.nnrp_session_close.argtypes == [_NnrpHandle]
    assert library.nnrp_client_close.argtypes == [_NnrpHandle]
    assert library.nnrp_client_cancel.argtypes == [_NnrpClientCancelRequest]
    assert library.nnrp_client_complete_operation.argtypes == [_NnrpClientCompleteOperationRequest]
    assert library.nnrp_client_drop_operation.argtypes == [_NnrpClientDropOperationRequest]
    assert library.nnrp_client_send_flow_update.argtypes == [_NnrpServerFlowUpdateRequest]
    assert library.nnrp_client_send_result_hint.argtypes == [_NnrpControlRequest]
    assert library.nnrp_client_await_event.argtypes == [
        _NnrpHandle,
        ctypes.POINTER(_NnrpPollResult),
    ]
    assert library.nnrp_client_await_events.argtypes == [
        _NnrpHandle,
        ctypes.POINTER(_NnrpEvent),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    assert library.nnrp_server_bind.argtypes == [_NnrpServerBindRequest, ctypes.POINTER(_NnrpHandle)]
    assert library.nnrp_server_accept.argtypes == [
        _NnrpServerAcceptRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_server_receive_submit.argtypes == [
        _NnrpServerReceiveSubmitRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_server_send_result.argtypes == [_NnrpServerSendResultRequest]
    assert library.nnrp_server_send_flow_update.argtypes == [_NnrpServerFlowUpdateRequest]
    assert library.nnrp_server_close.argtypes == [_NnrpHandle]
    assert library.nnrp_control.argtypes == [_NnrpControlRequest]
    assert library.nnrp_schema_descriptor_parse.argtypes == [
        _NnrpBufferView,
        ctypes.POINTER(_NnrpSchemaDescriptorHeader),
    ]
    assert library.nnrp_schema_descriptor_write.argtypes == [
        _NnrpSchemaDescriptorHeader,
        _NnrpBufferViewMut,
    ]
    assert library.nnrp_token_delta_schema_descriptor.argtypes == [ctypes.POINTER(_NnrpSchemaDescriptorHeader)]
    assert library.nnrp_typed_payload_descriptor_parse.argtypes == [
        _NnrpBufferView,
        ctypes.POINTER(_NnrpTypedPayloadDescriptor),
    ]
    assert library.nnrp_typed_payload_descriptor_write.argtypes == [
        _NnrpTypedPayloadDescriptor,
        _NnrpBufferViewMut,
    ]
    assert library.nnrp_typed_payload_validate_binding.argtypes == [
        ctypes.POINTER(_NnrpSchemaDescriptorHeader),
        ctypes.c_size_t,
        _NnrpTypedPayloadDescriptor,
    ]
    assert library.nnrp_schema_registry_create.argtypes == [ctypes.POINTER(_NnrpHandle)]
    assert library.nnrp_schema_registry_install.argtypes == [
        _NnrpHandle,
        _NnrpSchemaDescriptorHeader,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    assert library.nnrp_schema_registry_lookup.argtypes == [
        _NnrpHandle,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(_NnrpSchemaDescriptorHeader),
    ]
    assert library.nnrp_schema_registry_invalidate.argtypes == [
        _NnrpHandle,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    assert library.nnrp_schema_registry_validate_binding.argtypes == [_NnrpHandle, _NnrpTypedPayloadDescriptor]
    assert library.nnrp_schema_registry_release.argtypes == [_NnrpHandle]
    assert library.nnrp_session_recovery_request_validate.argtypes == [_NnrpBufferView]
    assert library.nnrp_session_recovery_ack_validate.argtypes == [
        _NnrpBufferView,
        _NnrpBufferView,
        ctypes.POINTER(_NnrpSessionRecoveryOutcome),
    ]
    assert library.nnrp_migration_recovery_validate.argtypes == [_NnrpBufferView, _NnrpBufferView]
    assert library.nnrp_migration_should_replay_frame.argtypes == [
        _NnrpBufferView,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
    ]
    assert library.nnrp_buffer_acquire_copy.argtypes == [
        _NnrpBufferView,
        ctypes.POINTER(_NnrpHandle),
        ctypes.POINTER(_NnrpBufferView),
    ]
    assert library.nnrp_buffer_view.argtypes == [_NnrpHandle, ctypes.POINTER(_NnrpBufferView)]
    assert library.nnrp_buffer_release.argtypes == [_NnrpHandle]
    assert library.nnrp_cache_query.argtypes == [_NnrpCacheLeaseRequest, ctypes.POINTER(_NnrpCacheLeaseResult)]
    assert library.nnrp_cache_touch.argtypes == [_NnrpCacheLeaseRequest, ctypes.POINTER(_NnrpCacheLeaseResult)]
    assert library.nnrp_cache_prefetch.argtypes == [
        _NnrpHandle,
        ctypes.POINTER(_NnrpCacheObjectId),
        ctypes.c_size_t,
        ctypes.c_uint64,
        ctypes.c_uint32,
        ctypes.POINTER(_NnrpCacheLeaseResult),
    ]
    assert library.nnrp_cache_release.argtypes == [_NnrpHandle, ctypes.POINTER(_NnrpCacheLeaseResult)]
    assert library.nnrp_poll_empty.argtypes == [ctypes.POINTER(_NnrpPollResult)]
    assert library.nnrp_dispatch_event.argtypes == [_NnrpCallbackSink, ctypes.POINTER(_NnrpEvent)]

    for symbol in RUNTIME_ENTRYPOINT_SYMBOLS[2:]:
        assert getattr(library, symbol).restype is _NnrpFfiStatus


def test_native_runtime_entrypoints_reject_missing_symbol() -> None:
    library = FakeEntrypointLibrary(missing_symbol="nnrp_submit")

    with pytest.raises(NativeArtifactError, match="missing nnrp_submit"):
        NativeRuntimeEntrypoints(library)
