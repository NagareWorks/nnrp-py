from __future__ import annotations

import asyncio
import ctypes
import json
import struct
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import nnrp.native as native_module
from nnrp.cache import CacheLeaseOutcome, CacheLeaseOwnerScope, CacheObjectIdentity
from nnrp.client import (
    SubmitHeaderContext,
    SubmitIdentity,
    SubmitPolicy,
    SubmitRequest,
    TokenChunk,
    TokenSubmitInput,
)
from nnrp.core import HeaderFlags, MessageType, SessionOpenMetadata, SessionPriorityClass, WireFormat
from nnrp.core.messages.control import (
    CacheInvalidateMetadata,
    CacheInvalidateScope,
    FlowUpdateMetadata,
    ResultHintBudgetPolicy,
    ResultHintCongestionState,
    ResultHintMetadata,
    ResultHintReason,
    SessionMigrateAckMetadata,
    TransportId,
    TransportPolicy,
)
from nnrp.core.messages.data import (
    BudgetPolicy,
    FrameSubmitMetadata,
    InputProfile,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    SubmitMode,
    TileIndexMode,
)
from nnrp.native import (
    _NATIVE_RUNTIME_DIAGNOSTIC_OK,
    CACHE_ERROR_DEPENDENCY_INVALID,
    CACHE_ERROR_MISS,
    DEFAULT_ARTIFACT_ROOT_ENV,
    ERROR_FAMILY_CACHE,
    ERROR_FAMILY_SCHEMA,
    ERROR_FAMILY_SESSION,
    EVENT_KIND_CONTROL,
    EVENT_KIND_FLOW_UPDATED,
    EVENT_KIND_RESULT_HINT,
    EVENT_KIND_RESULT_PUSHED,
    EVENT_KIND_RUNTIME_FRAME,
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
    HANDLE_KIND_OBJECT_DESCRIPTOR,
    HANDLE_KIND_OPERATION,
    HANDLE_KIND_SCHEMA_REGISTRY,
    HANDLE_KIND_SERVER_ACCEPT,
    HANDLE_KIND_SESSION,
    HANDLE_KIND_TRANSPORT_CONNECTION,
    HANDLE_KIND_TRANSPORT_LISTENER,
    REQUIRED_RUNTIME_FEATURES,
    RUNTIME_CONTROL_FEATURE_FLAGS,
    RUNTIME_OBJECT_FEATURE_FLAGS,
    SCHEMA_ERROR_HASH_CONFLICT,
    SESSION_ERROR_PRIORITY_REJECTED,
    SESSION_RECOVERY_OUTCOME_RESUME_ENABLED,
    SESSION_RECOVERY_OUTCOME_RESUMED,
    TRANSPORT_SLOT_IPC,
    TRANSPORT_SLOT_TCP,
    TRANSPORT_SLOT_WEBSOCKET,
    NativeArtifactError,
    NativeBorrowedBufferView,
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
    NativeLifecycleEvent,
    NativeMutableBufferView,
    NativeObjectDescriptor,
    NativeObjectDescriptorHandle,
    NativeObjectMetadataBuffer,
    NativeOperationHandle,
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
    NativeRuntimeFeatureFlag,
    NativeRuntimeOperation,
    NativeRuntimePollResult,
    NativeRuntimeResult,
    NativeRuntimeServer,
    NativeRuntimeServerOperation,
    NativeRuntimeServerSession,
    NativeRuntimeSession,
    NativeSchemaCodec,
    NativeSchemaRegistry,
    NativeSchemaRegistryHandle,
    NativeSessionHandle,
    NativeSessionPriorityClass,
    NativeSessionRecoveryOutcome,
    NativeStatus,
    NativeStructuredDiagnostic,
    NativeTransportBinding,
    NativeTransportConnection,
    NativeTransportEndpoint,
    NativeTransportEndpointSupport,
    NativeTransportListener,
    NativeTransportProbeSample,
    NativeTransportProvider,
    NativeWouldBlockError,
    NnrpEndpoint,
    NnrpEndpointSupport,
    _native_event_from_ffi,
    _NnrpBufferView,
    _NnrpBufferViewMut,
    _NnrpCacheLeaseRequest,
    _NnrpCacheLeaseResult,
    _NnrpCacheObjectId,
    _NnrpCallbackSink,
    _NnrpClientCancelRequest,
    _NnrpClientConnectRequest,
    _NnrpEvent,
    _NnrpFfiDiagnostic,
    _NnrpFfiStatus,
    _NnrpHandle,
    _NnrpPollResult,
    _NnrpProtocolVersion,
    _NnrpRoleEventPollRequest,
    _NnrpRuntimeCapabilities,
    _NnrpRuntimeFrameHeader,
    _NnrpRuntimeFrameSendRequest,
    _NnrpRuntimeObjectDescriptor,
    _NnrpSchemaDescriptorHeader,
    _NnrpServerAcceptBeginRequest,
    _NnrpServerAcceptClaimRequest,
    _NnrpServerAcceptResult,
    _NnrpServerAcceptWaitRequest,
    _NnrpServerBindRequest,
    _NnrpServerPolicyDecision,
    _NnrpServerSendResultRequest,
    _NnrpSessionOpenRequest,
    _NnrpSessionRecoveryOutcome,
    _NnrpSessionResumeRequest,
    _NnrpSubmitRequest,
    _NnrpTransportOpenRequest,
    _NnrpTypedPayloadDescriptor,
    _normalize_arch,
    current_native_platform,
    default_artifact_root,
    diagnose_native_transport_endpoint_support,
    diagnose_nnrp_endpoint_support,
    discover_native_transport_providers,
    load_native_library,
    load_native_recovery_codec,
    load_native_runtime,
    load_native_schema_codec,
    native_library_name,
    native_runtime_feature_flag_names,
    native_runtime_feature_flags_available,
    native_transport_slot_names,
    parse_native_transport_endpoint,
    parse_nnrp_endpoint,
    probe_native_artifact,
    raise_for_native_status,
    resolve_native_artifact,
    resolve_native_transport_provider,
    select_native_runtime_backend,
    select_native_transport_provider,
)
from nnrp.native import (
    load_native_client as _load_native_client,
)
from nnrp.runtime import (
    BudgetMetadata,
    CacheMissMetadata,
    CacheMissReason,
    CacheReferenceMetadata,
    CacheReuseScope,
    CapabilityMetadata,
    ControlRequestMetadata,
    MemoryLocationHint,
    NativeRuntimeEvent,
    NativeTerminalEvent,
    NativeTerminalEventKind,
    ObjectDeltaMetadata,
    ObjectDescriptorMetadata,
    ObjectReferenceMetadata,
    ObjectReleaseMetadata,
    ObjectReleaseReason,
    OperationLifecycleEvent,
    OperationState,
    OwnershipHint,
    PartialResultMetadata,
    PressureMetadata,
    ProgressMetadata,
    RecoverableErrorMetadata,
    ResultDropReasonCode,
    ResultDropReasonMetadata,
    ResultTerminalState,
    RetryAfterMetadata,
    RouteHintMetadata,
    RuntimeEventMetadata,
    RuntimeEventMetadataKind,
    RuntimeObjectKind,
    RuntimeRole,
    SchedulingMetadata,
    SupersedeMetadata,
    TraceContextMetadata,
    decode_runtime_control_metadata,
    decode_runtime_object_metadata,
    encode_runtime_control_metadata,
    encode_runtime_object_metadata,
)
from nnrp.schema import (
    SchemaDescriptorHeader,
    SchemaRegistryAction,
    StandardProfile,
    StreamSemantics,
    TypedPayloadDescriptor,
    TypedPayloadDescriptorFlags,
    token_delta_schema_descriptor,
)


def test_native_handle_allocator_uses_the_full_u64_abi_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_module, "_NATIVE_NEXT_HANDLE_ID", 0xFFFFFFFF)
    assert native_module._allocate_native_handle_id() == 0xFFFFFFFF
    assert native_module._allocate_native_handle_id() == 0x1_0000_0000

    monkeypatch.setattr(native_module, "_NATIVE_NEXT_HANDLE_ID", 0xFFFFFFFFFFFFFFFF)
    assert native_module._allocate_native_handle_id() == 0xFFFFFFFFFFFFFFFF
    assert native_module._allocate_native_handle_id() == 1


class FakeLibrary:
    def __init__(
        self,
        *,
        abi_major: int = 4,
        abi_minor: int = 4,
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
        if missing_symbol != "nnrp_transport_runtime_shutdown":
            self.nnrp_transport_runtime_shutdown = FakeFunction(NativeStatus.ok().to_ffi())


class FakeRuntimeLibrary(FakeEntrypointLibrary):
    def __init__(
        self,
        *,
        status: _NnrpFfiStatus | None = None,
        event_payload: bytes = b"",
        event_kind: int = 6,
        event_message_type: int = 0,
        await_event_delay_seconds: float = 0.0,
        active_transport_id: int = int(TransportId.TCP),
        accept_wait_statuses: list[_NnrpFfiStatus] | None = None,
    ) -> None:
        super().__init__()
        self.status = status or NativeStatus.ok().to_ffi()
        self.event_kind = event_kind
        self.event_message_type = event_message_type
        self.await_event_delay_seconds = await_event_delay_seconds
        self.active_transport_id = active_transport_id
        self.accept_wait_statuses = list(accept_wait_statuses or [])
        self._event_payload_owner = (
            ctypes.create_string_buffer(event_payload, len(event_payload)) if event_payload else None
        )
        self.nnrp_runtime_capabilities.value = FakeLibrary().nnrp_runtime_capabilities()
        self.nnrp_client_connect.handler = self._client_connect
        self.nnrp_client_open_session.handler = self._open_session
        self.nnrp_client_resume_session.handler = self._resume_session
        self.nnrp_client_session_recovery_ticket.handler = self._client_session_recovery_ticket
        self.nnrp_client_submit.handler = self._submit
        self.nnrp_client_close.handler = self._close
        self.nnrp_client_close_connection.handler = self._close_connection
        self.nnrp_client_cancel.handler = self._cancel
        self.nnrp_client_await_event.handler = self._await_event
        self.nnrp_client_await_events.handler = self._await_events
        self.nnrp_server_bind.handler = self._server_bind
        self.nnrp_server_accept_begin.handler = self._server_accept_begin
        self.nnrp_server_accept_wait.handler = self._server_accept_wait
        self.nnrp_server_accept_claim.handler = self._server_accept_claim
        self.nnrp_server_accept_release.handler = self._server_accept_release
        self.nnrp_server_await_events.handler = self._await_events
        self.nnrp_server_send_result.handler = self._server_send_result
        self.nnrp_server_close.handler = self._close
        self.nnrp_runtime_frame_send.handler = self._runtime_frame_send
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
        self.nnrp_object_metadata_buffer_acquire_copy.handler = self._object_metadata_buffer_acquire_copy
        self.nnrp_object_metadata_buffer_view.handler = self._object_metadata_buffer_view
        self.nnrp_object_metadata_buffer_release.handler = self._object_metadata_buffer_release
        self.nnrp_object_descriptor_create.handler = self._object_descriptor_create
        self.nnrp_object_descriptor_view.handler = self._object_descriptor_view
        self.nnrp_object_descriptor_metadata_snapshot.handler = self._object_descriptor_metadata_snapshot
        self.nnrp_object_descriptor_release.handler = self._object_descriptor_release
        self.nnrp_cache_query.handler = self._cache_query
        self.nnrp_cache_touch.handler = self._cache_touch
        self.nnrp_cache_prefetch.handler = self._cache_prefetch
        self.nnrp_cache_release.handler = self._cache_release
        self.submitted_payloads: list[bytes] = []
        self.runtime_frames: list[tuple[int, int, bytes]] = []
        self._schema_registry: dict[tuple[int, int], SchemaDescriptorHeader] = {}
        self._buffers: dict[int, ctypes.Array[ctypes.c_char]] = {}
        self._object_descriptors: dict[int, tuple[_NnrpRuntimeObjectDescriptor, ctypes.Array[ctypes.c_char]]] = {}
        self._cache_leases: dict[tuple[int, int, int, int], _NnrpHandle] = {}
        self._cache_lease_owners: dict[int, tuple[int, int]] = {}
        self._session_protocol_ids: dict[int, int] = {}
        self._server_accepts: dict[int, _NnrpHandle] = {}

    def _client_connect(self, request: _NnrpClientConnectRequest, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_CONNECTION, request.connection_id, request.generation, 0))
        return self.status

    def _server_bind(self, request: _NnrpServerBindRequest, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_CONNECTION, request.server_id, request.generation, 0))
        return self.status

    def _server_accept_begin(
        self,
        request: _NnrpServerAcceptBeginRequest,
        out_handle: object,
    ) -> _NnrpFfiStatus:
        if request.server.kind != HANDLE_KIND_CONNECTION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        accept = _NnrpHandle(HANDLE_KIND_SERVER_ACCEPT, request.accept_handle_id, request.generation, 0)
        self._server_accepts[request.accept_handle_id] = request.server
        _write_handle(out_handle, accept)
        return self.status

    def _server_accept_wait(self, request: _NnrpServerAcceptWaitRequest) -> _NnrpFfiStatus:
        if request.accept.id not in self._server_accepts:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        if self.accept_wait_statuses:
            return self.accept_wait_statuses.pop(0)
        return self.status

    def _server_accept_claim(
        self,
        request: _NnrpServerAcceptClaimRequest,
        out_result: object,
    ) -> _NnrpFfiStatus:
        server = self._server_accepts.pop(request.accept.id, None)
        if server is None:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        target = getattr(out_result, "_obj", None)
        if target is None:
            target = ctypes.cast(out_result, ctypes.POINTER(_NnrpServerAcceptResult)).contents
        target.session = _NnrpHandle(HANDLE_KIND_SESSION, request.session_handle_id, request.generation, 0)
        target.active_transport_id = self.active_transport_id
        target.reserved0 = 0
        return self.status

    def _server_accept_release(self, accept: _NnrpHandle) -> _NnrpFfiStatus:
        if self._server_accepts.pop(accept.id, None) is None:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        return self.status

    def _open_session(self, request: _NnrpSessionOpenRequest, out_handle: object) -> _NnrpFfiStatus:
        self._session_protocol_ids[request.session_handle_id] = request.requested_session_id
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_SESSION, request.session_handle_id, request.generation, 0))
        return self.status

    def _resume_session(
        self,
        request: _NnrpSessionResumeRequest,
        out_handle: object,
        out_outcome: object,
    ) -> _NnrpFfiStatus:
        self._session_protocol_ids[request.open.session_handle_id] = request.open.requested_session_id
        _write_handle(
            out_handle,
            _NnrpHandle(
                HANDLE_KIND_SESSION,
                request.open.session_handle_id,
                request.open.generation,
                0,
            ),
        )
        target = getattr(out_outcome, "_obj", None)
        if target is None:
            target = ctypes.cast(out_outcome, ctypes.POINTER(_NnrpSessionRecoveryOutcome)).contents
        target.outcome_code = SESSION_RECOVERY_OUTCOME_RESUMED
        target.resume_window_ms = request.recovery_ticket.len * 10
        return self.status

    def _client_session_recovery_ticket(
        self,
        session: _NnrpHandle,
        out_owner: object,
        out_view: object,
    ) -> _NnrpFfiStatus:
        if session.kind != HANDLE_KIND_SESSION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        token = b"fake-runtime-token"
        payload = (
            struct.pack(
                "<4sHHIIIQ",
                b"NRTK",
                1,
                1,
                self._session_protocol_ids[session.id],
                len(token),
                120_000,
                99,
            )
            + token
        )
        owner_id = len(self._buffers) + 900
        owner = ctypes.create_string_buffer(payload, len(payload))
        self._buffers[owner_id] = owner
        _write_handle(out_owner, _NnrpHandle(HANDLE_KIND_BUFFER, owner_id, 1, 0))
        _write_buffer_view(out_view, owner)
        return self.status

    def _submit(self, request: _NnrpSubmitRequest, out_handle: object) -> _NnrpFfiStatus:
        self.submitted_payloads.append(_read_buffer_view(request.payload))
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_OPERATION, request.operation_id, 1, 0))
        return self.status

    def _close(self, handle: _NnrpHandle) -> _NnrpFfiStatus:
        return (
            self.status
            if handle.kind in {HANDLE_KIND_CONNECTION, HANDLE_KIND_SESSION}
            else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        )

    def _close_connection(self, handle: _NnrpHandle) -> _NnrpFfiStatus:
        return (
            self.status if handle.kind == HANDLE_KIND_CONNECTION else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        )

    def _cancel(self, request: _NnrpClientCancelRequest) -> _NnrpFfiStatus:
        if request.session.kind != HANDLE_KIND_SESSION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        return self.status

    def _server_send_result(self, request: _NnrpServerSendResultRequest) -> _NnrpFfiStatus:
        if request.operation.kind != HANDLE_KIND_OPERATION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        self._event_payload_owner = ctypes.create_string_buffer(_read_buffer_view(request.payload), request.payload.len)
        self.event_kind = EVENT_KIND_RESULT_PUSHED
        self.event_message_type = int(MessageType.RESULT_PUSH)
        return self.status

    def _runtime_frame_send(self, request: _NnrpRuntimeFrameSendRequest) -> _NnrpFfiStatus:
        if request.handle.kind not in {HANDLE_KIND_SESSION, HANDLE_KIND_CONNECTION}:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        self.runtime_frames.append(
            (int(request.message_type), int(request.frame_id), _read_buffer_view(request.payload))
        )
        return self.status

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
        descriptor = TypedPayloadDescriptor.unpack(_read_buffer_view(source))
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

    def _object_metadata_buffer_acquire_copy(
        self,
        source: _NnrpBufferView,
        out_buffer: object,
        out_view: object,
    ) -> _NnrpFfiStatus:
        return self._buffer_acquire_copy(source, out_buffer, out_view)

    def _object_metadata_buffer_view(self, buffer: _NnrpHandle, out_view: object) -> _NnrpFfiStatus:
        return self._buffer_view(buffer, out_view)

    def _object_metadata_buffer_release(self, buffer: _NnrpHandle) -> _NnrpFfiStatus:
        return self._buffer_release(buffer)

    def _object_descriptor_create(
        self,
        descriptor: _NnrpRuntimeObjectDescriptor,
        metadata: _NnrpBufferView,
        out_handle: object,
    ) -> _NnrpFfiStatus:
        payload = _read_buffer_view(metadata)
        descriptor_id = len(self._object_descriptors) + 1000
        owner = ctypes.create_string_buffer(payload, len(payload))
        self._object_descriptors[descriptor_id] = (_copy_runtime_object_descriptor(descriptor), owner)
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_OBJECT_DESCRIPTOR, descriptor_id, 1, 0))
        return self.status

    def _object_descriptor_view(
        self,
        handle: _NnrpHandle,
        out_descriptor: object,
        out_metadata: object,
    ) -> _NnrpFfiStatus:
        item = self._object_descriptors.get(handle.id)
        if handle.kind != HANDLE_KIND_OBJECT_DESCRIPTOR or item is None:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        descriptor, owner = item
        _write_runtime_object_descriptor(out_descriptor, descriptor)
        _write_buffer_view(out_metadata, owner)
        return self.status

    def _object_descriptor_metadata_snapshot(
        self,
        handle: _NnrpHandle,
        out_buffer: object,
        out_view: object,
    ) -> _NnrpFfiStatus:
        item = self._object_descriptors.get(handle.id)
        if handle.kind != HANDLE_KIND_OBJECT_DESCRIPTOR or item is None:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        _, owner = item
        source = _NnrpBufferView(ctypes.cast(owner, ctypes.c_void_p), len(owner.raw))
        return self._buffer_acquire_copy(source, out_buffer, out_view)

    def _object_descriptor_release(self, handle: _NnrpHandle) -> _NnrpFfiStatus:
        if handle.kind != HANDLE_KIND_OBJECT_DESCRIPTOR:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        self._object_descriptors.pop(handle.id, None)
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
                result.object_id = _NnrpCacheObjectId(namespace, object_kind, key_hi, key_lo)
                result.object_version = 1
                result.lease_id = lease_handle.id
                owner_scope, owner_id = self._cache_lease_owners[lease_handle.id]
                result.owner_scope = owner_scope
                result.ttl_ms = 0
                result.owner_id = owner_id
                result.granted_at_ms = 0
                self._cache_leases.pop(key, None)
                self._cache_lease_owners.pop(lease_handle.id, None)
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
        result.owner_scope = {
            HANDLE_KIND_CONNECTION: int(CacheLeaseOwnerScope.CONNECTION),
            HANDLE_KIND_SESSION: int(CacheLeaseOwnerScope.SESSION),
            HANDLE_KIND_OPERATION: int(CacheLeaseOwnerScope.OPERATION),
        }[request.owner.kind]
        result.ttl_ms = request.ttl_ms or 30_000
        result.owner_id = (
            self._session_protocol_ids[request.owner.id]
            if request.owner.kind == HANDLE_KIND_SESSION
            else request.owner.id
        )
        self._cache_lease_owners[lease.id] = (result.owner_scope, result.owner_id)
        result.granted_at_ms = request.now_ms

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
            _write_event_header(target.event, message_type=self.event_message_type, frame_id=7)
            target.event.connection = _NnrpHandle(HANDLE_KIND_CONNECTION, 12, 2, 0)
            target.event.session = handle
            target.event.operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99, 1, 0)
            target.event.payload_owner = _NnrpHandle()
            target.event.payload = _NnrpBufferView(
                ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                len(self._event_payload_owner.raw),
            )
            target.event.diagnostic.status = NativeStatus.ok().to_ffi()
            target.event.diagnostic.related_operation_id = 99
            target.event.diagnostic.related_frame_id = 7
        return self.status

    def _await_events(
        self,
        request: _NnrpRoleEventPollRequest,
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
            _write_event_header(events[index], message_type=self.event_message_type, frame_id=7 + index)
            events[index].connection = _NnrpHandle(HANDLE_KIND_CONNECTION, 12, 2, 0)
            events[index].session = request.scope
            events[index].operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99 + index, 1, 0)
            events[index].payload_owner = _NnrpHandle()
            events[index].payload = _NnrpBufferView(
                ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                len(self._event_payload_owner.raw),
            )
            events[index].diagnostic.status = NativeStatus.ok().to_ffi()
            events[index].diagnostic.related_operation_id = 99 + index
            events[index].diagnostic.related_frame_id = 7 + index
        count_target.value = event_capacity
        return self.status


class BatchControlRuntimeLibrary(FakeRuntimeLibrary):
    def __init__(self, events: list[tuple[int, bytes]]) -> None:
        super().__init__()
        self._batch_events = events
        self._batch_payload_owners = [ctypes.create_string_buffer(payload, len(payload)) for _, payload in events]

    def _await_events(
        self,
        request: _NnrpRoleEventPollRequest,
        out_events: object,
        event_capacity: int,
        out_event_count: object,
    ) -> _NnrpFfiStatus:
        count_target = getattr(out_event_count, "_obj", None)
        if count_target is None:
            count_target = ctypes.cast(out_event_count, ctypes.POINTER(ctypes.c_size_t)).contents
        count_target.value = 0
        if event_capacity <= 0:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_ARGUMENT, 0, 0, 0)

        emitted = min(event_capacity, len(self._batch_events))
        if emitted == 0:
            return _NnrpFfiStatus(FFI_STATUS_WOULD_BLOCK, 0, 0, 0)

        events = ctypes.cast(out_events, ctypes.POINTER(_NnrpEvent))
        for index in range(emitted):
            kind, payload = self._batch_events[index]
            owner = self._batch_payload_owners[index]
            events[index].kind = kind
            _write_event_header(events[index], message_type=0, frame_id=7 + index)
            events[index].connection = _NnrpHandle(HANDLE_KIND_CONNECTION, 12, 2, 0)
            events[index].session = request.scope
            events[index].operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99 + index, 1, 0)
            events[index].payload_owner = _NnrpHandle()
            events[index].payload = _NnrpBufferView(ctypes.cast(owner, ctypes.c_void_p), len(payload))
            events[index].diagnostic.status = NativeStatus.ok().to_ffi()
            events[index].diagnostic.related_operation_id = 99 + index
            events[index].diagnostic.related_frame_id = 7 + index
        count_target.value = emitted
        return self.status


class OwnedBatchRuntimeLibrary(FakeRuntimeLibrary):
    def __init__(self, events: list[tuple[int, int, int, int, bytes]]) -> None:
        super().__init__()
        self._owned_batch_events: list[tuple[int, int, int, int, int, ctypes.Array[ctypes.c_char]]] = []
        for index, (kind, session_id, operation_id, frame_id, payload) in enumerate(events):
            owner_id = 1000 + index
            owner = ctypes.create_string_buffer(payload, len(payload))
            self._buffers[owner_id] = owner
            self._owned_batch_events.append((kind, session_id, operation_id, frame_id, owner_id, owner))

    def _open_session(self, request: _NnrpSessionOpenRequest, out_handle: object) -> _NnrpFfiStatus:
        status = super()._open_session(request, out_handle)
        self._owned_batch_events = [
            (
                kind,
                request.session_handle_id
                if session_id == request.requested_session_id
                else request.session_handle_id + 1_000,
                operation_id,
                frame_id,
                owner_id,
                owner,
            )
            for kind, session_id, operation_id, frame_id, owner_id, owner in self._owned_batch_events
        ]
        return status

    def _await_events(
        self,
        request: _NnrpRoleEventPollRequest,
        out_events: object,
        event_capacity: int,
        out_event_count: object,
    ) -> _NnrpFfiStatus:
        count_target = getattr(out_event_count, "_obj", None)
        if count_target is None:
            count_target = ctypes.cast(out_event_count, ctypes.POINTER(ctypes.c_size_t)).contents
        emitted = min(event_capacity, len(self._owned_batch_events))
        if emitted == 0:
            count_target.value = 0
            return _NnrpFfiStatus(FFI_STATUS_WOULD_BLOCK, 0, 0, 0)

        events = ctypes.cast(out_events, ctypes.POINTER(_NnrpEvent))
        for index in range(emitted):
            kind, session_id, operation_id, frame_id, owner_id, owner = self._owned_batch_events[index]
            events[index].kind = kind
            _write_event_header(events[index], message_type=0, frame_id=frame_id)
            events[index].connection = request.scope
            events[index].session = _NnrpHandle(
                HANDLE_KIND_SESSION,
                session_id,
                request.scope.generation,
                0,
            )
            events[index].operation = _NnrpHandle(HANDLE_KIND_OPERATION, operation_id, 1, 0)
            events[index].payload_owner = _NnrpHandle(HANDLE_KIND_BUFFER, owner_id, 1, 0)
            events[index].payload = _NnrpBufferView(ctypes.cast(owner, ctypes.c_void_p), len(owner.raw))
            events[index].diagnostic.status = NativeStatus.ok().to_ffi()
            events[index].diagnostic.related_operation_id = operation_id
            events[index].diagnostic.related_frame_id = frame_id
        del self._owned_batch_events[:emitted]
        count_target.value = emitted
        return self.status


class ExpiringCacheRuntimeLibrary(FakeRuntimeLibrary):
    def _cache_query(self, request: _NnrpCacheLeaseRequest, out_result: object) -> _NnrpFfiStatus:
        result = _cache_result_target(out_result)
        self._populate_cache_result(result, request, outcome=2)
        result.ttl_ms = 0
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


def _native_token_result_payload(body: bytes = b"result") -> bytes:
    return (
        ResultPushMetadata(
            status_code=200,
            result_flags=ResultFlags.NONE,
            section_count=0,
            tile_count=0,
            active_profile_id=2,
            reserved0=0,
            inference_ms=1,
            queue_ms=0,
            server_total_ms=1,
            reserved1=0,
            tile_base_id=0,
            tile_index_bytes=0,
            result_class=ResultClass.COMPLETE,
            applied_budget_policy=BudgetPolicy.NONE,
            payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
            payload_frame_count=1,
        ).pack()
        + body
    )


def _native_submit_request(
    operation_id: int = 99,
    frame_id: int = 7,
    body: bytes | bytearray | memoryview = b"payload",
    *,
    header: SubmitHeaderContext | None = None,
) -> SubmitRequest:
    return SubmitRequest.token(
        TokenSubmitInput(
            identity=SubmitIdentity(
                operation_id=operation_id,
                frame_id=frame_id,
                header=header or SubmitHeaderContext(),
            ),
            policy=SubmitPolicy(),
            chunks=(TokenChunk(bytes(body)),),
        )
    )


def _open_event_session(connection: NativeRuntimeConnection) -> NativeRuntimeSession:
    return connection.open_session(
        requested_session_id=41,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )


def _write_buffer_view(out_view: object, owner: ctypes.Array[ctypes.c_char]) -> None:
    target = getattr(out_view, "_obj", None)
    if target is None:
        target = ctypes.cast(out_view, ctypes.POINTER(_NnrpBufferView)).contents
    target.ptr = ctypes.cast(owner, ctypes.c_void_p)
    target.len = len(owner.raw)


def _copy_runtime_object_descriptor(descriptor: _NnrpRuntimeObjectDescriptor) -> _NnrpRuntimeObjectDescriptor:
    return _NnrpRuntimeObjectDescriptor(
        descriptor.object_id,
        descriptor.object_kind,
        descriptor.producer_role,
        descriptor.consumer_role,
        descriptor.session_id,
        descriptor.byte_size,
        descriptor.compute_cost_units,
        descriptor.memory_location_hint,
        descriptor.ownership_hint,
        descriptor.lifetime_hint_ms,
        descriptor.metadata_bytes,
    )


def _write_runtime_object_descriptor(out_descriptor: object, descriptor: _NnrpRuntimeObjectDescriptor) -> None:
    target = getattr(out_descriptor, "_obj", None)
    if target is None:
        target = ctypes.cast(out_descriptor, ctypes.POINTER(_NnrpRuntimeObjectDescriptor)).contents
    target.object_id = descriptor.object_id
    target.object_kind = descriptor.object_kind
    target.producer_role = descriptor.producer_role
    target.consumer_role = descriptor.consumer_role
    target.session_id = descriptor.session_id
    target.byte_size = descriptor.byte_size
    target.compute_cost_units = descriptor.compute_cost_units
    target.memory_location_hint = descriptor.memory_location_hint
    target.ownership_hint = descriptor.ownership_hint
    target.lifetime_hint_ms = descriptor.lifetime_hint_ms
    target.metadata_bytes = descriptor.metadata_bytes


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


def _typed_payload_descriptor_from_ffi(descriptor: _NnrpTypedPayloadDescriptor) -> TypedPayloadDescriptor:
    return TypedPayloadDescriptor(
        profile_id=descriptor.profile_id,
        payload_kind=PayloadKind(descriptor.payload_kind),
        descriptor_flags=descriptor.descriptor_flags,
        schema_id=descriptor.schema_id,
        schema_version=descriptor.schema_version,
        stream_semantics=descriptor.stream_semantics,
        offset=descriptor.offset,
        length=descriptor.length,
    )


def _write_typed_payload_descriptor(out_descriptor: object, descriptor: TypedPayloadDescriptor) -> None:
    target = getattr(out_descriptor, "_obj", None)
    if target is None:
        target = ctypes.cast(out_descriptor, ctypes.POINTER(_NnrpTypedPayloadDescriptor)).contents
    target.profile_id = int(descriptor.profile_id)
    target.payload_kind = int(descriptor.payload_kind)
    target.descriptor_flags = int(descriptor.descriptor_flags)
    target.schema_id = descriptor.schema_id
    target.schema_version = descriptor.schema_version
    target.stream_semantics = int(descriptor.stream_semantics)
    target.reserved0 = 0
    target.offset = descriptor.offset
    target.length = descriptor.length


def _write_event_header(event: _NnrpEvent, *, message_type: int, frame_id: int) -> None:
    event.header = _NnrpRuntimeFrameHeader(
        int(message_type != 0),
        1,
        0,
        message_type,
        0,
        41,
        frame_id,
        0,
        0,
        0,
    )


def _decode_test_wire_event(
    message_type: MessageType | int,
    payload: bytes,
    *,
    kind: int = EVENT_KIND_RUNTIME_FRAME,
    frame_id: int = 9,
    wire_format: int = int(WireFormat.CURRENT),
) -> NativeRuntimeEvent:
    library = FakeRuntimeLibrary()
    entrypoints = NativeRuntimeEntrypoints(library)
    owner = ctypes.create_string_buffer(payload, max(1, len(payload)))
    event = _NnrpEvent()
    event.kind = kind
    _write_event_header(event, message_type=int(message_type), frame_id=frame_id)
    event.header.wire_format = wire_format
    event.connection = _NnrpHandle(HANDLE_KIND_CONNECTION, 12, 2, 0)
    event.session = _NnrpHandle(HANDLE_KIND_SESSION, 41, 3, 0)
    event.operation = _NnrpHandle(HANDLE_KIND_OPERATION, 42, 1, 0)
    event.payload = _NnrpBufferView(
        ctypes.cast(owner, ctypes.c_void_p) if payload else None,
        len(payload),
    )
    event.diagnostic.status = NativeStatus.ok().to_ffi()
    decoded = _native_event_from_ffi(event, entrypoints)
    assert isinstance(decoded, NativeRuntimeEvent)
    return decoded


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


RUNTIME_ENTRYPOINT_SYMBOLS = [
    "nnrp_current_protocol_version",
    "nnrp_runtime_capabilities",
    "nnrp_client_connect",
    "nnrp_session_open",
    "nnrp_client_open_session",
    "nnrp_client_resume_session",
    "nnrp_client_session_recovery_ticket",
    "nnrp_submit",
    "nnrp_client_submit",
    "nnrp_session_close",
    "nnrp_client_close",
    "nnrp_client_close_connection",
    "nnrp_client_cancel",
    "nnrp_client_await_event",
    "nnrp_client_await_events",
    "nnrp_server_bind",
    "nnrp_server_accept_begin",
    "nnrp_server_accept_wait",
    "nnrp_server_accept_claim",
    "nnrp_server_accept_release",
    "nnrp_server_await_events",
    "nnrp_server_send_result",
    "nnrp_server_close",
    "nnrp_runtime_frame_send",
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
    "nnrp_object_metadata_buffer_acquire_copy",
    "nnrp_object_metadata_buffer_view",
    "nnrp_object_metadata_buffer_release",
    "nnrp_object_descriptor_create",
    "nnrp_object_descriptor_view",
    "nnrp_object_descriptor_metadata_snapshot",
    "nnrp_object_descriptor_release",
    "nnrp_cache_query",
    "nnrp_cache_touch",
    "nnrp_cache_prefetch",
    "nnrp_cache_release",
    "nnrp_poll_empty",
    "nnrp_dispatch_event",
]


def _test_transport_name(transport_id: int) -> str:
    return {
        TRANSPORT_SLOT_TCP: "tcp",
        TRANSPORT_SLOT_IPC: "ipc",
        TRANSPORT_SLOT_WEBSOCKET: "websocket",
    }.get(transport_id, "tcp")


def _test_transport_endpoint(name: str) -> NativeTransportEndpoint:
    return parse_native_transport_endpoint(
        {
            "tcp": "tcp://127.0.0.1:4433",
            "ipc": "unix:///tmp/nnrp-test.sock",
            "websocket": "ws://127.0.0.1:4433/nnrp",
        }[name]
    )


def _test_server_role_options() -> dict[str, object]:
    return {
        "supported_profiles": (int(StandardProfile.TOKEN),),
        "supported_cache_objects": (),
        "max_cache_objects": 0,
        "max_cache_object_bytes": 0,
        "resume_token_bytes": 24,
        "max_in_flight_operations": 4,
        "granted_operation_credit": 2,
        "lease_ttl_ms": 30_000,
        "resume_window_ms": 120_000,
        "schema_descriptors": (token_delta_schema_descriptor(),),
        "application_policy": None,
    }


class _TestNativeRuntimeClient(NativeRuntimeClient):
    def connect(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_id: int = TRANSPORT_SLOT_TCP,
    ) -> NativeRuntimeConnection:
        name = _test_transport_name(transport_id)
        carrier = NativeTransportConnection(
            SimpleNamespace(),
            SimpleNamespace(name=name),
            _test_transport_endpoint(name),
            NativeHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 800, 1, 0),
        )
        return super().connect(
            connection_id=connection_id,
            generation=generation,
            transport_connection=carrier,
        )

    def bind_server(
        self,
        *,
        server_id: int,
        generation: int,
        transport_id: int = TRANSPORT_SLOT_TCP,
    ) -> NativeRuntimeServer:
        name = _test_transport_name(transport_id)
        listener = NativeTransportListener(
            SimpleNamespace(),
            SimpleNamespace(name=name),
            _test_transport_endpoint(name),
            NativeHandle(HANDLE_KIND_TRANSPORT_LISTENER, 801, 1, 0),
        )
        return super().bind_server(
            server_id=server_id,
            generation=generation,
            transport_listener=listener,
        )


def load_native_client(*args: object, **kwargs: object) -> _TestNativeRuntimeClient:
    client = _load_native_client(*args, **kwargs)
    return _TestNativeRuntimeClient(client.entrypoints)


def test_native_transport_connection_transfers_ownership_only_after_client_role_adoption() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    role_library = FakeRuntimeLibrary()
    carrier = NativeTransportConnection(
        transport_entrypoints,
        SimpleNamespace(name="tcp"),
        _test_transport_endpoint("tcp"),
        NativeHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 800, 1, 0),
    )

    connection = carrier._adopt_client_role(
        NativeRuntimeEntrypoints(role_library),
        connection_id=11,
        generation=2,
    )

    assert connection.handle.handle.id == 11
    assert carrier.connected is False
    assert transport_entrypoints.close.calls == []
    request = role_library.nnrp_client_connect.calls[0][0]
    assert (
        request.transport_connection.kind,
        request.transport_connection.id,
        request.transport_connection.generation,
        request.transport_connection.flags,
    ) == (HANDLE_KIND_TRANSPORT_CONNECTION, 800, 1, 0)


def test_native_transport_binding_adopts_only_its_own_client_carrier() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    role_entrypoints = NativeRuntimeEntrypoints(FakeRuntimeLibrary())
    provider = SimpleNamespace(name="tcp", transport_slots=("tcp",))
    binding = NativeTransportBinding(transport_entrypoints, provider, role_entrypoints)
    carrier = NativeTransportConnection(
        transport_entrypoints,
        provider,
        _test_transport_endpoint("tcp"),
        NativeHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 800, 1, 0),
    )

    connection = binding.adopt_client(carrier, connection_id=11, generation=2)

    assert connection.handle.handle.id == 11
    foreign_carrier = NativeTransportConnection(
        SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi())),
        provider,
        _test_transport_endpoint("tcp"),
        NativeHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 801, 1, 0),
    )
    with pytest.raises(NativeArtifactError, match="owning transport artifact"):
        binding.adopt_client(foreign_carrier, connection_id=12, generation=2)


def test_native_transport_binding_requires_client_role_entrypoints() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    provider = SimpleNamespace(name="tcp", transport_slots=("tcp",))
    binding = NativeTransportBinding(transport_entrypoints, provider)
    carrier = NativeTransportConnection(
        transport_entrypoints,
        provider,
        _test_transport_endpoint("tcp"),
        NativeHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 800, 1, 0),
    )

    with pytest.raises(NativeArtifactError, match="role adoption entrypoints"):
        binding.adopt_client(carrier, connection_id=11, generation=2)


def test_unavailable_native_transport_binding_rejects_every_execution_path() -> None:
    provider = SimpleNamespace(
        name="tcp",
        transport_slots=("tcp",),
        metadata=SimpleNamespace(id="example.transport.tcp.uninstalled"),
    )
    binding = NativeTransportBinding.unavailable(provider, "provider package is not installed")

    assert binding.local_available is False
    assert binding.diagnostic == "provider package is not installed"
    calls = (
        lambda: binding._probe(_test_transport_endpoint("tcp"), None, 1, 1, 0, 0),
        lambda: binding._connect(_test_transport_endpoint("tcp"), None, 0, 0),
        lambda: binding._listen(_test_transport_endpoint("tcp"), None, 0, 0),
    )
    for call in calls:
        with pytest.raises(NativeArtifactError, match="provider package is not installed"):
            call()


def test_native_transport_connection_remains_owned_when_client_role_adoption_fails() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    role_library = FakeRuntimeLibrary(status=_NnrpFfiStatus(FFI_STATUS_INTERNAL_ERROR, 0, 0, 0))
    carrier = NativeTransportConnection(
        transport_entrypoints,
        SimpleNamespace(name="tcp"),
        _test_transport_endpoint("tcp"),
        NativeHandle(HANDLE_KIND_TRANSPORT_CONNECTION, 800, 1, 0),
    )

    with pytest.raises(NativeInternalError):
        carrier._adopt_client_role(
            NativeRuntimeEntrypoints(role_library),
            connection_id=11,
            generation=2,
        )

    assert carrier.connected is True
    carrier._close()
    assert transport_entrypoints.close.calls[0][0].id == 800


def test_native_transport_listener_transfers_ownership_only_after_server_role_adoption() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    role_library = FakeRuntimeLibrary()
    listener = NativeTransportListener(
        transport_entrypoints,
        SimpleNamespace(name="ipc"),
        _test_transport_endpoint("ipc"),
        NativeHandle(HANDLE_KIND_TRANSPORT_LISTENER, 801, 1, 0),
    )

    server = listener._adopt_server_role(
        NativeRuntimeEntrypoints(role_library),
        server_id=21,
        generation=2,
        **_test_server_role_options(),
    )

    assert server.handle.handle.id == 21
    assert listener.listening is False
    assert transport_entrypoints.close.calls == []
    request = role_library.nnrp_server_bind.calls[0][0]
    assert (
        request.transport_listener.kind,
        request.transport_listener.id,
        request.transport_listener.generation,
        request.transport_listener.flags,
    ) == (HANDLE_KIND_TRANSPORT_LISTENER, 801, 1, 0)


def test_native_transport_binding_adopts_only_its_own_server_listener() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    role_entrypoints = NativeRuntimeEntrypoints(FakeRuntimeLibrary())
    provider = SimpleNamespace(name="ipc", transport_slots=("ipc",))
    binding = NativeTransportBinding(transport_entrypoints, provider, role_entrypoints)
    listener = NativeTransportListener(
        transport_entrypoints,
        provider,
        _test_transport_endpoint("ipc"),
        NativeHandle(HANDLE_KIND_TRANSPORT_LISTENER, 801, 1, 0),
    )

    server = binding.adopt_server(
        listener,
        server_id=21,
        generation=2,
        **_test_server_role_options(),
    )

    assert server.handle.handle.id == 21
    foreign_listener = NativeTransportListener(
        SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi())),
        provider,
        _test_transport_endpoint("ipc"),
        NativeHandle(HANDLE_KIND_TRANSPORT_LISTENER, 802, 1, 0),
    )
    with pytest.raises(NativeArtifactError, match="owning transport artifact"):
        binding.adopt_server(
            foreign_listener,
            server_id=22,
            generation=2,
            **_test_server_role_options(),
        )


def test_native_transport_binding_requires_server_role_entrypoints() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    provider = SimpleNamespace(name="ipc", transport_slots=("ipc",))
    binding = NativeTransportBinding(transport_entrypoints, provider)
    listener = NativeTransportListener(
        transport_entrypoints,
        provider,
        _test_transport_endpoint("ipc"),
        NativeHandle(HANDLE_KIND_TRANSPORT_LISTENER, 801, 1, 0),
    )

    with pytest.raises(NativeArtifactError, match="role adoption entrypoints"):
        binding.adopt_server(
            listener,
            server_id=21,
            generation=2,
            **_test_server_role_options(),
        )


def test_native_transport_listener_remains_owned_when_server_role_adoption_fails() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    role_library = FakeRuntimeLibrary(status=_NnrpFfiStatus(FFI_STATUS_INTERNAL_ERROR, 0, 0, 0))
    listener = NativeTransportListener(
        transport_entrypoints,
        SimpleNamespace(name="ipc"),
        _test_transport_endpoint("ipc"),
        NativeHandle(HANDLE_KIND_TRANSPORT_LISTENER, 801, 1, 0),
    )

    with pytest.raises(NativeInternalError):
        listener._adopt_server_role(
            NativeRuntimeEntrypoints(role_library),
            server_id=21,
            generation=2,
            **_test_server_role_options(),
        )

    assert listener.listening is True
    listener._close()
    assert transport_entrypoints.close.calls[0][0].id == 801


def test_native_server_policy_callback_receives_exact_session_open_metadata() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    role_library = FakeRuntimeLibrary()
    listener = NativeTransportListener(
        transport_entrypoints,
        SimpleNamespace(name="ipc"),
        _test_transport_endpoint("ipc"),
        NativeHandle(HANDLE_KIND_TRANSPORT_LISTENER, 801, 1, 0),
    )
    observed: list[SessionOpenMetadata] = []

    def reject(open_metadata: SessionOpenMetadata) -> tuple[bool, int, str]:
        observed.append(open_metadata)
        return False, 17, "policy rejected"

    server = listener._adopt_server_role(
        NativeRuntimeEntrypoints(role_library),
        server_id=21,
        generation=2,
        **(_test_server_role_options() | {"application_policy": reject}),
    )
    request = role_library.nnrp_server_bind.calls[0][0]
    metadata = SessionOpenMetadata(
        41,
        int(StandardProfile.TOKEN),
        SessionPriorityClass.INTERACTIVE,
        1,
        7,
        8,
        500,
        4,
        30_000,
        24,
        12,
        16,
        99,
    )
    encoded = metadata.pack()
    owner = ctypes.create_string_buffer(encoded, len(encoded))
    decision = _NnrpServerPolicyDecision()

    status = request.application_policy.evaluate(
        None,
        _NnrpBufferView(ctypes.cast(owner, ctypes.c_void_p), len(encoded)),
        ctypes.byref(decision),
    )

    assert status == FFI_STATUS_OK
    assert observed == [metadata]
    assert decision.accepted == 0
    assert decision.session_error_code == 17
    assert _read_buffer_view(decision.diagnostic) == b"policy rejected"
    assert server._policy_callback is not None
    server.close()


def test_native_server_policy_callback_rejects_application_exceptions() -> None:
    transport_entrypoints = SimpleNamespace(close=FakeFunction(NativeStatus.ok().to_ffi()))
    role_library = FakeRuntimeLibrary()
    listener = NativeTransportListener(
        transport_entrypoints,
        SimpleNamespace(name="ipc"),
        _test_transport_endpoint("ipc"),
        NativeHandle(HANDLE_KIND_TRANSPORT_LISTENER, 801, 1, 0),
    )

    def fail(_open_metadata: SessionOpenMetadata) -> tuple[bool, int, str | None]:
        raise RuntimeError("policy failure")

    server = listener._adopt_server_role(
        NativeRuntimeEntrypoints(role_library),
        server_id=21,
        generation=2,
        **(_test_server_role_options() | {"application_policy": fail}),
    )
    request = role_library.nnrp_server_bind.calls[0][0]
    encoded = SessionOpenMetadata(1, 2, SessionPriorityClass.BALANCED, 0, 3, 4, 5, 6, 7, 8, 9, 10, 11).pack()
    owner = ctypes.create_string_buffer(encoded, len(encoded))
    decision = _NnrpServerPolicyDecision()

    status = request.application_policy.evaluate(
        None,
        _NnrpBufferView(ctypes.cast(owner, ctypes.c_void_p), len(encoded)),
        ctypes.byref(decision),
    )

    assert status == FFI_STATUS_CALLBACK_REJECTED
    server.close()


def test_native_integer_slices_validate_and_preserve_values() -> None:
    empty_u16, empty_u16_owner = native_module._u16_slice_from_values(())
    populated_u16, populated_u16_owner = native_module._u16_slice_from_values((1, 0xFFFF))
    empty_u32, empty_u32_owner = native_module._u32_slice_from_values(())
    populated_u32, populated_u32_owner = native_module._u32_slice_from_values((1, 0xFFFFFFFF))

    assert empty_u16.len == 0 and empty_u16_owner is None
    assert [populated_u16.ptr[index] for index in range(populated_u16.len)] == [1, 0xFFFF]
    assert populated_u16_owner is not None
    assert empty_u32.len == 0 and empty_u32_owner is None
    assert [populated_u32.ptr[index] for index in range(populated_u32.len)] == [1, 0xFFFFFFFF]
    assert populated_u32_owner is not None
    with pytest.raises(ValueError, match="fit in u16"):
        native_module._u16_slice_from_values((-1,))
    with pytest.raises(ValueError, match="fit in u32"):
        native_module._u32_slice_from_values((-1,))


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
                "provider": _provider_manifest(scope),
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _provider_manifest(scope: str) -> dict[str, object]:
    provider_ids = {
        "tcp": "nnrp.transport.tcp.native",
        "quic": "nnrp.transport.quic.native",
        "ipc": "nnrp.transport.ipc.native",
        "websocket": "nnrp.transport.websocket.native",
    }
    preference_ranks = {"tcp": 2, "quic": 1, "ipc": 0, "websocket": 3}
    limitations = {
        "tcp": ["requires-tcp", "native-host-only"],
        "quic": ["requires-udp", "native-host-only"],
        "ipc": ["local-host-only", "native-host-only", "unix-domain-socket"],
        "websocket": ["requires-tcp", "native-host-only"],
    }
    return {
        "id": provider_ids[scope],
        "cost": {"model_id": 0, "units": "0"},
        "preference_rank": preference_ranks[scope],
        "limits": {"max_frame_bytes": "67108864"},
        "limitations": limitations[scope],
    }


def _provider_metadata(scope: str) -> native_module.NativeTransportProviderMetadata:
    manifest = _provider_manifest(scope)
    return native_module.NativeTransportProviderMetadata(
        id=str(manifest["id"]),
        cost=native_module.NativeTransportProviderCost(model_id=0, units=0),
        preference_rank=int(manifest["preference_rank"]),
        limits=native_module.NativeTransportProviderLimits(max_frame_bytes=67108864),
        limitations=tuple(native_module.NativeTransportProviderLimitation(value) for value in manifest["limitations"]),
    )


def _provider_artifact_manifest(scope: str) -> dict[str, object]:
    return {
        "package": f"nnrp-ffi-transport-{scope}",
        "transport_scope": scope,
        "transport_slots": [scope],
        "enabled_features": [f"transport-{scope}"],
        "provider": _provider_manifest(scope),
    }


def _probe_sample(
    scope: str,
    *,
    elapsed_us: int = 1_000,
    rtt_us: int | None = 1_000,
    bytes_sent: int = 512,
    bytes_received: int = 512,
    timed_out: bool = False,
    failed: bool = False,
) -> NativeTransportProbeSample:
    return NativeTransportProbeSample(
        provider_id=str(_provider_manifest(scope)["id"]),
        transport_name=scope,
        elapsed_us=elapsed_us,
        rtt_us=rtt_us,
        bytes_sent=bytes_sent,
        bytes_received=bytes_received,
        timed_out=timed_out,
        failed=failed,
    )


def _candidate_readiness(root: Path) -> list[native_module.NativeTransportCandidateReadiness]:
    return [
        native_module.NativeTransportCandidateReadiness.ready(provider)
        for provider in discover_native_transport_providers(root, NativePlatform("linux", "x86_64"))
    ]


def _probe_observations(
    root: Path,
    samples: list[NativeTransportProbeSample],
) -> list[native_module.NativeTransportProbeObservation]:
    observations: list[native_module.NativeTransportProbeObservation] = []
    for provider in discover_native_transport_providers(root, NativePlatform("linux", "x86_64")):
        matching = [
            sample
            for sample in samples
            if sample.provider_id == provider.metadata.id and sample.transport_name == provider.name
        ]
        if not matching:
            continue
        metrics = native_module.summarize_native_provider_probe(provider, matching)
        if metrics is None:
            observations.append(native_module.NativeTransportProbeObservation.failed(provider, "probe failed"))
        else:
            observations.append(native_module.NativeTransportProbeObservation.succeeded(provider, metrics))
    return observations


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
            metadata=_provider_metadata("tcp"),
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
            metadata=_provider_metadata("ipc"),
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
            metadata=_provider_metadata("websocket"),
        ),
    )


def test_discover_native_transport_provider_ignores_removed_aggregate_artifact(tmp_path: Path) -> None:
    platform_dir = tmp_path / "linux-x86_64"
    platform_dir.mkdir()
    artifact = platform_dir / "libnnrp_ffi.so"
    artifact.write_bytes(b"all")
    (platform_dir / "manifest.json").write_text(json.dumps({"transport_scope": "all"}), encoding="utf-8")

    providers = discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))

    assert providers == ()


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
        (_provider_artifact_manifest("ipc") | {"transport_scope": 7}, "transport_scope must be a string"),
        (_provider_artifact_manifest("ipc") | {"transport_scope": "stdio"}, "unsupported native transport scope"),
        (_provider_artifact_manifest("ipc") | {"transport_slots": []}, "transport_slots must be a non-empty list"),
        (_provider_artifact_manifest("ipc") | {"transport_slots": [7]}, "transport_slots entries must be strings"),
        (_provider_artifact_manifest("ipc") | {"transport_slots": ["stdio"]}, "unsupported native transport slot"),
        (
            _provider_artifact_manifest("ipc") | {"enabled_features": "transport-ipc"},
            "enabled_features must be a list",
        ),
        (
            _provider_artifact_manifest("ipc") | {"enabled_features": [""]},
            "enabled_features entries must be non-empty strings",
        ),
        (_provider_artifact_manifest("ipc") | {"package": ""}, "package must be a non-empty string"),
        (_provider_artifact_manifest("ipc") | {"provider": []}, "provider must be an object"),
        (
            _provider_artifact_manifest("ipc") | {"provider": _provider_manifest("ipc") | {"id": ""}},
            "id must be a non-empty string",
        ),
        (
            _provider_artifact_manifest("ipc")
            | {"provider": _provider_manifest("ipc") | {"id": "provider-\u8f93\u5165"}},
            "provider.id must be ASCII",
        ),
        (
            _provider_artifact_manifest("ipc") | {"provider": _provider_manifest("ipc") | {"cost": []}},
            "provider.cost must be an object",
        ),
        (
            _provider_artifact_manifest("ipc")
            | {"provider": _provider_manifest("ipc") | {"cost": {"model_id": True, "units": "0"}}},
            "model_id must be an integer",
        ),
        (
            _provider_artifact_manifest("ipc")
            | {"provider": _provider_manifest("ipc") | {"cost": {"model_id": 0, "units": "01"}}},
            "units must be a canonical decimal u64 string",
        ),
        (
            _provider_artifact_manifest("ipc")
            | {"provider": _provider_manifest("ipc") | {"cost": {"model_id": 0, "units": "1"}}},
            "units must be zero when model_id is zero",
        ),
        (
            _provider_artifact_manifest("ipc") | {"provider": _provider_manifest("ipc") | {"preference_rank": 65536}},
            "preference_rank must be an integer",
        ),
        (
            _provider_artifact_manifest("ipc") | {"provider": _provider_manifest("ipc") | {"limits": []}},
            "provider.limits must be an object",
        ),
        (
            _provider_artifact_manifest("ipc")
            | {"provider": _provider_manifest("ipc") | {"limits": {"max_frame_bytes": "-1"}}},
            "max_frame_bytes must be a canonical decimal u64 string",
        ),
        (
            _provider_artifact_manifest("ipc")
            | {"provider": _provider_manifest("ipc") | {"limits": {"max_frame_bytes": "0"}}},
            "max_frame_bytes must be greater than zero",
        ),
        (
            _provider_artifact_manifest("ipc")
            | {"provider": _provider_manifest("ipc") | {"limitations": "local-host-only"}},
            "provider.limitations must be a list",
        ),
        (
            _provider_artifact_manifest("ipc") | {"provider": _provider_manifest("ipc") | {"limitations": ["unknown"]}},
            "unsupported native transport provider limitation",
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


def test_parse_nnrp_endpoint_accepts_application_facing_uri() -> None:
    endpoint = parse_nnrp_endpoint("nnrps://runtime.example/session/default?tenant=alpha")

    assert endpoint == NnrpEndpoint(
        uri="nnrps://runtime.example/session/default?tenant=alpha",
        scheme="nnrps",
        authority="runtime.example",
        path="/session/default",
        query="tenant=alpha",
        secure=True,
    )


@pytest.mark.parametrize(
    ("uri", "match"),
    [
        ("", "must be non-empty"),
        ("wss://runtime.example/nnrp", "unsupported NNRP endpoint scheme"),
        ("nnrp:///session", "must include an authority"),
        ("nnrp://runtime.example/session#fragment", "must not include a fragment"),
    ],
)
def test_parse_nnrp_endpoint_rejects_non_application_uri_shapes(uri: str, match: str) -> None:
    with pytest.raises(NativeArtifactError, match=match):
        parse_nnrp_endpoint(uri)


def test_parse_native_transport_endpoint_rejects_application_endpoint_scheme() -> None:
    with pytest.raises(NativeArtifactError, match="parse_nnrp_endpoint"):
        parse_native_transport_endpoint("nnrps://runtime.example/session")


def test_diagnose_nnrp_endpoint_support_selects_installed_provider(tmp_path: Path) -> None:
    tcp_artifact = _write_provider_artifact(tmp_path, "tcp")

    support = diagnose_nnrp_endpoint_support(
        "nnrps://runtime.example/session/default",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )

    assert support.endpoint == NnrpEndpoint(
        uri="nnrps://runtime.example/session/default",
        scheme="nnrps",
        authority="runtime.example",
        path="/session/default",
        query="",
        secure=True,
    )
    assert support.available is True
    assert support.selection is not None
    assert support.selection.selected_provider.artifact_path == tcp_artifact
    assert support.selection.selected_transport_id is TransportId.TCP
    assert support.diagnostic == "NNRP endpoint nnrps://runtime.example/session/default selected tcp carrier"


def test_diagnose_nnrp_endpoint_support_reports_missing_provider(tmp_path: Path) -> None:
    support = diagnose_nnrp_endpoint_support(
        "nnrp://runtime.example/session/default",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )

    assert support == NnrpEndpointSupport(
        endpoint=NnrpEndpoint(
            uri="nnrp://runtime.example/session/default",
            scheme="nnrp",
            authority="runtime.example",
            path="/session/default",
            query="",
            secure=False,
        ),
        selection=None,
        available=False,
        skip_reason="no viable native transport provider after applying policy and remote support",
        diagnostic=(
            "skip nnrp://runtime.example/session/default: "
            "no viable native transport provider after applying policy and remote support"
        ),
    )


def test_diagnose_native_transport_endpoint_support_reports_available_provider(tmp_path: Path) -> None:
    websocket_artifact = _write_provider_artifact(tmp_path, "websocket")

    support = diagnose_native_transport_endpoint_support(
        "wss://example.test/nnrp",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )

    assert support == NativeTransportEndpointSupport(
        endpoint=NativeTransportEndpoint(
            uri="wss://example.test/nnrp",
            scheme="wss",
            transport_name="websocket",
            transport_id=TransportId.WEBSOCKET,
            address="example.test/nnrp",
            secure=True,
        ),
        provider=NativeTransportProvider(
            name="websocket",
            artifact_path=websocket_artifact,
            manifest_path=websocket_artifact.with_name("manifest.json"),
            transport_slots=("websocket",),
            enabled_features=("transport-websocket",),
            package="nnrp-ffi-transport-websocket",
            transport_scope="websocket",
            platform_tag="linux-x86_64",
            metadata=_provider_metadata("websocket"),
        ),
        available=True,
        diagnostic="native transport provider 'websocket' exposes websocket",
    )


@pytest.mark.parametrize(
    ("uri", "transport_name"),
    [
        ("unix:///tmp/nnrp.sock", "ipc"),
        ("ws://example.test/nnrp", "websocket"),
    ],
)
def test_diagnose_native_transport_endpoint_support_skips_missing_provider(
    tmp_path: Path,
    uri: str,
    transport_name: str,
) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    support = diagnose_native_transport_endpoint_support(
        uri,
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )

    assert support.available is False
    assert support.provider is None
    assert support.endpoint.transport_name == transport_name
    assert support.skip_reason == f"native artifact does not expose {transport_name} transport"
    assert f"install a preview4 {transport_name} native transport artifact" in str(support.diagnostic)


def test_select_native_transport_provider_selects_single_installed_transport(tmp_path: Path) -> None:
    tcp_artifact = _write_provider_artifact(tmp_path, "tcp")

    selection = select_native_transport_provider(
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=_candidate_readiness(tmp_path),
    )

    assert selection.selected_provider.artifact_path == tcp_artifact
    assert selection.selected_transport_name == "tcp"
    assert selection.selected_transport_id is TransportId.TCP
    assert selection.policy is TransportPolicy.AUTO
    assert selection.candidates[0].selection_rank == 0
    assert selection.candidates[0].probe_state is native_module.NativeTransportProbeState.NOT_RUN
    assert selection.diagnostic == "single eligible native transport selected directly"


def test_select_native_transport_provider_applies_preview4_policy_order(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "quic")
    _write_provider_artifact(tmp_path, "ipc")
    _write_provider_artifact(tmp_path, "websocket")

    samples = [_probe_sample(scope) for scope in ("tcp", "quic", "ipc", "websocket")]
    auto = select_native_transport_provider(
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=_candidate_readiness(tmp_path),
        probe_observations=_probe_observations(tmp_path, samples),
    )
    preferred_websocket = select_native_transport_provider(
        TransportPolicy.PREFER_WEBSOCKET,
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=_candidate_readiness(tmp_path),
        probe_observations=_probe_observations(tmp_path, samples),
    )
    preferred_tcp = select_native_transport_provider(
        "prefer-tcp",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=_candidate_readiness(tmp_path),
        probe_observations=_probe_observations(tmp_path, samples),
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
            candidate_readiness=_candidate_readiness(tmp_path),
        )


def test_select_native_transport_provider_reports_forced_transport_rejection(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    with pytest.raises(
        NativeArtifactError,
        match="forced native transport tcp rejected: peer-unsupported",
    ) as caught:
        select_native_transport_provider(
            TransportPolicy.FORCE_TCP,
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            supported_transports=(TransportId.QUIC,),
            candidate_readiness=_candidate_readiness(tmp_path),
        )

    assert caught.value.candidates[0].rejection_reason is (
        native_module.NativeTransportRejectionReason.PEER_UNSUPPORTED
    )


def test_select_native_transport_provider_rejects_empty_provider_registry(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError, match="no viable native transport provider"):
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            candidate_readiness=[],
        )


def test_select_native_transport_provider_accepts_integer_and_auto_string_policy(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "websocket")

    auto = select_native_transport_provider(
        "auto",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=_candidate_readiness(tmp_path),
    )
    forced = select_native_transport_provider(
        int(TransportPolicy.FORCE_WEBSOCKET),
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=_candidate_readiness(tmp_path),
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
            candidate_readiness=_candidate_readiness(tmp_path),
        )


def test_select_native_transport_provider_rejects_unspecified_supported_transport(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    with pytest.raises(NativeArtifactError, match="unsupported native transport id"):
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            supported_transports=(TransportId.UNSPECIFIED,),
            candidate_readiness=_candidate_readiness(tmp_path),
        )


def test_select_native_transport_provider_reports_remote_unsupported_transport(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "ipc")
    _write_provider_artifact(tmp_path, "quic")

    selection = select_native_transport_provider(
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        supported_transports=(TransportId.TCP, TransportId.QUIC),
        candidate_readiness=_candidate_readiness(tmp_path),
        probe_observations=_probe_observations(
            tmp_path,
            [_probe_sample("tcp"), _probe_sample("quic")],
        ),
    )

    assert selection.selected_transport_id is TransportId.QUIC
    ipc = next(candidate for candidate in selection.candidates if candidate.transport_id is TransportId.IPC)
    assert ipc.rejection_reason is native_module.NativeTransportRejectionReason.PEER_UNSUPPORTED
    assert ipc.peer_supported is False


def test_select_native_transport_provider_uses_deterministic_probe_metrics(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "quic")
    _write_provider_artifact(tmp_path, "ipc")

    samples = [
        _probe_sample(
            "tcp",
            elapsed_us=1_500,
            rtt_us=1_500,
            bytes_sent=128,
            bytes_received=128,
        ),
        _probe_sample(
            "quic",
            elapsed_us=800,
            rtt_us=800,
            bytes_sent=512,
            bytes_received=512,
        ),
        _probe_sample(
            "ipc",
            elapsed_us=2_000,
            rtt_us=None,
            bytes_sent=0,
            bytes_received=0,
            timed_out=True,
            failed=True,
        ),
    ]
    selection = select_native_transport_provider(
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=_candidate_readiness(tmp_path),
        probe_observations=_probe_observations(tmp_path, samples),
    )

    assert selection.selected_transport_id is TransportId.QUIC
    assert selection.candidates[0].probe is not None
    assert selection.candidates[0].probe.median_rtt_us == 800
    assert [candidate.transport_name for candidate in selection.candidates[:2]] == ["quic", "tcp"]
    assert selection.candidates[-1].rejection_reason is native_module.NativeTransportRejectionReason.PROBE_FAILED
    assert selection.diagnostic == "native transport selected by deterministic probe ordering"


def test_select_native_transport_provider_requires_probe_observations_for_probe_mode(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "quic")

    with pytest.raises(NativeArtifactError, match="no viable native transport provider") as caught:
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            candidate_readiness=_candidate_readiness(tmp_path),
            probe_observations=[],
        )
    assert all(
        candidate.rejection_reason is native_module.NativeTransportRejectionReason.PROBE_MISSING
        for candidate in caught.value.candidates
    )


def test_select_native_transport_provider_distinguishes_failed_and_missing_probe_observations(
    tmp_path: Path,
) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "quic")
    providers = discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))
    tcp = next(provider for provider in providers if provider.name == "tcp")
    quic = next(provider for provider in providers if provider.name == "quic")
    quic_metrics = native_module.summarize_native_provider_probe(quic, [_probe_sample("quic")])
    assert quic_metrics is not None

    selection = select_native_transport_provider(
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=[native_module.NativeTransportCandidateReadiness.ready(provider) for provider in providers],
        probe_observations=[
            native_module.NativeTransportProbeObservation.failed(tcp, "connection refused"),
            native_module.NativeTransportProbeObservation.succeeded(quic, quic_metrics),
        ],
    )

    tcp_candidate = next(candidate for candidate in selection.candidates if candidate.transport_name == "tcp")
    assert tcp_candidate.probe_state is native_module.NativeTransportProbeState.FAILED
    assert tcp_candidate.rejection_reason is native_module.NativeTransportRejectionReason.PROBE_FAILED
    assert tcp_candidate.diagnostic == "connection refused"


@pytest.mark.parametrize(
    "evidence_case",
    ["missing", "duplicate", "unmatched-readiness", "unmatched-observation", "duplicate-observation"],
)
def test_select_native_transport_provider_rejects_invalid_evidence(
    tmp_path: Path,
    evidence_case: str,
) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    provider = discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))[0]
    readiness = [native_module.NativeTransportCandidateReadiness.ready(provider)]
    observations: list[native_module.NativeTransportProbeObservation] = []
    if evidence_case == "missing":
        readiness = []
    elif evidence_case == "duplicate":
        readiness *= 2
    elif evidence_case == "unmatched-readiness":
        readiness = [
            native_module.NativeTransportCandidateReadiness(
                transport_id=TransportId.TCP,
                provider_id="unknown.provider",
                route_resolved=True,
                security_satisfied=True,
            )
        ]
    else:
        observations = [
            native_module.NativeTransportProbeObservation(
                transport_id=TransportId.TCP,
                provider_id=(provider.metadata.id if evidence_case == "duplicate-observation" else "unknown.provider"),
                state=native_module.NativeTransportProbeState.FAILED,
            )
        ]
        if evidence_case == "duplicate-observation":
            observations *= 2

    with pytest.raises(native_module.NativeTransportSelectionError) as caught:
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            candidate_readiness=readiness,
            probe_observations=observations,
        )

    assert caught.value.code is native_module.NativeTransportSelectionErrorCode.INVALID_EVIDENCE
    assert caught.value.policy is None
    assert caught.value.candidates == ()


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: native_module.NativeTransportCandidateReadiness(TransportId.UNSPECIFIED, "provider", True, True),
        lambda: native_module.NativeTransportCandidateReadiness(TransportId.TCP, "", True, True),
        lambda: native_module.NativeTransportCandidateReadiness(TransportId.TCP, "provider", 1, True),
        lambda: native_module.NativeTransportCandidateReadiness(TransportId.TCP, "provider", True, 1),
        lambda: native_module.NativeTransportCandidateReadiness(TransportId.TCP, "provider", True, True, diagnostic=1),
        lambda: native_module.NativeTransportProbeObservation(
            TransportId.UNSPECIFIED, "provider", native_module.NativeTransportProbeState.FAILED
        ),
        lambda: native_module.NativeTransportProbeObservation(
            TransportId.TCP, "", native_module.NativeTransportProbeState.FAILED
        ),
        lambda: native_module.NativeTransportProbeObservation(
            TransportId.TCP, "provider", native_module.NativeTransportProbeState.NOT_RUN
        ),
        lambda: native_module.NativeTransportProbeObservation(
            TransportId.TCP, "provider", native_module.NativeTransportProbeState.SUCCEEDED
        ),
        lambda: native_module.NativeTransportProbeObservation(
            TransportId.TCP,
            "provider",
            native_module.NativeTransportProbeState.SUCCEEDED,
            metrics=object(),
        ),
        lambda: native_module.NativeTransportProbeObservation(
            TransportId.TCP,
            "provider",
            native_module.NativeTransportProbeState.FAILED,
            metrics=native_module.NativeTransportProbeMetrics(1, 1, 1, 1),
        ),
        lambda: native_module.NativeTransportProbeObservation(
            TransportId.TCP,
            "provider",
            native_module.NativeTransportProbeState.FAILED,
            diagnostic=1,
        ),
    ],
)
def test_transport_selection_evidence_models_reject_invalid_fields(constructor) -> None:
    with pytest.raises(ValueError):
        constructor()


def test_transport_candidate_readiness_factories_preserve_host_failure(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    provider = discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))[0]

    unresolved = native_module.NativeTransportCandidateReadiness.route_unresolved(provider, "no route")
    insecure = native_module.NativeTransportCandidateReadiness.security_unsatisfied(provider, "TLS required")

    assert (unresolved.route_resolved, unresolved.diagnostic) == (False, "no route")
    assert (insecure.security_satisfied, insecure.diagnostic) == (False, "TLS required")


@pytest.mark.parametrize(
    ("readiness_factory", "expected_reason"),
    [
        (
            lambda provider: native_module.NativeTransportCandidateReadiness.route_unresolved(provider, "no route"),
            native_module.NativeTransportRejectionReason.ROUTE_UNRESOLVED,
        ),
        (
            lambda provider: native_module.NativeTransportCandidateReadiness.security_unsatisfied(
                provider, "TLS required"
            ),
            native_module.NativeTransportRejectionReason.SECURITY_UNSATISFIED,
        ),
    ],
)
def test_select_native_transport_provider_applies_candidate_readiness(
    tmp_path: Path,
    readiness_factory,
    expected_reason: native_module.NativeTransportRejectionReason,
) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    provider = discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))[0]

    with pytest.raises(native_module.NativeTransportSelectionError) as caught:
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            candidate_readiness=[readiness_factory(provider)],
        )

    assert caught.value.candidates[0].rejection_reason is expected_reason


def test_diagnose_nnrp_endpoint_support_summarizes_raw_probe_samples(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    _write_provider_artifact(tmp_path, "quic")

    support = diagnose_nnrp_endpoint_support(
        "nnrp://localhost",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        probe_samples=[_probe_sample("tcp"), _probe_sample("quic", failed=True, rtt_us=None)],
    )

    assert support.available is True
    assert support.selection is not None
    assert support.selection.selected_transport_name == "tcp"


def test_discover_native_transport_providers_rejects_duplicate_provider_ids(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    quic_manifest = _provider_artifact_manifest("quic")
    quic_provider = dict(quic_manifest["provider"])
    quic_provider["id"] = _provider_manifest("tcp")["id"]
    quic_manifest["provider"] = quic_provider
    _write_provider_artifact_with_manifest(tmp_path, "quic", quic_manifest)

    with pytest.raises(NativeArtifactError, match="duplicate native provider id"):
        discover_native_transport_providers(tmp_path, NativePlatform("linux", "x86_64"))


def test_select_native_transport_provider_enforces_requested_frame_limit(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    with pytest.raises(NativeArtifactError, match="no viable native transport provider") as caught:
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            requested_max_frame_bytes=67108865,
            candidate_readiness=_candidate_readiness(tmp_path),
        )

    assert len(caught.value.candidates) == 1
    candidate = caught.value.candidates[0]
    assert candidate.within_limits is False
    assert candidate.rejection_reason is native_module.NativeTransportRejectionReason.LIMIT_EXCEEDED


def test_select_native_transport_provider_rejects_invalid_requested_frame_limit(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")

    with pytest.raises(ValueError, match="requested_max_frame_bytes"):
        select_native_transport_provider(
            root=tmp_path,
            native_platform=NativePlatform("linux", "x86_64"),
            requested_max_frame_bytes=True,
            candidate_readiness=_candidate_readiness(tmp_path),
        )


def test_summarize_native_provider_probe_uses_per_sample_even_medians(tmp_path: Path) -> None:
    _write_provider_artifact(tmp_path, "tcp")
    provider = resolve_native_transport_provider(
        "tcp",
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
    )

    metrics = native_module.summarize_native_provider_probe(
        provider,
        [
            _probe_sample("tcp", elapsed_us=1_000, rtt_us=10, bytes_sent=100, bytes_received=0),
            _probe_sample("tcp", elapsed_us=1_000, rtt_us=20, bytes_sent=300, bytes_received=0),
            _probe_sample("tcp", elapsed_us=1_000, rtt_us=None, failed=True),
        ],
    )

    assert metrics == native_module.NativeTransportProbeMetrics(
        sample_count=3,
        success_count=2,
        median_throughput_bytes_per_sec=200_000,
        median_rtt_us=15,
    )


def test_select_native_transport_provider_compares_shared_cost_model_before_preference(tmp_path: Path) -> None:
    tcp_manifest = _provider_artifact_manifest("tcp")
    tcp_manifest["provider"] = _provider_manifest("tcp") | {"cost": {"model_id": 7, "units": "1"}}
    ipc_manifest = _provider_artifact_manifest("ipc")
    ipc_manifest["provider"] = _provider_manifest("ipc") | {"cost": {"model_id": 7, "units": "2"}}
    _write_provider_artifact_with_manifest(tmp_path, "tcp", tcp_manifest)
    _write_provider_artifact_with_manifest(tmp_path, "ipc", ipc_manifest)

    samples = [_probe_sample("tcp"), _probe_sample("ipc")]
    selection = select_native_transport_provider(
        root=tmp_path,
        native_platform=NativePlatform("linux", "x86_64"),
        candidate_readiness=_candidate_readiness(tmp_path),
        probe_observations=_probe_observations(tmp_path, samples),
    )

    assert selection.selected_transport_id is TransportId.TCP
    assert [candidate.selection_rank for candidate in selection.candidates] == [0, 1]


@pytest.mark.parametrize(
    ("constructor", "match"),
    [
        (lambda: native_module.NativeTransportProviderCost(model_id=True, units=0), "model_id"),
        (lambda: native_module.NativeTransportProviderCost(model_id=0, units=1), "units"),
        (lambda: native_module.NativeTransportProviderLimits(max_frame_bytes=-1), "max_frame_bytes"),
        (lambda: native_module.NativeTransportProviderLimits(max_frame_bytes=0), "max_frame_bytes"),
        (
            lambda: native_module.NativeTransportProviderMetadata(
                id="provider-\u8f93\u5165",
                cost=native_module.NativeTransportProviderCost(model_id=0, units=0),
                preference_rank=0,
                limits=native_module.NativeTransportProviderLimits(max_frame_bytes=1),
                limitations=(),
            ),
            "id",
        ),
        (
            lambda: native_module.NativeTransportProviderMetadata(
                id="provider",
                cost={},  # type: ignore[arg-type]
                preference_rank=0,
                limits=native_module.NativeTransportProviderLimits(max_frame_bytes=1),
                limitations=(),
            ),
            "cost",
        ),
        (
            lambda: native_module.NativeTransportProbeMetrics(
                sample_count=1,
                success_count=2,
                median_throughput_bytes_per_sec=1,
                median_rtt_us=1,
            ),
            "success_count",
        ),
        (
            lambda: NativeTransportProbeSample(
                provider_id="",
                transport_name="tcp",
                elapsed_us=1,
            ),
            "provider_id",
        ),
        (
            lambda: NativeTransportProbeSample(
                provider_id="provider-\u8f93\u5165",
                transport_name="tcp",
                elapsed_us=1,
            ),
            "provider_id",
        ),
        (
            lambda: native_module.NativeTransportCandidateReadiness(
                transport_id=TransportId.TCP,
                provider_id="provider-\u8f93\u5165",
                route_resolved=True,
                security_satisfied=True,
            ),
            "provider_id",
        ),
        (
            lambda: native_module.NativeTransportProbeObservation(
                transport_id=TransportId.TCP,
                provider_id="provider-\u8f93\u5165",
                state=native_module.NativeTransportProbeState.FAILED,
            ),
            "provider_id",
        ),
        (
            lambda: NativeTransportProbeSample(
                provider_id="provider",
                transport_name="auto",
                elapsed_us=1,
            ),
            "transport_name",
        ),
    ],
)
def test_native_transport_public_models_reject_invalid_values(
    constructor: Callable[[], object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        constructor()


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
    assert result.abi_major == 4
    assert result.abi_minor == 4
    assert result.abi_patch == 0
    assert result.protocol_major == 1
    assert result.protocol_wire_format == 0
    assert result.sdk_channel == 3
    assert result.sdk_revision == 6
    assert result.transport_slots == TRANSPORT_SLOT_TCP
    assert result.feature_flags == REQUIRED_RUNTIME_FEATURES
    assert result.has_runtime_control_features is True
    assert result.has_runtime_object_features is True
    assert "client_api" in result.runtime_control_feature_names
    assert "cache_schema" in result.runtime_object_feature_names
    assert "transport_slots" in result.feature_flag_names


def test_native_runtime_feature_flag_helpers_filter_known_feature_groups() -> None:
    future_unknown_flag = 0x8000000000000000
    feature_flags = (
        NativeRuntimeFeatureFlag.CLIENT_API
        | NativeRuntimeFeatureFlag.CACHE_SCHEMA
        | NativeRuntimeFeatureFlag.TRANSPORT_SLOTS
        | future_unknown_flag
    )

    assert native_runtime_feature_flag_names(feature_flags) == (
        "client_api",
        "cache_schema",
        "transport_slots",
    )
    assert native_runtime_feature_flag_names(feature_flags, mask=RUNTIME_CONTROL_FEATURE_FLAGS) == ("client_api",)
    assert native_runtime_feature_flag_names(feature_flags, mask=RUNTIME_OBJECT_FEATURE_FLAGS) == ("cache_schema",)
    assert native_runtime_feature_flags_available(feature_flags, NativeRuntimeFeatureFlag.CLIENT_API) is True
    assert native_runtime_feature_flags_available(feature_flags, RUNTIME_CONTROL_FEATURE_FLAGS) is False


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

    with pytest.raises(NativeArtifactError, match="ABI mismatch"):
        probe_native_artifact(artifact, library=FakeLibrary(abi_patch=1))


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
    assert library.nnrp_transport_runtime_shutdown.restype is _NnrpFfiStatus
    assert library.nnrp_transport_runtime_shutdown.argtypes == []


def test_load_native_runtime_rejects_missing_shutdown_boundary(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeEntrypointLibrary(missing_symbol="nnrp_transport_runtime_shutdown")
    library.nnrp_runtime_capabilities.value = FakeLibrary().nnrp_runtime_capabilities()

    with pytest.raises(NativeArtifactError, match="missing nnrp_transport_runtime_shutdown"):
        load_native_runtime(artifact, library=library)


def test_native_runtime_shutdown_is_registered_once_per_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    first = FakeRuntimeLibrary()
    second = FakeRuntimeLibrary()

    load_native_runtime(artifact, library=first)
    load_native_runtime(artifact, library=second)
    native_module._shutdown_registered_native_runtimes()

    assert len(first.nnrp_transport_runtime_shutdown.calls) == 1
    assert second.nnrp_transport_runtime_shutdown.calls == []

    load_native_runtime(artifact, library=second)
    native_module._shutdown_registered_native_runtimes()

    assert len(first.nnrp_transport_runtime_shutdown.calls) == 1
    assert len(second.nnrp_transport_runtime_shutdown.calls) == 1


def test_native_runtime_shutdown_does_not_interrupt_interpreter_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_during_shutdown() -> None:
        raise RuntimeError("shutdown failed")

    monkeypatch.setattr(
        native_module,
        "_NATIVE_RUNTIME_SHUTDOWNS",
        {"failing-runtime": (object(), raise_during_shutdown)},
    )

    native_module._shutdown_registered_native_runtimes()


def test_load_native_transport_binding_registers_runtime_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    provider = SimpleNamespace(name="tcp", artifact_path=artifact)
    library = object()
    registrations: list[tuple[object, Path]] = []

    monkeypatch.setattr(native_module, "resolve_native_transport_provider", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(native_module, "_call_runtime_capabilities", lambda _library: object())
    monkeypatch.setattr(native_module, "_validate_runtime_capabilities", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        native_module,
        "_register_native_runtime_shutdown",
        lambda loaded, path: registrations.append((loaded, path)),
    )
    monkeypatch.setattr(native_module, "_NativeTransportEntrypoints", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(native_module, "NativeRuntimeEntrypoints", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(native_module, "NativeTransportBinding", lambda *args: args)

    binding = native_module.load_native_transport_binding("tcp", library=library)

    assert len(binding) == 3
    assert registrations == [(library, artifact)]


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
    descriptor = TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        payload_kind=PayloadKind.TOKEN_CHUNK,
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
    descriptor = TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        payload_kind=PayloadKind.TOKEN_CHUNK,
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
    descriptor = TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        payload_kind=PayloadKind.TOKEN_CHUNK,
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


def test_native_owned_buffer_borrows_read_only_view_with_lifetime_guard(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    client = load_native_client(artifact, library=FakeRuntimeLibrary())
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    buffer = connection.acquire_buffer_copy(b"native-borrow")
    borrowed = buffer.borrow_view()

    assert isinstance(borrowed, NativeBorrowedBufferView)
    with borrowed as view:
        assert view.readonly is True
        assert view.tobytes() == b"native-borrow"
        with pytest.raises(NativeInvalidStateError, match="already active"):
            with borrowed:
                pass
        with pytest.raises(NativeInvalidStateError, match="active borrowed views"):
            buffer.close()

    buffer.close()
    with pytest.raises(NativeInvalidStateError):
        buffer.borrow_view()


def test_native_borrowed_buffer_releases_guard_when_view_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    client = load_native_client(artifact, library=FakeRuntimeLibrary())
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    buffer = connection.acquire_buffer_copy(b"native-borrow")

    def reject_borrowed_view(view: NativeBufferView) -> memoryview:
        raise RuntimeError("borrow rejected")

    monkeypatch.setattr(native_module, "_borrow_buffer_view", reject_borrowed_view)

    with pytest.raises(RuntimeError, match="borrow rejected"):
        with buffer.borrow_view():
            pass
    assert buffer._borrow_count == 0
    buffer.close()


def test_native_runtime_connection_manages_object_descriptor_handles(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    client = load_native_client(artifact, library=FakeRuntimeLibrary())
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    descriptor = ObjectDescriptorMetadata(
        object_id=101,
        object_kind=RuntimeObjectKind.TENSOR,
        producer_role=RuntimeRole.RUNTIME,
        consumer_role=RuntimeRole.CLIENT,
        session_id=42,
        byte_size=4096,
        compute_cost_units=7,
        memory_location_hint=MemoryLocationHint.DEVICE_MEMORY,
        ownership_hint=OwnershipHint.BORROWED,
        lifetime_hint_ms=250,
        metadata_bytes=8,
    )

    native_descriptor = connection.create_object_descriptor(descriptor, metadata=b"metadata")
    refreshed_descriptor, metadata_view = native_descriptor.refresh_view()
    metadata_snapshot = native_descriptor.metadata_snapshot()

    assert isinstance(native_descriptor, NativeObjectDescriptor)
    assert isinstance(native_descriptor.handle, NativeObjectDescriptorHandle)
    assert refreshed_descriptor == descriptor
    assert metadata_view.length == len(b"metadata")
    assert isinstance(metadata_snapshot, NativeObjectMetadataBuffer)
    assert metadata_snapshot.to_bytes() == b"metadata"
    assert metadata_snapshot.refresh_view().length == len(b"metadata")

    metadata_snapshot.close()
    with pytest.raises(NativeInvalidStateError):
        metadata_snapshot.to_bytes()

    native_descriptor.close()
    with pytest.raises(NativeInvalidStateError):
        native_descriptor.refresh_view()


def test_native_object_metadata_buffer_acquires_views_and_releases_handle(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    client = load_native_client(artifact, library=FakeRuntimeLibrary())
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    buffer = connection.acquire_object_metadata_copy(b"object-meta")

    assert isinstance(buffer, NativeObjectMetadataBuffer)
    assert isinstance(buffer.handle, NativeBufferHandle)
    assert buffer.view.length == len(b"object-meta")
    assert buffer.to_bytes() == b"object-meta"

    with buffer.borrow_view() as view:
        assert view.readonly is True
        assert view.tobytes() == b"object-meta"
        with pytest.raises(NativeInvalidStateError, match="active borrowed views"):
            buffer.close()

    buffer.close()
    with pytest.raises(NativeInvalidStateError):
        buffer.refresh_view()


def test_native_object_metadata_buffer_borrows_empty_view(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    client = load_native_client(artifact, library=FakeRuntimeLibrary())
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    buffer = connection.acquire_object_metadata_copy(b"")

    with buffer.borrow_view() as view:
        assert view.readonly is True
        assert view.tobytes() == b""

    buffer.close()


def test_native_object_delta_helpers_acquire_native_metadata_buffers(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    client = load_native_client(artifact, library=FakeRuntimeLibrary())
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    metadata = ObjectDeltaMetadata(
        object_id=101,
        delta_sequence=2,
        region_offset=8,
        region_bytes=4,
        delta_bytes=4,
        flags=0x03,
        metadata_bytes=2,
    )

    patch_buffer = connection.acquire_object_patch_metadata_copy(metadata, metadata_tail=b"md", delta=b"xxxx")
    delta_buffer = connection.acquire_object_delta_metadata_copy(metadata, metadata_tail=b"md", delta=b"yyyy")
    decoded_patch = decode_runtime_object_metadata(MessageType.OBJECT_PATCH, patch_buffer.to_bytes())
    decoded_delta = decode_runtime_object_metadata(MessageType.OBJECT_DELTA, delta_buffer.to_bytes())

    assert isinstance(patch_buffer, NativeObjectMetadataBuffer)
    assert decoded_patch.metadata == metadata
    assert decoded_patch.tail == b"mdxxxx"
    assert decoded_delta.metadata == metadata
    assert decoded_delta.tail == b"mdyyyy"

    patch_buffer.close()
    delta_buffer.close()


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
        profile_id=4,
        schema_id=5,
        schema_version=6,
        priority_class=NativeSessionPriorityClass.INTERACTIVE,
    )
    first_request = _native_submit_request(
        header=SubmitHeaderContext(
            flags=HeaderFlags.ACK_REQUIRED,
            view_id=3,
            route_id=4,
            trace_id=5,
        )
    )
    operation = session.submit(first_request)
    operation_scope = session.submit_operation(
        _native_submit_request(100, 8),
        scheduling_hint=NativeOperationSchedulingHint(
            parent_operation_id=99,
            operation_group_id=1234,
            deadline_ms=250,
        ),
    )
    operation_scope.cancel()
    session.cancel(frame_id=7)
    session.send_trace_context(TraceContextMetadata(1, 2, 0, 3, 0, 0))
    session.close()

    assert isinstance(client, NativeRuntimeClient)
    assert isinstance(connection, NativeRuntimeConnection)
    assert isinstance(session, NativeRuntimeSession)
    assert connection.handle.handle.id == 11
    assert session.connection.handle.id == 11
    open_request = library.nnrp_client_open_session.calls[0][0]
    assert session.handle.handle.id == open_request.session_handle_id
    assert open_request.requested_session_id == 41
    assert open_request.generation == 1
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
    assert submit_request.header_flags == int(HeaderFlags.ACK_REQUIRED)
    assert submit_request.view_id == 3
    assert submit_request.route_id == 4
    assert submit_request.trace_id == 5
    assert submit_request.payload.len == FrameSubmitMetadata.STRUCT.size + len(first_request.body)
    first_submit = _read_buffer_view(submit_request.payload)
    first_metadata = FrameSubmitMetadata.unpack(first_submit[: FrameSubmitMetadata.STRUCT.size])
    assert first_metadata.operation_id == 99
    assert first_metadata.payload_kind_bitmap == PayloadKind.TOKEN_CHUNK
    assert first_submit[FrameSubmitMetadata.STRUCT.size :] == first_request.body
    scheduled_submit_request = library.nnrp_client_submit.calls[1][0]
    assert scheduled_submit_request.operation_id == 100
    assert not hasattr(scheduled_submit_request, "parent_operation_id")
    assert not hasattr(scheduled_submit_request, "operation_group_id")
    assert not hasattr(scheduled_submit_request, "deadline_ms")
    assert not hasattr(connection, "control")
    assert library.nnrp_client_cancel.calls[0][0].frame_id == 8
    assert library.nnrp_client_cancel.calls[1][0].frame_id == 7
    assert library.runtime_frames[0][:2] == (int(MessageType.TRACE_CONTEXT), 1)


def test_native_runtime_client_binds_native_ipc_and_websocket_servers(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    submit_metadata = FrameSubmitMetadata(
        src_width=1,
        src_height=1,
        tile_width=1,
        tile_height=1,
        tile_count=1,
        section_count=0,
        frame_class=0,
        input_profile=InputProfile.DENSE_LUMA_FRAME,
        tile_index_mode=TileIndexMode.DENSE_RANGE,
        reserved0=0,
        latency_budget_ms=0,
        target_fps_x100=0,
        retry_of_frame=0,
        tile_base_id=0,
        camera_bytes=0,
        tile_index_bytes=0,
        operation_id=99,
        submit_mode=SubmitMode.INLINE,
        budget_policy=BudgetPolicy.NONE,
        payload_kind_bitmap=PayloadKind.TENSOR,
        payload_frame_count=0,
    )
    library = FakeRuntimeLibrary(
        event_payload=submit_metadata.pack() + b"server-submit",
        event_kind=EVENT_KIND_SUBMIT_ACCEPTED,
        event_message_type=int(MessageType.FRAME_SUBMIT),
    )

    client = load_native_client(artifact, library=library)
    ipc_server = client.bind_server(server_id=21, generation=2, transport_id=TRANSPORT_SLOT_IPC)
    websocket_server = client.bind_server(
        server_id=22,
        generation=3,
        transport_id=TRANSPORT_SLOT_WEBSOCKET,
    )
    session = ipc_server.accept_session(
        session_handle_id=41,
        generation=4,
        timeout_ms=25,
    )
    operation = session.receive_submit(timeout_ms=30)
    result_metadata = ResultPushMetadata(
        status_code=200,
        result_flags=ResultFlags.NONE,
        section_count=0,
        tile_count=0,
        active_profile_id=2,
        reserved0=0,
        inference_ms=1,
        queue_ms=0,
        server_total_ms=1,
        reserved1=0,
        tile_base_id=0,
        tile_index_bytes=0,
        result_class=ResultClass.COMPLETE,
        applied_budget_policy=BudgetPolicy.NONE,
        payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
        payload_frame_count=1,
    )
    operation.send_result(result_metadata, b"server-result")
    session.send_trace_context(TraceContextMetadata(1, 2, 0, 3, 0, 0))
    event = session.poll_event(timeout_ms=1)
    session.close()
    websocket_server.close()

    assert isinstance(ipc_server, NativeRuntimeServer)
    assert isinstance(websocket_server, NativeRuntimeServer)
    assert isinstance(session, NativeRuntimeServerSession)
    assert isinstance(operation, NativeRuntimeServerOperation)
    assert event is not None
    assert isinstance(event, NativeRuntimeEvent)
    assert event.header.session_id == 41
    assert ipc_server.handle.handle.id == 21
    assert websocket_server.handle.handle.id == 22
    assert session.server.handle.id == 21
    assert session.handle.handle.id == 41
    assert operation.session.handle.id == 41
    assert operation.handle.handle.id == 99
    assert operation.operation_id == 99
    assert operation.frame_id == 7
    assert operation.metadata == submit_metadata
    assert operation.body == b"server-submit"
    assert library.nnrp_server_bind.calls[0][0].transport_listener.kind == HANDLE_KIND_TRANSPORT_LISTENER
    assert library.nnrp_server_bind.calls[1][0].transport_listener.kind == HANDLE_KIND_TRANSPORT_LISTENER
    assert library.nnrp_server_accept_begin.calls[0][0].server.id == 21
    assert library.nnrp_server_accept_begin.calls[0][0].accept_handle_id == 41
    assert library.nnrp_server_accept_wait.calls[0][0].accept.id == 41
    assert library.nnrp_server_accept_wait.calls[0][0].timeout_ms == 25
    assert library.nnrp_server_accept_claim.calls[0][0].session_handle_id == 41
    assert library.nnrp_server_await_events.calls[0][0].timeout_ms == 30
    assert library.nnrp_server_await_events.calls[1][0].timeout_ms == 1
    assert library.nnrp_server_await_events.calls[1][0].max_events == 1
    assert _read_buffer_view(library.nnrp_server_send_result.calls[0][0].payload) == (
        result_metadata.pack() + b"server-result"
    )
    assert library.runtime_frames[0][:2] == (int(MessageType.TRACE_CONTEXT), 1)
    assert library.nnrp_server_close.calls[0][0].kind == HANDLE_KIND_SESSION
    assert library.nnrp_client_close_connection.calls[0][0].id == 22

    with pytest.raises(NativeInvalidStateError):
        session.receive_submit()
    with pytest.raises(NativeInvalidStateError):
        websocket_server.accept_session(
            session_handle_id=42,
            generation=4,
        )


def test_native_runtime_server_retains_accept_ticket_across_would_block() -> None:
    library = FakeRuntimeLibrary(
        active_transport_id=int(TransportId.IPC),
        accept_wait_statuses=[NativeStatus(FFI_STATUS_WOULD_BLOCK).to_ffi()],
    )
    entrypoints = NativeRuntimeEntrypoints(library)
    server = NativeRuntimeServer(
        entrypoints,
        NativeConnectionHandle.from_ffi(_NnrpHandle(HANDLE_KIND_CONNECTION, 21, 1, 0)),
        "tcp",
    )

    with pytest.raises(NativeWouldBlockError):
        server.accept_session(session_handle_id=41, generation=3, timeout_ms=1)

    session = server.accept_session(session_handle_id=41, generation=3, timeout_ms=25)

    assert session.handle.handle.id == 41
    assert session.active_transport_name == "ipc"
    assert len(library.nnrp_server_accept_begin.calls) == 1
    assert len(library.nnrp_server_accept_wait.calls) == 2
    assert len(library.nnrp_server_accept_claim.calls) == 1
    assert library.nnrp_server_accept_claim.calls[0][0].accept.id == 41
    assert library.nnrp_server_accept_claim.calls[0][0].session_handle_id == 41


def test_native_runtime_server_rejects_changed_identity_for_pending_accept_ticket() -> None:
    library = FakeRuntimeLibrary(
        accept_wait_statuses=[NativeStatus(FFI_STATUS_WOULD_BLOCK).to_ffi()],
    )
    server = NativeRuntimeServer(
        NativeRuntimeEntrypoints(library),
        NativeConnectionHandle.from_ffi(_NnrpHandle(HANDLE_KIND_CONNECTION, 21, 1, 0)),
        "tcp",
    )

    with pytest.raises(NativeWouldBlockError):
        server.accept_session(session_handle_id=41, generation=3, timeout_ms=1)

    with pytest.raises(NativeInvalidStateError, match="original session handle id and generation"):
        server.accept_session(session_handle_id=42, generation=4, timeout_ms=25)

    assert len(library.nnrp_server_accept_begin.calls) == 1
    assert len(library.nnrp_server_accept_wait.calls) == 1
    assert len(library.nnrp_server_accept_claim.calls) == 0


def test_native_runtime_server_releases_pending_accept_ticket_before_close() -> None:
    library = FakeRuntimeLibrary(
        accept_wait_statuses=[NativeStatus(FFI_STATUS_WOULD_BLOCK).to_ffi()],
    )
    entrypoints = NativeRuntimeEntrypoints(library)
    server = NativeRuntimeServer(
        entrypoints,
        NativeConnectionHandle.from_ffi(_NnrpHandle(HANDLE_KIND_CONNECTION, 21, 1, 0)),
        "tcp",
    )

    with pytest.raises(NativeWouldBlockError):
        server.accept_session(session_handle_id=41, generation=3, timeout_ms=1)
    server.close()

    assert len(library.nnrp_server_accept_release.calls) == 1
    assert library.nnrp_server_accept_release.calls[0][0].id == 41
    assert len(library.nnrp_client_close_connection.calls) == 1


def test_native_runtime_server_rejects_unknown_claimed_transport() -> None:
    library = FakeRuntimeLibrary(active_transport_id=999)
    server = NativeRuntimeServer(
        NativeRuntimeEntrypoints(library),
        NativeConnectionHandle.from_ffi(_NnrpHandle(HANDLE_KIND_CONNECTION, 21, 1, 0)),
        "tcp",
    )

    with pytest.raises(NativeArtifactError, match="unsupported transport id 999"):
        server.accept_session(session_handle_id=41, generation=3, timeout_ms=1)


def test_native_runtime_server_still_closes_connection_when_ticket_release_fails() -> None:
    library = FakeRuntimeLibrary(
        accept_wait_statuses=[NativeStatus(FFI_STATUS_WOULD_BLOCK).to_ffi()],
    )
    server = NativeRuntimeServer(
        NativeRuntimeEntrypoints(library),
        NativeConnectionHandle.from_ffi(_NnrpHandle(HANDLE_KIND_CONNECTION, 21, 1, 0)),
        "tcp",
    )
    with pytest.raises(NativeWouldBlockError):
        server.accept_session(session_handle_id=41, generation=3, timeout_ms=1)
    library.nnrp_server_accept_release.handler = lambda _accept: NativeStatus(FFI_STATUS_INVALID_STATE).to_ffi()

    with pytest.raises(NativeInvalidStateError):
        server.close()

    assert server._closed is True
    assert len(library.nnrp_client_close_connection.calls) == 1


def test_native_submit_request_shape_matches_frozen_ffi() -> None:
    assert [name for name, _field_type in _NnrpSubmitRequest._fields_] == [
        "session",
        "operation_id",
        "frame_id",
        "header_flags",
        "view_id",
        "route_id",
        "trace_id",
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
        recovery_ticket=b"runtime-ticket",
        requested_session_id=41,
        profile_id=4,
        schema_id=5,
        schema_version=6,
        resume_token_bytes=24,
    )

    assert isinstance(session, NativeRuntimeSession)
    resume_request = library.nnrp_client_resume_session.calls[0][0]
    assert session.handle.handle.id == resume_request.open.session_handle_id
    assert outcome.outcome_code == SESSION_RECOVERY_OUTCOME_RESUMED
    assert outcome.resume_window_ms == len(b"runtime-ticket") * 10
    assert resume_request.open.connection.id == 11
    assert resume_request.open.requested_session_id == 41
    assert resume_request.open.generation == 1
    assert resume_request.open.resume_token_bytes == 24
    assert _read_buffer_view(resume_request.recovery_ticket) == b"runtime-ticket"


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
        recovery_ticket=b"runtime-ticket",
        requested_session_id=41,
        profile_id=4,
        schema_id=5,
        schema_version=6,
        resume_token_bytes=24,
    )

    request = _native_submit_request(body=b"after-resume")
    operation = session.submit_operation(request)

    assert outcome.resumed is True
    assert operation.session == session.handle
    assert operation.operation_id == 99
    submit_request = library.nnrp_client_submit.calls[0][0]
    assert submit_request.session.id == session.handle.handle.id
    assert _read_buffer_view(submit_request.payload) == request.metadata.pack() + request.body


def test_native_role_event_abi_layout_matches_rust_header() -> None:
    layout = (ctypes.sizeof(ctypes.c_void_p), ctypes.alignment(ctypes.c_uint64))
    if layout == (8, 8):
        diagnostic = (48, 32, 40)
        header = (32, 24)
        event = (200, 8, 40, 64, 88, 112, 136, 152)
    elif layout == (4, 8):
        diagnostic = (48, 32, 40)
        header = (32, 24)
        event = (192, 8, 40, 64, 88, 112, 136, 144)
    elif layout == (4, 4):
        diagnostic = (40, 28, 36)
        header = (28, 20)
        event = (160, 4, 32, 52, 72, 92, 112, 120)
    else:
        pytest.fail(f"unsupported FFI ABI layout: {layout}")

    assert ctypes.sizeof(_NnrpFfiDiagnostic) == diagnostic[0]
    assert _NnrpFfiDiagnostic.status.offset == 0
    assert _NnrpFfiDiagnostic.related_connection_id.offset == 16
    assert _NnrpFfiDiagnostic.related_session_id.offset == 24
    assert _NnrpFfiDiagnostic.related_operation_id.offset == diagnostic[1]
    assert _NnrpFfiDiagnostic.related_frame_id.offset == diagnostic[2]

    assert ctypes.sizeof(_NnrpRuntimeFrameHeader) == header[0]
    assert _NnrpRuntimeFrameHeader.present.offset == 0
    assert _NnrpRuntimeFrameHeader.flags.offset == 4
    assert _NnrpRuntimeFrameHeader.session_id.offset == 8
    assert _NnrpRuntimeFrameHeader.frame_id.offset == 12
    assert _NnrpRuntimeFrameHeader.view_id.offset == 16
    assert _NnrpRuntimeFrameHeader.route_id.offset == 18
    assert _NnrpRuntimeFrameHeader.trace_id.offset == header[1]

    assert ctypes.sizeof(_NnrpEvent) == event[0]
    assert _NnrpEvent.kind.offset == 0
    assert _NnrpEvent.header.offset == event[1]
    assert _NnrpEvent.connection.offset == event[2]
    assert _NnrpEvent.session.offset == event[3]
    assert _NnrpEvent.operation.offset == event[4]
    assert _NnrpEvent.payload_owner.offset == event[5]
    assert _NnrpEvent.payload.offset == event[6]
    assert _NnrpEvent.diagnostic.offset == event[7]


def test_native_role_request_abi_layout_matches_rust_header() -> None:
    layout = (ctypes.sizeof(ctypes.c_void_p), ctypes.alignment(ctypes.c_uint64))
    if layout == (8, 8):
        expected = (24, 8, 64, 24, 48, 60, 40, 144, 88, 104, 72, 40, 224, 24)
    elif layout == (4, 8):
        expected = (24, 8, 56, 16, 40, 52, 40, 120, 80, 88, 64, 40, 216, 24)
    elif layout == (4, 4):
        expected = (20, 4, 52, 16, 36, 48, 36, 108, 72, 80, 56, 36, 184, 20)
    else:
        pytest.fail(f"unsupported FFI ABI layout: {layout}")

    (
        handle_size,
        handle_id,
        open_size,
        open_config,
        open_max_packet,
        open_reserved,
        adoption_size,
        server_bind_size,
        session_open_size,
        session_resume_size,
        submit_size,
        role_poll_size,
        poll_result_size,
        poll_result_event,
    ) = expected

    assert ctypes.sizeof(_NnrpHandle) == handle_size
    assert _NnrpHandle.kind.offset == 0
    assert _NnrpHandle.id.offset == handle_id
    assert _NnrpHandle.generation.offset == handle_id + 8
    assert _NnrpHandle.flags.offset == handle_id + 12

    assert ctypes.sizeof(_NnrpTransportOpenRequest) == open_size
    assert _NnrpTransportOpenRequest.endpoint.offset == 8
    assert _NnrpTransportOpenRequest.config.offset == open_config
    assert _NnrpTransportOpenRequest.max_packet_bytes.offset == open_max_packet
    assert _NnrpTransportOpenRequest.reserved0.offset == open_reserved

    assert ctypes.sizeof(_NnrpClientConnectRequest) == adoption_size
    assert _NnrpClientConnectRequest.reserved0.offset == 12
    assert _NnrpClientConnectRequest.transport_connection.offset == 16
    assert ctypes.sizeof(_NnrpServerBindRequest) == server_bind_size
    assert _NnrpServerBindRequest.reserved0.offset == 12
    assert _NnrpServerBindRequest.transport_listener.offset == 16

    assert ctypes.sizeof(_NnrpSessionOpenRequest) == session_open_size
    assert _NnrpSessionOpenRequest.requested_session_id.offset == handle_size
    assert _NnrpSessionOpenRequest.session_handle_id.offset == (32 if handle_size == 24 else 24)
    assert _NnrpSessionOpenRequest.generation.offset == (40 if handle_size == 24 else 32)
    assert _NnrpSessionOpenRequest.profile_id.offset == (44 if handle_size == 24 else 36)
    assert _NnrpSessionOpenRequest.schema_id.offset == (48 if handle_size == 24 else 40)
    assert _NnrpSessionOpenRequest.cache_hints.offset == (72 if handle_size == 24 else 64)
    assert ctypes.sizeof(_NnrpSessionResumeRequest) == session_resume_size
    assert _NnrpSessionResumeRequest.open.offset == 0
    assert _NnrpSessionResumeRequest.recovery_ticket.offset == session_open_size
    assert ctypes.sizeof(_NnrpSubmitRequest) == submit_size
    assert _NnrpSubmitRequest.operation_id.offset == handle_size
    assert _NnrpSubmitRequest.frame_id.offset == handle_size + 8
    assert _NnrpSubmitRequest.header_flags.offset == handle_size + 12
    assert _NnrpSubmitRequest.view_id.offset == handle_size + 16
    assert _NnrpSubmitRequest.route_id.offset == handle_size + 18
    assert _NnrpSubmitRequest.trace_id.offset == handle_size + 24

    assert ctypes.sizeof(_NnrpRoleEventPollRequest) == role_poll_size
    assert _NnrpRoleEventPollRequest.max_events.offset == handle_size
    assert ctypes.sizeof(_NnrpServerAcceptBeginRequest) == adoption_size
    assert ctypes.sizeof(_NnrpServerAcceptClaimRequest) == adoption_size
    assert ctypes.sizeof(_NnrpServerAcceptWaitRequest) == handle_size + 8
    assert ctypes.sizeof(_NnrpServerAcceptResult) == handle_size + 8
    assert ctypes.sizeof(_NnrpPollResult) == poll_result_size
    assert _NnrpPollResult.has_event.offset == 16
    assert _NnrpPollResult.event.offset == poll_result_event


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
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    backend = session.cache_backend(now_ms=1000, ttl_ms=500, expected_version=9)
    identity = CacheObjectIdentity(cache_namespace=1, object_kind=2, cache_key_hi=3, cache_key_lo=4)

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
    assert query.lease.object_version == 9
    assert query.lease.lease_id == 800
    assert query.lease.owner_scope is CacheLeaseOwnerScope.SESSION
    assert query.lease.owner_id == 41
    assert query.lease.granted_at_ms == 1000
    assert query.lease.ttl_ms == 30_000
    assert query.lease.expires_at_ms == 31_000
    assert touch.lease is not None
    assert touch.lease.granted_at_ms == 1000
    assert touch.lease.ttl_ms == 750
    assert prefetched[0].identity == identity
    assert released.outcome is CacheLeaseOutcome.RELEASED
    assert missing.outcome is CacheLeaseOutcome.MISSING
    cache_request = library.nnrp_cache_query.calls[0][0]
    assert cache_request.owner.id == session.handle.handle.id
    assert cache_request.owner.id != query.lease.owner_id
    assert cache_request.object_id.cache_namespace == 1
    assert cache_request.object_id.object_kind == 2
    assert ctypes.sizeof(_NnrpCacheObjectId) == 24
    assert _NnrpCacheObjectId.object_kind.offset == 4
    assert _NnrpCacheObjectId.cache_key_hi.offset == 8
    assert _NnrpCacheObjectId.cache_key_lo.offset == 16
    assert ctypes.sizeof(_NnrpCacheLeaseResult) == 96
    assert _NnrpCacheLeaseResult.outcome_code.offset == 0
    assert _NnrpCacheLeaseResult.lease_handle.offset == 8
    assert _NnrpCacheLeaseResult.object_id.offset == 32
    assert _NnrpCacheLeaseResult.object_version.offset == 56
    assert _NnrpCacheLeaseResult.lease_id.offset == 64
    assert _NnrpCacheLeaseResult.owner_scope.offset == 72
    assert _NnrpCacheLeaseResult.ttl_ms.offset == 76
    assert _NnrpCacheLeaseResult.owner_id.offset == 80
    assert _NnrpCacheLeaseResult.granted_at_ms.offset == 88
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
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    backend = session.cache_backend(now_ms=5000, expected_version=9)
    identity = CacheObjectIdentity(cache_namespace=1, object_kind=2, cache_key_hi=3, cache_key_lo=4)

    result = backend.query_cache(identity)

    assert result.outcome is CacheLeaseOutcome.EXPIRED
    assert result.lease is not None
    assert result.lease.is_expired(5000) is True
    assert result.lease.granted_at_ms == 5000
    assert result.lease.ttl_ms == 0
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
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    with pytest.raises(NativeHandleError, match="parent_operation_id conflicts"):
        session.submit_operation(
            _native_submit_request(100, 8),
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
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    second_session = connection.open_session(
        requested_session_id=42,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )

    first_operation = first_session.submit_operation(_native_submit_request(99, 7, b""))
    second_operation = second_session.submit_operation(_native_submit_request(100, 8, b""))
    first_open = library.nnrp_client_open_session.calls[0][0]
    second_open = library.nnrp_client_open_session.calls[1][0]

    assert first_session.connection == second_session.connection == connection.handle
    assert first_session.handle.handle.id == first_open.session_handle_id
    assert second_session.handle.handle.id == second_open.session_handle_id
    assert first_open.session_handle_id != second_open.session_handle_id
    assert first_operation.session == first_session.handle
    assert second_operation.session == second_session.handle
    assert first_open.requested_session_id == 41
    assert second_open.requested_session_id == 42
    assert first_open.generation == second_open.generation == 1
    assert library.nnrp_client_submit.calls[0][0].session.id == first_open.session_handle_id
    assert library.nnrp_client_submit.calls[1][0].session.id == second_open.session_handle_id


def test_native_runtime_connections_isolate_resource_handles_from_duplicate_protocol_session_ids(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    client = load_native_client(artifact, library=library)
    first_connection = client.connect(connection_id=11, generation=1, transport_id=TRANSPORT_SLOT_TCP)
    second_connection = client.connect(connection_id=12, generation=1, transport_id=TRANSPORT_SLOT_TCP)

    first_session = first_connection.open_session(
        requested_session_id=42,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    second_session = second_connection.open_session(
        requested_session_id=42,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    first_open, second_open = (call[0] for call in library.nnrp_client_open_session.calls[-2:])

    assert first_open.requested_session_id == second_open.requested_session_id == 42
    assert first_open.session_handle_id != second_open.session_handle_id
    assert first_session.handle.handle.id == first_open.session_handle_id
    assert second_session.handle.handle.id == second_open.session_handle_id


def test_native_runtime_session_recovery_ticket_copies_and_releases_native_owner(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = _open_event_session(
        load_native_client(artifact, library=library).connect(
            connection_id=12,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
    )

    ticket = session.recovery_ticket()

    assert ticket is not None
    assert ticket.session_id == 41
    assert ticket.resume_token == b"fake-runtime-token"
    assert ticket.resume_from_operation_id == 99
    assert ticket.resume_window_ms == 120_000
    assert library._buffers == {}

    library.nnrp_client_session_recovery_ticket.handler = lambda *_args: _NnrpFfiStatus(
        FFI_STATUS_INVALID_ARGUMENT,
        0,
        0,
        104,
    )
    assert session.recovery_ticket() is None


def test_native_runtime_session_awaits_empty_event(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()

    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)
    result = session.await_event()

    assert connection.handle.handle.id == 12
    assert isinstance(result, NativeRuntimePollResult)
    assert result.event is None


def test_native_runtime_session_event_snapshot_copies_payload(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")

    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)
    result = session.await_event()

    assert result.event is not None
    assert isinstance(result.event, NativeLifecycleEvent)
    assert result.event.kind == 6
    assert result.event.payload == b"result"
    assert result.event.connection.id == 12
    assert result.event.session.id == session.handle.handle.id
    assert result.event.operation.id == 99
    assert result.event.diagnostic.status.succeeded is True


def test_native_runtime_session_event_snapshot_survives_native_buffer_reuse(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")

    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)
    result = session.await_event()
    assert result.event is not None

    assert library._event_payload_owner is not None
    library._event_payload_owner.value = b"reuse!"

    assert result.event.payload == b"result"


def test_native_runtime_frame_event_copies_and_releases_owned_payload() -> None:
    library = FakeRuntimeLibrary()
    entrypoints = NativeRuntimeEntrypoints(library)
    metadata = ProgressMetadata(
        operation_id=42,
        progress_sequence=7,
        stage_code=3,
        percent_x100=2500,
        object_id=11,
        body_bytes=4,
    )
    payload = encode_runtime_control_metadata(MessageType.PROGRESS, metadata, tail=b"step")
    owner = ctypes.create_string_buffer(payload, len(payload))
    library._buffers[777] = owner
    event = _NnrpEvent()
    event.kind = EVENT_KIND_RUNTIME_FRAME
    _write_event_header(event, message_type=int(MessageType.PROGRESS), frame_id=9)
    event.connection = _NnrpHandle(HANDLE_KIND_CONNECTION, 12, 2, 0)
    event.session = _NnrpHandle(HANDLE_KIND_SESSION, 41, 3, 0)
    event.operation = _NnrpHandle(HANDLE_KIND_OPERATION, 42, 1, 0)
    event.payload_owner = _NnrpHandle(HANDLE_KIND_BUFFER, 777, 1, 0)
    event.payload = _NnrpBufferView(ctypes.cast(owner, ctypes.c_void_p), len(payload))
    event.diagnostic.status = NativeStatus.ok().to_ffi()

    snapshot = _native_event_from_ffi(event, entrypoints)
    owner[0] = b"x"

    assert 777 not in library._buffers
    assert isinstance(snapshot, NativeRuntimeEvent)
    assert snapshot.header.message_type is MessageType.PROGRESS
    assert snapshot.metadata.value == metadata
    assert snapshot.tail.body == b"step"
    assert snapshot.header.frame_id == 9


@pytest.mark.parametrize(
    ("message_type", "payload", "expected_fields"),
    [
        (
            MessageType.CANCEL,
            encode_runtime_control_metadata(
                MessageType.CANCEL,
                ControlRequestMetadata(42, 1, 0, RuntimeRole.CLIENT, 0, 2),
                tail=b"no",
            ),
            {"diagnostic": b"no"},
        ),
        (
            MessageType.OBJECT_PATCH,
            encode_runtime_object_metadata(
                MessageType.OBJECT_PATCH,
                ObjectDeltaMetadata(9, 2, 128, 64, 4, 0x03, 2),
                tail=b"mdxxxx",
            ),
            {"metadata_body": b"md", "delta": b"xxxx"},
        ),
        (
            MessageType.CACHE_INVALIDATE,
            CacheInvalidateMetadata(
                invalidate_scope=CacheInvalidateScope.OBJECT_KEY,
                cache_namespace=3,
                cache_key_hi=4,
                cache_key_lo=5,
                reason_code=6,
            ).pack(),
            {},
        ),
    ],
)
def test_native_runtime_frame_event_decodes_frozen_payload_families(
    message_type: MessageType,
    payload: bytes,
    expected_fields: dict[str, bytes],
) -> None:
    event = _decode_test_wire_event(message_type, payload)

    assert event.header.message_type is message_type
    for field_name, expected in expected_fields.items():
        assert getattr(event.tail, field_name) == expected


def test_native_runtime_event_rejects_unknown_runtime_message_type() -> None:
    with pytest.raises(NativeProtocolError, match="unknown Preview4 runtime message type"):
        _decode_test_wire_event(0xFFFF, b"")


def test_native_runtime_event_reports_unknown_wire_format_separately() -> None:
    with pytest.raises(NativeProtocolError, match="unknown Preview4 runtime wire format 255"):
        _decode_test_wire_event(MessageType.PROGRESS, b"", wire_format=255)


def test_native_runtime_session_polls_and_iterates_named_runtime_frames(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    metadata = ProgressMetadata(42, 7, 3, 2500, 11, 4)
    payload = encode_runtime_control_metadata(MessageType.PROGRESS, metadata, tail=b"step")

    def create_session() -> NativeRuntimeSession:
        library = FakeRuntimeLibrary(
            event_payload=payload,
            event_kind=EVENT_KIND_RUNTIME_FRAME,
            event_message_type=int(MessageType.PROGRESS),
        )
        connection = load_native_client(artifact, library=library).connect(
            connection_id=12,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
        return _open_event_session(connection)

    frames = create_session().poll_runtime_frames(max_events=1)

    async def collect() -> tuple[NativeRuntimeEvent, ...]:
        return tuple([frame async for frame in create_session().iter_runtime_frames(max_events=1)])

    async_frames = asyncio.run(collect())

    assert [(frame.header.message_type, frame.tail.body) for frame in frames] == [(MessageType.PROGRESS, b"step")]
    assert [(frame.header.message_type, frame.tail.body) for frame in async_frames] == [(MessageType.PROGRESS, b"step")]


def test_native_runtime_session_frame_poll_preserves_lifecycle_events(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    session = _open_event_session(
        load_native_client(artifact, library=FakeRuntimeLibrary()).connect(
            connection_id=12,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
    )
    lifecycle = NativeLifecycleEvent(
        EVENT_KIND_CONTROL,
        NativeHandle.invalid(),
        NativeHandle.invalid(),
        NativeHandle.invalid(),
        b"lifecycle",
        _NATIVE_RUNTIME_DIAGNOSTIC_OK,
    )
    runtime = _decode_test_wire_event(
        MessageType.PROGRESS,
        encode_runtime_control_metadata(
            MessageType.PROGRESS,
            ProgressMetadata(42, 7, 3, 2500, 11, 0),
        ),
    )
    session._pending_events.extend((lifecycle, runtime))

    assert session.poll_runtime_frames(max_events=1) == (runtime,)
    assert session.poll_event() is lifecycle


def test_native_runtime_session_frame_poll_handles_zero_limit_and_would_block(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = _open_event_session(
        load_native_client(artifact, library=library).connect(
            connection_id=12,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
    )

    assert session.poll_runtime_frames(max_events=0) == ()
    library.status = NativeStatus(FFI_STATUS_WOULD_BLOCK).to_ffi()
    assert session.poll_runtime_frames(max_events=1) == ()


def test_native_runtime_server_frame_poll_preserves_lifecycle_events() -> None:
    library = FakeRuntimeLibrary()
    server = NativeRuntimeServer(
        NativeRuntimeEntrypoints(library),
        NativeConnectionHandle.from_ffi(_NnrpHandle(HANDLE_KIND_CONNECTION, 21, 1, 0)),
        "tcp",
    )
    session = server.accept_session(session_handle_id=41, generation=3)
    lifecycle = NativeLifecycleEvent(
        EVENT_KIND_CONTROL,
        NativeHandle.invalid(),
        NativeHandle.invalid(),
        NativeHandle.invalid(),
        b"lifecycle",
        _NATIVE_RUNTIME_DIAGNOSTIC_OK,
    )
    runtime = _decode_test_wire_event(
        MessageType.PROGRESS,
        encode_runtime_control_metadata(
            MessageType.PROGRESS,
            ProgressMetadata(42, 7, 3, 2500, 11, 0),
        ),
    )
    session._pending_events.extend((lifecycle, runtime))

    assert session.poll_runtime_frames(max_events=1) == (runtime,)
    assert session.poll_event() is lifecycle


def test_native_runtime_server_frame_poll_preserves_native_lifecycle_event() -> None:
    library = FakeRuntimeLibrary(event_payload=b"lifecycle", event_kind=EVENT_KIND_CONTROL)
    server = NativeRuntimeServer(
        NativeRuntimeEntrypoints(library),
        NativeConnectionHandle.from_ffi(_NnrpHandle(HANDLE_KIND_CONNECTION, 21, 1, 0)),
        "tcp",
    )
    session = server.accept_session(session_handle_id=41, generation=3)

    assert session.poll_runtime_frames(max_events=0) == ()
    assert session.poll_runtime_frames(max_events=1) == ()
    lifecycle = session.poll_event()
    assert isinstance(lifecycle, NativeLifecycleEvent)
    assert lifecycle.payload == b"lifecycle"


def test_native_runtime_server_frame_poll_reads_native_runtime_event() -> None:
    metadata = ProgressMetadata(42, 7, 3, 2500, 11, 0)
    library = FakeRuntimeLibrary(
        event_payload=encode_runtime_control_metadata(MessageType.PROGRESS, metadata),
        event_kind=EVENT_KIND_RUNTIME_FRAME,
        event_message_type=int(MessageType.PROGRESS),
    )
    server = NativeRuntimeServer(
        NativeRuntimeEntrypoints(library),
        NativeConnectionHandle.from_ffi(_NnrpHandle(HANDLE_KIND_CONNECTION, 21, 1, 0)),
        "tcp",
    )
    session = server.accept_session(session_handle_id=41, generation=3)

    frames = session.poll_runtime_frames(max_events=1)

    assert len(frames) == 1
    assert frames[0].header.message_type is MessageType.PROGRESS
    assert frames[0].metadata.value == metadata


def test_local_lifecycle_event_has_no_fabricated_runtime_header() -> None:
    event = NativeLifecycleEvent(
        EVENT_KIND_CONTROL,
        NativeHandle.invalid(),
        NativeHandle.invalid(),
        NativeHandle.invalid(),
        b"",
        _NATIVE_RUNTIME_DIAGNOSTIC_OK,
    )

    assert not hasattr(event, "header")


def test_native_runtime_sessions_expose_named_preview4_methods_without_raw_frame_api() -> None:
    client_methods = {
        "cancel_operation",
        "abort_operation",
        "update_priority",
        "update_deadline",
        "expire_at",
        "supersede",
        "update_budget",
        "negotiate_capabilities",
        "degrade_profile",
        "send_route_hint",
        "send_execution_hint",
        "send_trace_context",
        "declare_object",
        "reference_object",
        "release_object",
        "patch_object",
        "send_object_delta",
        "reference_cache",
        "report_cache_miss",
        "invalidate_cache",
    }
    server_methods = {
        "send_progress",
        "send_partial_result",
        "send_backpressure",
        "send_credit_update",
        "send_result_drop_reason",
        "send_trace_context",
        "send_recoverable_error",
        "send_retry_after",
        "declare_object",
        "reference_object",
        "release_object",
        "patch_object",
        "send_object_delta",
        "reference_cache",
        "report_cache_miss",
        "invalidate_cache",
    }

    assert all(callable(getattr(NativeRuntimeSession, name, None)) for name in client_methods)
    assert all(callable(getattr(NativeRuntimeServerSession, name, None)) for name in server_methods)
    assert not hasattr(NativeRuntimeSession, "control")
    assert not hasattr(NativeRuntimeSession, "send_runtime_frame")
    assert not hasattr(NativeRuntimeServerSession, "control")
    assert not hasattr(NativeRuntimeServerSession, "send_runtime_frame")


def test_native_runtime_client_named_methods_share_one_coarse_frame_abi(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(
            connection_id=12,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
        .open_session(
            requested_session_id=42,
            profile_id=0,
            schema_id=0,
            schema_version=0,
        )
    )
    control = ControlRequestMetadata(10, 1, 0, RuntimeRole.CLIENT, 0, 2)
    scheduling = SchedulingMetadata(10, 2, 4, -1, 1000, 0)
    supersede = SupersedeMetadata(10, 11, 3, ResultDropReasonCode.SUPERSEDED, 0, 2)
    budget = BudgetMetadata(10, 20, 30, 40, 50, 0)
    capability = CapabilityMetadata(3, 1, 4, 2, 99, 88, 2, 0)
    route = RouteHintMetadata(10, 20, 2, 3, 1000, 2, 0)
    trace = TraceContextMetadata(1, 2, 0, 3, 0, 2)
    descriptor = ObjectDescriptorMetadata(
        9,
        RuntimeObjectKind.IMAGE_TILE,
        RuntimeRole.RUNTIME,
        RuntimeRole.CLIENT,
        3,
        4096,
        12,
        MemoryLocationHint.HOST_MEMORY,
        OwnershipHint.CONSUMER_OWNED,
        1000,
        2,
    )
    object_ref = ObjectReferenceMetadata(9, 10, 2, 0, 4096, 0, 2)
    release = ObjectReleaseMetadata(9, 10, ObjectReleaseReason.COMPLETED, RuntimeRole.CLIENT, 0, 2)
    delta = ObjectDeltaMetadata(9, 2, 128, 64, 4, 0x03, 2)
    cache_ref = CacheReferenceMetadata(7, 1, 2, 3, CacheReuseScope.SESSION, 4, 5, 1000, 2, 0)
    cache_miss = CacheMissMetadata(7, 1, 2, CacheMissReason.UNKNOWN, 3, 2)
    invalidate = CacheInvalidateMetadata(CacheInvalidateScope.OBJECT_KEY, 3, 4, 5, 6)

    session.cancel_operation(control, b"no")
    session.abort_operation(control, b"no")
    session.update_priority(scheduling)
    session.update_deadline(scheduling)
    session.expire_at(scheduling)
    session.supersede(supersede, b"no")
    session.update_budget(budget)
    session.negotiate_capabilities(capability, b"{}")
    session.degrade_profile(capability, b"{}")
    session.send_route_hint(route, b"rt")
    session.send_execution_hint(route, b"rt")
    session.send_trace_context(trace, b"tr")
    session.declare_object(descriptor, b"md")
    session.reference_object(object_ref, b"md")
    session.release_object(release, b"ok")
    session.patch_object(delta, b"data", b"md")
    session.send_object_delta(delta, b"data", b"md")
    session.reference_cache(cache_ref, b"md")
    session.report_cache_miss(cache_miss, b"no")
    session.invalidate_cache(invalidate)

    expected_types = [
        MessageType.CANCEL,
        MessageType.ABORT,
        MessageType.PRIORITY_UPDATE,
        MessageType.DEADLINE,
        MessageType.EXPIRE_AT,
        MessageType.SUPERSEDE,
        MessageType.BUDGET_UPDATE,
        MessageType.CAPABILITY_NEGOTIATION,
        MessageType.DEGRADE_PROFILE,
        MessageType.ROUTE_HINT,
        MessageType.EXECUTION_HINT,
        MessageType.TRACE_CONTEXT,
        MessageType.OBJECT_DECLARE,
        MessageType.OBJECT_REF,
        MessageType.OBJECT_RELEASE,
        MessageType.OBJECT_PATCH,
        MessageType.OBJECT_DELTA,
        MessageType.CACHE_REFERENCE,
        MessageType.CACHE_MISS,
        MessageType.CACHE_INVALIDATE,
    ]
    assert [message_type for message_type, _frame_id, _payload in library.runtime_frames] == [
        int(message_type) for message_type in expected_types
    ]
    assert [frame_id for _message_type, frame_id, _payload in library.runtime_frames] == list(range(1, 21))
    assert decode_runtime_control_metadata(MessageType.CANCEL, library.runtime_frames[0][2]).tail == b"no"
    assert decode_runtime_object_metadata(MessageType.OBJECT_PATCH, library.runtime_frames[15][2]).tail == b"mddata"


def test_native_runtime_server_named_methods_share_one_coarse_frame_abi(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .bind_server(
            server_id=21,
            generation=2,
            transport_id=TRANSPORT_SLOT_IPC,
        )
        .accept_session(
            session_handle_id=42,
            generation=3,
        )
    )
    progress = ProgressMetadata(10, 1, 2, 2500, 20, 4)
    partial = PartialResultMetadata(10, 2, 20, 1, 4, 0)
    pressure = PressureMetadata(10, 4, 2, 1, 5, 0)
    drop = ResultDropReasonMetadata(10, 1, ResultDropReasonCode.PEER_CANCELLED, RuntimeRole.RUNTIME, 0, 2)
    trace = TraceContextMetadata(1, 2, 0, 3, 0, 2)
    recoverable = RecoverableErrorMetadata(20, 21, 22, RuntimeRole.RUNTIME, 0, 23, 24, 25, 26, 2)
    retry = RetryAfterMetadata(10, 1, 100, 10, 2, RuntimeRole.RUNTIME, 0, 2)
    descriptor = ObjectDescriptorMetadata(
        9,
        RuntimeObjectKind.IMAGE_TILE,
        RuntimeRole.RUNTIME,
        RuntimeRole.CLIENT,
        3,
        4096,
        12,
        MemoryLocationHint.HOST_MEMORY,
        OwnershipHint.CONSUMER_OWNED,
        1000,
        2,
    )
    object_ref = ObjectReferenceMetadata(9, 10, 2, 0, 4096, 0, 2)
    release = ObjectReleaseMetadata(9, 10, ObjectReleaseReason.COMPLETED, RuntimeRole.RUNTIME, 0, 2)
    delta = ObjectDeltaMetadata(9, 2, 128, 64, 4, 0x03, 2)
    cache_ref = CacheReferenceMetadata(7, 1, 2, 3, CacheReuseScope.SESSION, 4, 5, 1000, 2, 0)
    cache_miss = CacheMissMetadata(7, 1, 2, CacheMissReason.UNKNOWN, 3, 2)
    invalidate = CacheInvalidateMetadata(CacheInvalidateScope.OBJECT_KEY, 3, 4, 5, 6)

    session.send_progress(progress, b"step")
    session.send_partial_result(partial, b"part")
    session.send_backpressure(pressure)
    session.send_credit_update(pressure)
    session.send_result_drop_reason(drop, b"no")
    session.send_trace_context(trace, b"tr")
    session.send_recoverable_error(recoverable, b"er")
    session.send_retry_after(retry, b"ra")
    session.declare_object(descriptor, b"md")
    session.reference_object(object_ref, b"md")
    session.release_object(release, b"ok")
    session.patch_object(delta, b"data", b"md")
    session.send_object_delta(delta, b"data", b"md")
    session.reference_cache(cache_ref, b"md")
    session.report_cache_miss(cache_miss, b"no")
    session.invalidate_cache(invalidate)

    expected_types = [
        MessageType.PROGRESS,
        MessageType.PARTIAL_RESULT,
        MessageType.BACKPRESSURE,
        MessageType.CREDIT_UPDATE,
        MessageType.RESULT_DROP_REASON,
        MessageType.TRACE_CONTEXT,
        MessageType.ERROR_RECOVERABLE,
        MessageType.RETRY_AFTER,
        MessageType.OBJECT_DECLARE,
        MessageType.OBJECT_REF,
        MessageType.OBJECT_RELEASE,
        MessageType.OBJECT_PATCH,
        MessageType.OBJECT_DELTA,
        MessageType.CACHE_REFERENCE,
        MessageType.CACHE_MISS,
        MessageType.CACHE_INVALIDATE,
    ]
    assert [message_type for message_type, _frame_id, _payload in library.runtime_frames] == [
        int(message_type) for message_type in expected_types
    ]
    assert [frame_id for _message_type, frame_id, _payload in library.runtime_frames] == list(range(1, 17))
    assert decode_runtime_control_metadata(MessageType.PROGRESS, library.runtime_frames[0][2]).tail == b"step"
    assert decode_runtime_object_metadata(MessageType.OBJECT_DELTA, library.runtime_frames[12][2]).tail == b"mddata"


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
        profile_id=0,
        schema_id=0,
        schema_version=0,
    )
    payload = bytearray(b"before")

    request = _native_submit_request(body=payload)
    session.submit_operation(request)
    payload[:] = b"after!"

    assert library.submitted_payloads[0] == request.metadata.pack() + request.body


def test_native_submit_rejects_metadata_operation_identity_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = _open_event_session(
        load_native_client(artifact, library=library).connect(
            connection_id=12,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
    )
    request = _native_submit_request()
    request = replace(request, metadata=replace(request.metadata, operation_id=100))

    with pytest.raises(ValueError, match="metadata.operation_id must equal"):
        session.submit_operation(request)

    assert library.nnrp_client_submit.calls == []


def test_native_result_keeps_wire_operation_identity_separate_from_handle_identity(tmp_path: Path) -> None:
    class DistinctOperationHandleLibrary(FakeRuntimeLibrary):
        def _submit(self, request: _NnrpSubmitRequest, out_handle: object) -> _NnrpFfiStatus:
            self.submitted_payloads.append(_read_buffer_view(request.payload))
            _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_OPERATION, 500, 1, 0))
            return self.status

        def _await_events(
            self,
            request: _NnrpRoleEventPollRequest,
            out_events: object,
            event_capacity: int,
            out_event_count: object,
        ) -> _NnrpFfiStatus:
            assert event_capacity >= 1
            count_target = getattr(out_event_count, "_obj", None)
            if count_target is None:
                count_target = ctypes.cast(out_event_count, ctypes.POINTER(ctypes.c_size_t)).contents
            events = ctypes.cast(out_events, ctypes.POINTER(_NnrpEvent))
            events[0].kind = EVENT_KIND_RESULT_PUSHED
            events[0].connection = _NnrpHandle(HANDLE_KIND_CONNECTION, 12, 2, 0)
            events[0].session = request.scope
            events[0].operation = _NnrpHandle(HANDLE_KIND_OPERATION, 500, 1, 0)
            _write_event_header(events[0], message_type=0, frame_id=7)
            events[0].payload = _NnrpBufferView(
                ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                len(self._event_payload_owner.raw),
            )
            events[0].diagnostic.status = NativeStatus.ok().to_ffi()
            events[0].diagnostic.related_operation_id = 99
            events[0].diagnostic.related_frame_id = 7
            count_target.value = 1
            return self.status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = DistinctOperationHandleLibrary(event_payload=_native_token_result_payload())
    session = _open_event_session(
        load_native_client(artifact, library=library).connect(
            connection_id=12,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )
    )

    operation = session.submit_operation(_native_submit_request(body=b"request"))
    result = session.poll_result(operation, max_events=1)

    assert operation.handle.handle.id == 500
    assert result.operation_id == 99
    lifecycle = result.event.as_lifecycle()
    assert lifecycle == OperationLifecycleEvent(99, OperationState.COMPLETED)


def test_native_runtime_result_preserves_lifecycle_surface(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=_native_token_result_payload())
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    event = _open_event_session(connection).poll_event()

    assert event is not None
    result = NativeRuntimeResult._from_polled_event(event)

    assert result.terminal_state is ResultTerminalState.SUCCESS
    assert result.operation_id == 99
    assert result.event.kind is NativeTerminalEventKind.LIFECYCLE
    assert result.event.as_lifecycle() == OperationLifecycleEvent(99, OperationState.COMPLETED)
    assert not any(hasattr(result, name) for name in ("payload", "frame_id", "body", "metadata", "diagnostic", "state"))


@pytest.mark.parametrize(
    ("operation_state", "terminal_state"),
    [
        (OperationState.COMPLETED, ResultTerminalState.SUCCESS),
        (OperationState.CANCELLED, ResultTerminalState.CANCELLED),
        (OperationState.SUPERSEDED, ResultTerminalState.DROPPED),
        (OperationState.FAILED, ResultTerminalState.ERROR),
    ],
)
def test_native_runtime_result_preserves_exact_local_terminal_state(
    operation_state: OperationState,
    terminal_state: ResultTerminalState,
) -> None:
    lifecycle = OperationLifecycleEvent(99, operation_state)
    result = NativeRuntimeResult(99, terminal_state, NativeTerminalEvent.lifecycle(lifecycle))

    assert result.event.as_lifecycle() is lifecycle
    assert result.terminal_state is terminal_state


def test_native_runtime_result_rejects_inconsistent_terminal_evidence() -> None:
    completed = NativeTerminalEvent.lifecycle(OperationLifecycleEvent(99, OperationState.COMPLETED))
    with pytest.raises(ValueError, match="terminal_state ERROR does not match"):
        NativeRuntimeResult(99, ResultTerminalState.ERROR, completed)

    wrong_operation = NativeTerminalEvent.lifecycle(OperationLifecycleEvent(100, OperationState.COMPLETED))
    with pytest.raises(ValueError, match="operation_id must match"):
        NativeRuntimeResult(99, ResultTerminalState.SUCCESS, wrong_operation)

    nonterminal = NativeTerminalEvent.lifecycle(OperationLifecycleEvent(99, OperationState.RUNNING))
    with pytest.raises(ValueError, match="not a terminal operation state"):
        NativeRuntimeResult(99, ResultTerminalState.SUCCESS, nonterminal)

    with pytest.raises(ValueError, match="operation_id"):
        NativeRuntimeResult(0, ResultTerminalState.SUCCESS, completed)
    with pytest.raises(TypeError, match="NativeTerminalEvent"):
        NativeRuntimeResult(99, ResultTerminalState.SUCCESS, object())
    with pytest.raises(TypeError, match="runtime terminal event requires NativeRuntimeEvent"):
        NativeTerminalEvent(NativeTerminalEventKind.RUNTIME, completed.value)


def test_native_runtime_result_validates_runtime_terminal_evidence() -> None:
    pushed = _decode_test_wire_event(
        MessageType.RESULT_PUSH,
        _native_token_result_payload(),
        kind=EVENT_KIND_RESULT_PUSHED,
        frame_id=7,
    )
    pushed_result = NativeRuntimeResult._from_polled_event(pushed, operation_id=99)
    assert pushed_result.terminal_state is ResultTerminalState.SUCCESS
    assert pushed_result.event.as_runtime() is pushed

    drop = _decode_test_wire_event(
        MessageType.RESULT_DROP_REASON,
        encode_runtime_control_metadata(
            MessageType.RESULT_DROP_REASON,
            ResultDropReasonMetadata(99, 1, ResultDropReasonCode.BACKPRESSURE, RuntimeRole.SERVER, 0, 0),
        ),
        kind=EVENT_KIND_CONTROL,
        frame_id=8,
    )
    dropped_result = NativeRuntimeResult._from_polled_event(drop, operation_id=99)
    assert dropped_result.terminal_state is ResultTerminalState.DROPPED

    malformed_push = replace(pushed, metadata=RuntimeEventMetadata(RuntimeEventMetadataKind.NONE))
    with pytest.raises(NativeHandleError, match="requires ResultPushMetadata"):
        NativeRuntimeResult._from_polled_event(malformed_push, operation_id=99)
    with pytest.raises(ValueError, match="requires ResultPushMetadata"):
        NativeRuntimeResult(99, ResultTerminalState.SUCCESS, NativeTerminalEvent.runtime(malformed_push))

    progress = _decode_test_wire_event(
        MessageType.PROGRESS,
        encode_runtime_control_metadata(MessageType.PROGRESS, ProgressMetadata(99, 1, 1, 100, 0, 0)),
    )
    with pytest.raises(NativeHandleError, match="not a terminal result event"):
        NativeRuntimeResult._from_polled_event(progress, operation_id=99)
    with pytest.raises(ValueError, match="not terminal result evidence"):
        NativeRuntimeResult(99, ResultTerminalState.SUCCESS, NativeTerminalEvent.runtime(progress))


def test_operation_lifecycle_event_rejects_invalid_or_nonterminal_result_evidence() -> None:
    with pytest.raises(ValueError, match="operation_id"):
        OperationLifecycleEvent(0, OperationState.FAILED)
    with pytest.raises(NativeHandleError, match="not a terminal operation state"):
        native_module._terminal_state_from_operation_state(OperationState.RUNNING)


def test_native_runtime_result_maps_error_and_drop_events() -> None:
    base_event = NativeLifecycleEvent(
        kind=10,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        payload=b"",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus(FFI_STATUS_INTERNAL_ERROR), 12, 41, 99, 7),
    )
    drop_event = NativeLifecycleEvent(
        kind=7,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        payload=b"",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 7),
    )

    failed = NativeRuntimeResult._from_polled_event(base_event)
    cancelled = NativeRuntimeResult._from_polled_event(drop_event)

    assert failed.terminal_state is ResultTerminalState.ERROR
    assert failed.event.as_lifecycle() == OperationLifecycleEvent(99, OperationState.FAILED)
    assert cancelled.terminal_state is ResultTerminalState.CANCELLED
    assert cancelled.event.as_lifecycle() == OperationLifecycleEvent(99, OperationState.CANCELLED)


def test_native_runtime_event_classifies_control_and_credit_updates() -> None:
    flow_metadata = FlowUpdateMetadata()
    flow_event = _decode_test_wire_event(
        MessageType.FLOW_UPDATE,
        flow_metadata.pack(),
        kind=EVENT_KIND_FLOW_UPDATED,
        frame_id=7,
    )
    control_event = NativeLifecycleEvent(
        kind=EVENT_KIND_CONTROL,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        payload=b"opaque-control-state",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 8),
    )

    update = NativeCreditUpdateEvent.from_event(flow_event)

    assert isinstance(update, NativeCreditUpdateEvent)
    assert update.event is flow_event
    assert update.metadata == flow_metadata
    assert control_event.kind_name == "control"
    assert control_event.is_control_event is True
    assert control_event.is_flow_update is False
    with pytest.raises(NativeHandleError, match="expected FLOW_UPDATE event"):
        NativeCreditUpdateEvent.from_event(control_event)


def test_native_runtime_event_wraps_result_hint_metadata() -> None:
    metadata = ResultHintMetadata(
        applied_budget_policy=ResultHintBudgetPolicy.PARTIAL,
        congestion_state=ResultHintCongestionState.ELEVATED,
        reason=ResultHintReason.SERVER_BUSY,
        retry_after_ms=125,
    )
    hint_event = _decode_test_wire_event(
        MessageType.RESULT_HINT,
        metadata.pack(),
        kind=EVENT_KIND_RESULT_HINT,
        frame_id=7,
    )
    control_event = _decode_test_wire_event(
        MessageType.CANCEL,
        encode_runtime_control_metadata(
            MessageType.CANCEL,
            ControlRequestMetadata(99, 1, 0, RuntimeRole.CLIENT, 0, 0),
        ),
        kind=EVENT_KIND_CONTROL,
        frame_id=8,
    )

    hint = NativeResultHintEvent.from_event(hint_event)

    assert hint.event is hint_event
    assert hint.metadata == metadata
    with pytest.raises(NativeHandleError, match="expected RESULT_HINT event"):
        NativeResultHintEvent.from_event(control_event)


def test_native_runtime_session_polls_event_delivery_model(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)

    event = session.poll_event()
    events = session.poll_events(max_events=1)
    async_event = asyncio.run(session.async_poll_event())
    async_events = asyncio.run(_collect_async_events(session))

    assert event is not None
    assert event.payload == b"result"
    assert [polled.payload for polled in events] == [b"result"]
    assert library.nnrp_client_await_events.calls[0][2] == 1
    assert library.nnrp_client_await_events.calls[0][0].timeout_ms == 1
    assert [polled.session.id for polled in session.poll_events_batch(max_events=2)] == [
        session.handle.handle.id,
        session.handle.handle.id,
    ]
    assert session.poll_events_batch(max_events=2, event_kind=EVENT_KIND_CONTROL) == ()
    assert async_event is not None
    assert async_event.payload == b"result"
    assert [polled.payload for polled in async_events] == [b"result"]

    with pytest.raises(ValueError, match="max_events"):
        session.poll_events(max_events=-1)
    with pytest.raises(ValueError, match="max_events"):
        session.poll_events_batch(max_events=-1)
    assert session.poll_events_batch(max_events=0) == ()
    assert library.nnrp_client_await_events.calls[0][0].scope.id == session.handle.handle.id


def test_native_runtime_session_batch_polls_preview4_control_events(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    progress_payload = encode_runtime_control_metadata(
        MessageType.PROGRESS,
        ProgressMetadata(
            operation_id=101,
            progress_sequence=1,
            stage_code=2,
            percent_x100=5000,
            object_id=33,
            body_bytes=8,
        ),
        tail=b"progress",
    )
    partial_payload = encode_runtime_control_metadata(
        MessageType.PARTIAL_RESULT,
        PartialResultMetadata(
            operation_id=101,
            result_sequence=2,
            object_id=33,
            delta_sequence=4,
            body_bytes=7,
            flags=0x01,
        ),
        tail=b"partial",
    )
    drop_payload = encode_runtime_control_metadata(
        MessageType.RESULT_DROP_REASON,
        ResultDropReasonMetadata(
            operation_id=101,
            result_sequence=3,
            drop_reason_code=ResultDropReasonCode.BACKPRESSURE,
            source_role=RuntimeRole.SERVER,
            flags=0,
            diagnostic_bytes=4,
        ),
        tail=b"drop",
    )
    library = BatchControlRuntimeLibrary(
        [
            (EVENT_KIND_CONTROL, progress_payload),
            (EVENT_KIND_CONTROL, partial_payload),
            (EVENT_KIND_CONTROL, drop_payload),
        ]
    )
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)

    events = session.poll_events_batch(max_events=3, event_kind=EVENT_KIND_CONTROL)
    decoded = [
        decode_runtime_control_metadata(message_type, event.payload)
        for message_type, event in zip(
            (MessageType.PROGRESS, MessageType.PARTIAL_RESULT, MessageType.RESULT_DROP_REASON),
            events,
            strict=True,
        )
    ]

    assert [event.kind for event in events] == [EVENT_KIND_CONTROL, EVENT_KIND_CONTROL, EVENT_KIND_CONTROL]
    assert library.nnrp_client_await_events.calls[0][2] == 3
    assert decoded[0].metadata == ProgressMetadata(101, 1, 2, 5000, 33, 8)
    assert decoded[0].tail == b"progress"
    assert decoded[1].metadata == PartialResultMetadata(101, 2, 33, 4, 7, 0x01)
    assert decoded[1].tail == b"partial"
    assert decoded[2].metadata == ResultDropReasonMetadata(
        101,
        3,
        ResultDropReasonCode.BACKPRESSURE,
        RuntimeRole.SERVER,
        0,
        4,
    )
    assert decoded[2].tail == b"drop"


def test_native_runtime_session_preserves_filtered_owned_batch_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = OwnedBatchRuntimeLibrary(
        [
            (EVENT_KIND_RESULT_PUSHED, 41, 99, 7, b"skip"),
            (EVENT_KIND_CONTROL, 41, 100, 8, b"keep"),
        ]
    )
    copied_payloads: list[bytes] = []
    original_copy = native_module._copy_buffer_view

    def track_copy(view: _NnrpBufferView) -> bytes:
        payload = original_copy(view)
        copied_payloads.append(payload)
        return payload

    monkeypatch.setattr(native_module, "_copy_buffer_view", track_copy)
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)

    events = session.poll_events_batch(max_events=2, event_kind=EVENT_KIND_CONTROL)

    assert [event.payload for event in events] == [b"keep"]
    assert session.poll_events_batch(max_events=2, event_kind=EVENT_KIND_CONTROL) == ()
    assert session.poll_event().payload == b"skip"
    assert copied_payloads == [b"skip", b"keep"]
    assert library._buffers == {}
    assert [call[0].id for call in library.nnrp_buffer_release.calls] == [1000, 1001]


def test_native_runtime_session_batch_poll_maps_would_block_to_empty(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)

    assert session.poll_events_batch(max_events=4) == ()
    assert library.nnrp_client_await_events.calls[0][2] == 4


def test_native_runtime_session_poll_events_without_limit_filters_until_empty(
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
    session = _open_event_session(connection)
    control_event = _decode_test_wire_event(
        MessageType.CANCEL,
        encode_runtime_control_metadata(
            MessageType.CANCEL,
            ControlRequestMetadata(99, 1, 0, RuntimeRole.CLIENT, 0, len(b"control")),
            tail=b"control",
        ),
        kind=EVENT_KIND_CONTROL,
        frame_id=7,
    )
    result_event = _decode_test_wire_event(
        MessageType.RESULT_PUSH,
        _native_token_result_payload(),
        kind=EVENT_KIND_RESULT_PUSHED,
        frame_id=8,
    )
    queued_events: list[NativeRuntimeEvent] = [control_event, result_event]

    def poll_event_batch_once(
        self: NativeRuntimeSession,
        *,
        max_events: int,
        event_kind: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativeRuntimeEvent, ...]:
        del self, max_events, event_kind, timeout_ms
        return (queued_events.pop(0),) if queued_events else ()

    monkeypatch.setattr(NativeRuntimeSession, "poll_events_batch", poll_event_batch_once)

    assert session.poll_events(event_kind=EVENT_KIND_RESULT_PUSHED) == (result_event,)


def test_native_runtime_session_filters_credit_update_events(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(
        event_payload=FlowUpdateMetadata().pack(),
        event_kind=EVENT_KIND_FLOW_UPDATED,
        event_message_type=int(MessageType.FLOW_UPDATE),
    )
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)

    updates = session.poll_credit_updates(max_events=1)
    async_updates = asyncio.run(_collect_async_credit_updates(session))
    async_control_events = asyncio.run(_collect_async_events_by_kind(session, EVENT_KIND_CONTROL))

    assert len(updates) == 1
    assert updates[0].event.header.session_id == 41
    assert updates[0].event.header.frame_id == 7
    assert updates[0].metadata == FlowUpdateMetadata()
    assert len(async_updates) == 1
    assert async_updates[0].event.header.session_id == 41
    assert async_control_events == []
    assert not hasattr(updates[0], "credits")


def test_native_runtime_session_filters_result_hint_events(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    metadata = ResultHintMetadata(
        applied_budget_policy=ResultHintBudgetPolicy.PARTIAL,
        congestion_state=ResultHintCongestionState.SATURATED,
        reason=ResultHintReason.BUDGET_EXCEEDED,
        retry_after_ms=250,
    )
    library = FakeRuntimeLibrary(
        event_payload=metadata.pack(),
        event_kind=EVENT_KIND_RESULT_HINT,
        event_message_type=int(MessageType.RESULT_HINT),
    )
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)

    hints = session.poll_result_hints(max_events=1)
    async_hints = asyncio.run(_collect_async_result_hints(session))

    assert len(hints) == 1
    assert hints[0].metadata == metadata
    assert hints[0].event.header.session_id == 41
    assert hints[0].event.header.frame_id == 7
    assert len(async_hints) == 1
    assert async_hints[0].metadata.retry_after_ms == 250


def test_native_runtime_session_wraps_payload_family_events(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(
        event_payload=_native_token_result_payload(b'{"delta":"ok"}'),
        event_kind=EVENT_KIND_RESULT_PUSHED,
        event_message_type=int(MessageType.RESULT_PUSH),
    )
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)

    structured = session.poll_structured_events(max_events=1)
    tool_deltas = session.poll_tool_deltas(max_events=1)
    workflow_states = session.poll_workflow_states(max_events=1)
    async_structured = asyncio.run(_collect_async_structured_events(session))
    async_tool_deltas = asyncio.run(_collect_async_tool_deltas(session))
    async_workflow_states = asyncio.run(_collect_async_workflow_states(session))

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


def test_native_runtime_session_dispatches_callbacks(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    result_library = FakeRuntimeLibrary(
        event_payload=_native_token_result_payload(b'{"delta":"ok"}'),
        event_kind=EVENT_KIND_RESULT_PUSHED,
        event_message_type=int(MessageType.RESULT_PUSH),
    )
    result_connection = load_native_client(artifact, library=result_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result_session = _open_event_session(result_connection)
    credit_library = FakeRuntimeLibrary(
        event_payload=FlowUpdateMetadata().pack(),
        event_kind=EVENT_KIND_FLOW_UPDATED,
        event_message_type=int(MessageType.FLOW_UPDATE),
    )
    credit_connection = load_native_client(artifact, library=credit_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    credit_session = _open_event_session(credit_connection)
    raw_payloads: list[bytes] = []
    structured_payloads: list[bytes] = []
    tool_payloads: list[bytes] = []
    credit_frames: list[int] = []
    hint_retries: list[int] = []

    raw_count = result_session.dispatch_events(
        lambda event: raw_payloads.append(event.tail.body) if isinstance(event, NativeRuntimeEvent) else None,
        max_events=1,
    )
    structured_count = result_session.dispatch_structured_events(
        lambda event: structured_payloads.append(event.payload),
        max_events=1,
    )
    tool_count = result_session.dispatch_tool_deltas(
        lambda event: tool_payloads.append(event.payload),
        max_events=1,
    )
    credit_count = credit_session.dispatch_credit_updates(
        lambda update: credit_frames.append(update.event.header.frame_id),
        max_events=1,
    )
    hint_library = FakeRuntimeLibrary(
        event_payload=ResultHintMetadata(retry_after_ms=75).pack(),
        event_kind=EVENT_KIND_RESULT_HINT,
        event_message_type=int(MessageType.RESULT_HINT),
    )
    hint_connection = load_native_client(artifact, library=hint_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    hint_session = _open_event_session(hint_connection)
    hint_count = hint_session.dispatch_result_hints(
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


def test_native_runtime_session_dispatches_payload_family_callbacks_by_event_kind(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    result_library = FakeRuntimeLibrary(
        event_payload=_native_token_result_payload(b'{"result":true}'),
        event_kind=EVENT_KIND_RESULT_PUSHED,
        event_message_type=int(MessageType.RESULT_PUSH),
    )
    result_connection = load_native_client(artifact, library=result_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result_session = _open_event_session(result_connection)
    control_library = FakeRuntimeLibrary(
        event_payload=encode_runtime_control_metadata(
            MessageType.CANCEL,
            ControlRequestMetadata(99, 1, 0, RuntimeRole.CLIENT, 0, len(b'{"control":true}')),
            tail=b'{"control":true}',
        ),
        event_kind=EVENT_KIND_CONTROL,
        event_message_type=int(MessageType.CANCEL),
    )
    control_connection = load_native_client(artifact, library=control_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    control_session = _open_event_session(control_connection)
    structured_events: list[tuple[str, bytes, int]] = []
    tool_deltas: list[tuple[str, bytes, int]] = []

    structured_count = control_session.dispatch_payload_family_events(
        "structured_event",
        lambda event: structured_events.append((event.payload_family, event.payload, event.event.header.message_type)),
        max_events=1,
        event_kind=EVENT_KIND_CONTROL,
    )
    tool_count = result_session.dispatch_payload_family_events(
        "tool_delta",
        lambda event: tool_deltas.append((event.payload_family, event.payload, event.event.header.message_type)),
        max_events=1,
        event_kind=EVENT_KIND_RESULT_PUSHED,
    )

    assert structured_count == 1
    assert tool_count == 1
    assert structured_events == [("structured_event", b'{"control":true}', MessageType.CANCEL)]
    assert tool_deltas == [("tool_delta", b'{"result":true}', MessageType.RESULT_PUSH)]


def test_native_runtime_session_maps_callback_rejection(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(
        event_payload=_native_token_result_payload(b"payload"),
        event_kind=EVENT_KIND_RESULT_PUSHED,
        event_message_type=int(MessageType.RESULT_PUSH),
    )
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    session = _open_event_session(connection)

    def reject(_event: NativePayloadFamilyEvent) -> None:
        raise ValueError("host rejected payload")

    with pytest.raises(NativeCallbackRejectedError) as captured:
        session.dispatch_tool_deltas(reject, max_events=1)

    assert captured.value.status.status_code == FFI_STATUS_CALLBACK_REJECTED
    assert isinstance(captured.value.__cause__, ValueError)


def test_native_payload_family_event_rejects_unknown_family_and_non_payload_event(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    result_library = FakeRuntimeLibrary(
        event_payload=_native_token_result_payload(b"payload"),
        event_kind=EVENT_KIND_RESULT_PUSHED,
        event_message_type=int(MessageType.RESULT_PUSH),
    )
    result_connection = load_native_client(artifact, library=result_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result_session = _open_event_session(result_connection)

    with pytest.raises(NativeHandleError, match="unknown native payload family"):
        result_session.poll_payload_family_events("private_family", max_events=1)

    flow_library = FakeRuntimeLibrary(
        event_payload=FlowUpdateMetadata().pack(),
        event_kind=EVENT_KIND_FLOW_UPDATED,
        event_message_type=int(MessageType.FLOW_UPDATE),
    )
    flow_connection = load_native_client(artifact, library=flow_library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    flow_session = _open_event_session(flow_connection)

    with pytest.raises(NativeHandleError, match="expected native result/control event"):
        flow_session.poll_payload_family_events(
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
    session = _open_event_session(connection)

    asyncio.run(_cancel_async_credit_updates(session))


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
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.close()
    assert not hasattr(connection, "control")
    assert not hasattr(connection, "poll_event")
    assert not hasattr(connection, "poll_runtime_frames")


def test_native_runtime_session_submits_and_polls_result(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(
        event_payload=_native_token_result_payload(),
        event_message_type=int(MessageType.RESULT_PUSH),
    )
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    request = _native_submit_request()
    result = session.submit_and_poll_result(
        request,
        max_events=1,
        timeout_ms=25,
    )
    async_result = asyncio.run(
        session.async_submit_and_poll_result(
            request,
            max_events=1,
        )
    )

    assert result.terminal_state is ResultTerminalState.SUCCESS
    assert result.operation_id == 99
    runtime_event = result.event.as_runtime()
    assert runtime_event is not None
    assert runtime_event.header.frame_id == 7
    assert isinstance(runtime_event.metadata.value, ResultPushMetadata)
    assert runtime_event.tail.body == b"result"
    assert async_result.terminal_state is ResultTerminalState.SUCCESS
    assert async_result.event.as_runtime().tail.body == b"result"
    assert [_read_buffer_view(call[0].payload) for call in library.nnrp_client_submit.calls] == [
        request.metadata.pack() + request.body,
        request.metadata.pack() + request.body,
    ]
    assert library.nnrp_client_await_events.calls[0][0].timeout_ms == 25
    assert library.nnrp_client_await_events.calls[1][0].timeout_ms == 1
    assert len(library.nnrp_client_await_events.calls) == 2


def test_native_runtime_session_polls_result_with_batch_when_event_budget_allows(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=_native_token_result_payload())
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(_native_submit_request(body=b""))
    result = session.poll_result(operation, max_events=2)
    second_result = session.poll_result(operation, max_events=2)

    assert result.event.as_lifecycle() == OperationLifecycleEvent(99, OperationState.COMPLETED)
    assert second_result.event.as_lifecycle() == OperationLifecycleEvent(99, OperationState.COMPLETED)
    assert library.nnrp_client_await_events.calls[0][2] == 2
    assert library.nnrp_client_await_event.calls == []


def test_native_runtime_session_submit_result_reports_would_block_when_no_event(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    with pytest.raises(NativeWouldBlockError):
        session.submit_and_poll_result(_native_submit_request(), max_events=1)

    with pytest.raises(ValueError, match="max_events"):
        session.submit_and_poll_result(_native_submit_request(body=b""), max_events=-1)


def test_native_runtime_session_submit_result_preserves_related_diagnostic_ids(tmp_path: Path) -> None:
    class RelatedDiagnosticRuntimeLibrary(FakeRuntimeLibrary):
        def _await_events(
            self,
            request: _NnrpRoleEventPollRequest,
            out_events: object,
            event_capacity: int,
            out_event_count: object,
        ) -> _NnrpFfiStatus:
            status = super()._await_events(request, out_events, event_capacity, out_event_count)
            event = ctypes.cast(out_events, ctypes.POINTER(_NnrpEvent))[0]
            event.diagnostic.related_connection_id = 12
            event.diagnostic.related_session_id = 41
            event.diagnostic.related_operation_id = 99
            event.diagnostic.related_frame_id = 7
            return status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = RelatedDiagnosticRuntimeLibrary(event_payload=_native_token_result_payload())
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    result = session.submit_and_poll_result(_native_submit_request())

    assert result.operation_id == 99
    assert result.event.as_lifecycle() == OperationLifecycleEvent(99, OperationState.COMPLETED)
    assert not hasattr(result, "diagnostic")


def test_native_runtime_session_submit_and_poll_maps_non_ok_statuses(tmp_path: Path) -> None:
    class SubmitFailureRuntimeLibrary(FakeRuntimeLibrary):
        def _submit(
            self,
            request: _NnrpSubmitRequest,
            out_handle: object,
        ) -> _NnrpFfiStatus:
            return _NnrpFfiStatus(FFI_STATUS_INTERNAL_ERROR, 0, 0, 0)

    class PollFailureRuntimeLibrary(FakeRuntimeLibrary):
        def _await_events(
            self,
            request: _NnrpRoleEventPollRequest,
            out_events: object,
            event_capacity: int,
            out_event_count: object,
        ) -> _NnrpFfiStatus:
            return _NnrpFfiStatus(FFI_STATUS_INTERNAL_ERROR, 0, 0, 0)

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    for library in (
        SubmitFailureRuntimeLibrary(),
        PollFailureRuntimeLibrary(event_payload=_native_token_result_payload()),
    ):
        session = (
            load_native_client(artifact, library=library)
            .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
            .open_session(
                requested_session_id=41,
                profile_id=4,
                schema_id=5,
                schema_version=6,
            )
        )

        with pytest.raises(NativeInternalError):
            session.submit_and_poll_result(_native_submit_request())


def test_native_runtime_session_batch_poll_reports_would_block_when_no_result(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(_native_submit_request(body=b""))

    with pytest.raises(NativeWouldBlockError):
        session.poll_result(operation, max_events=2)


def test_native_runtime_session_batch_poll_skips_mismatched_events(tmp_path: Path) -> None:
    class MismatchedRuntimeLibrary(FakeRuntimeLibrary):
        def _await_events(
            self,
            request: _NnrpRoleEventPollRequest,
            out_events: object,
            event_capacity: int,
            out_event_count: object,
        ) -> _NnrpFfiStatus:
            count_target = getattr(out_event_count, "_obj", None)
            if count_target is None:
                count_target = ctypes.cast(out_event_count, ctypes.POINTER(ctypes.c_size_t)).contents
            events = ctypes.cast(out_events, ctypes.POINTER(_NnrpEvent))
            events[0].kind = EVENT_KIND_RESULT_PUSHED
            events[0].connection = request.scope
            events[0].session = _NnrpHandle(HANDLE_KIND_SESSION, 42, 3, 0)
            events[0].operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99, 1, 0)
            _write_event_header(events[0], message_type=0, frame_id=7)
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
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(_native_submit_request(body=b""))

    with pytest.raises(NativeWouldBlockError):
        session.poll_result(operation, max_events=2)


def test_native_runtime_session_batch_poll_skips_submit_accepted_events(tmp_path: Path) -> None:
    class MixedEventRuntimeLibrary(FakeRuntimeLibrary):
        def _await_events(
            self,
            request: _NnrpRoleEventPollRequest,
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
                events[index].connection = request.scope
                events[index].session = _NnrpHandle(
                    HANDLE_KIND_SESSION,
                    request.scope.id,
                    request.scope.generation,
                    0,
                )
                events[index].operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99, 1, 0)
                _write_event_header(events[index], message_type=0, frame_id=7)
                events[index].payload = _NnrpBufferView(
                    ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                    len(self._event_payload_owner.raw),
                )
                events[index].diagnostic.status = NativeStatus.ok().to_ffi()
                events[index].diagnostic.related_operation_id = 99
                events[index].diagnostic.related_frame_id = 7
            count_target.value = 2
            return self.status

    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = MixedEventRuntimeLibrary(event_payload=_native_token_result_payload())
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(_native_submit_request(body=b""))
    result = session.poll_result(operation, max_events=2)

    assert result.terminal_state is ResultTerminalState.SUCCESS
    assert result.event.as_lifecycle() == OperationLifecycleEvent(99, OperationState.COMPLETED)


def test_native_runtime_session_batch_poll_preserves_unmatched_owned_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = OwnedBatchRuntimeLibrary(
        [
            (EVENT_KIND_RESULT_PUSHED, 42, 99, 7, _native_token_result_payload(b"wrong-session")),
            (EVENT_KIND_SUBMIT_ACCEPTED, 41, 99, 7, b"not-result"),
            (EVENT_KIND_RESULT_PUSHED, 41, 99, 7, _native_token_result_payload()),
            (EVENT_KIND_RESULT_PUSHED, 41, 100, 8, _native_token_result_payload(b"trailing")),
        ]
    )
    copied_payloads: list[bytes] = []
    original_copy = native_module._copy_buffer_view

    def track_copy(view: _NnrpBufferView) -> bytes:
        payload = original_copy(view)
        copied_payloads.append(payload)
        return payload

    monkeypatch.setattr(native_module, "_copy_buffer_view", track_copy)
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    operation = session.submit_operation(_native_submit_request(body=b""))

    result = session.poll_result(operation, max_events=4)

    assert result.event.as_lifecycle() == OperationLifecycleEvent(99, OperationState.COMPLETED)
    assert copied_payloads == [
        _native_token_result_payload(b"wrong-session"),
        b"not-result",
        _native_token_result_payload(),
        _native_token_result_payload(b"trailing"),
    ]
    assert library._buffers == {}
    assert [call[0].id for call in library.nnrp_buffer_release.calls] == [1000, 1001, 1002, 1003]


def test_native_runtime_session_accepts_read_only_memoryview_payloads(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    request = _native_submit_request(body=memoryview(b"payload"))
    session.submit_operation(request)

    assert library.submitted_payloads[-1] == request.metadata.pack() + request.body


def test_native_runtime_session_raises_when_result_is_not_available(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    with pytest.raises(NativeWouldBlockError):
        session.submit_and_poll_result(
            _native_submit_request(),
            parent_operation_id=1,
            max_events=1,
        )

    with pytest.raises(ValueError, match="max_events"):
        session.poll_result(session.submit_operation(_native_submit_request(body=b"")), max_events=-1)


def test_native_runtime_session_rejects_use_after_close(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    operation = session.submit_operation(_native_submit_request(body=b""))

    session.close()

    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.submit(_native_submit_request(100, 8, b""))
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.poll_result(operation, max_events=1)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.cancel(frame_id=7)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.cancel_operation(ControlRequestMetadata(99, 1, 0, RuntimeRole.CLIENT, 0, 0))
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


async def _collect_async_events(
    session: NativeRuntimeSession,
) -> list[NativeRuntimeEvent | NativeLifecycleEvent]:
    return [event async for event in session.iter_events(max_events=1)]


async def _collect_async_events_by_kind(
    session: NativeRuntimeSession,
    event_kind: int,
) -> list[NativeRuntimeEvent | NativeLifecycleEvent]:
    return [event async for event in session.iter_events(max_events=1, event_kind=event_kind)]


async def _collect_async_credit_updates(session: NativeRuntimeSession) -> list[NativeCreditUpdateEvent]:
    return [event async for event in session.iter_credit_updates(max_events=1)]


async def _collect_async_result_hints(session: NativeRuntimeSession) -> list[NativeResultHintEvent]:
    return [event async for event in session.iter_result_hints(max_events=1)]


async def _collect_async_structured_events(session: NativeRuntimeSession) -> list[NativePayloadFamilyEvent]:
    return [event async for event in session.iter_structured_events(max_events=1)]


async def _collect_async_tool_deltas(session: NativeRuntimeSession) -> list[NativePayloadFamilyEvent]:
    return [event async for event in session.iter_tool_deltas(max_events=1)]


async def _collect_async_workflow_states(session: NativeRuntimeSession) -> list[NativePayloadFamilyEvent]:
    return [event async for event in session.iter_workflow_states(max_events=1)]


async def _cancel_async_credit_updates(session: NativeRuntimeSession) -> None:
    task = asyncio.create_task(_collect_async_credit_updates(session))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def _cancel_async_submit(session: NativeRuntimeSession) -> None:
    task = asyncio.create_task(session.async_submit_operation(_native_submit_request(101, 9)))
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
        (NativeObjectDescriptorHandle, HANDLE_KIND_OBJECT_DESCRIPTOR),
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
    assert library.nnrp_session_close.argtypes == [_NnrpHandle]
    assert library.nnrp_client_close.argtypes == [_NnrpHandle]
    assert library.nnrp_client_close_connection.argtypes == [_NnrpHandle]
    assert library.nnrp_client_cancel.argtypes == [_NnrpClientCancelRequest]
    assert library.nnrp_client_await_event.argtypes == [
        _NnrpHandle,
        ctypes.POINTER(_NnrpPollResult),
    ]
    assert library.nnrp_client_await_events.argtypes == [
        _NnrpRoleEventPollRequest,
        ctypes.POINTER(_NnrpEvent),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    assert library.nnrp_server_bind.argtypes == [_NnrpServerBindRequest, ctypes.POINTER(_NnrpHandle)]
    assert library.nnrp_server_accept_begin.argtypes == [
        _NnrpServerAcceptBeginRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_server_accept_wait.argtypes == [_NnrpServerAcceptWaitRequest]
    assert library.nnrp_server_accept_claim.argtypes == [
        _NnrpServerAcceptClaimRequest,
        ctypes.POINTER(_NnrpServerAcceptResult),
    ]
    assert library.nnrp_server_accept_release.argtypes == [_NnrpHandle]
    assert library.nnrp_server_await_events.argtypes == [
        _NnrpRoleEventPollRequest,
        ctypes.POINTER(_NnrpEvent),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    assert library.nnrp_server_send_result.argtypes == [_NnrpServerSendResultRequest]
    assert library.nnrp_server_close.argtypes == [_NnrpHandle]
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
    assert library.nnrp_object_metadata_buffer_acquire_copy.argtypes == [
        _NnrpBufferView,
        ctypes.POINTER(_NnrpHandle),
        ctypes.POINTER(_NnrpBufferView),
    ]
    assert library.nnrp_object_metadata_buffer_view.argtypes == [_NnrpHandle, ctypes.POINTER(_NnrpBufferView)]
    assert library.nnrp_object_metadata_buffer_release.argtypes == [_NnrpHandle]
    assert library.nnrp_object_descriptor_create.argtypes == [
        _NnrpRuntimeObjectDescriptor,
        _NnrpBufferView,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_object_descriptor_view.argtypes == [
        _NnrpHandle,
        ctypes.POINTER(_NnrpRuntimeObjectDescriptor),
        ctypes.POINTER(_NnrpBufferView),
    ]
    assert library.nnrp_object_descriptor_metadata_snapshot.argtypes == [
        _NnrpHandle,
        ctypes.POINTER(_NnrpHandle),
        ctypes.POINTER(_NnrpBufferView),
    ]
    assert library.nnrp_object_descriptor_release.argtypes == [_NnrpHandle]
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
    assert library.nnrp_runtime_frame_send.argtypes == [_NnrpRuntimeFrameSendRequest]
    assert library.nnrp_poll_empty.argtypes == [ctypes.POINTER(_NnrpPollResult)]
    assert library.nnrp_dispatch_event.argtypes == [_NnrpCallbackSink, ctypes.POINTER(_NnrpEvent)]

    for symbol in RUNTIME_ENTRYPOINT_SYMBOLS[2:]:
        assert getattr(library, symbol).restype is _NnrpFfiStatus


def test_native_runtime_entrypoints_reject_missing_symbol() -> None:
    library = FakeEntrypointLibrary(missing_symbol="nnrp_submit")

    with pytest.raises(NativeArtifactError, match="missing nnrp_submit"):
        NativeRuntimeEntrypoints(library)
