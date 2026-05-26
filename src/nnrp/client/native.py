"""Client-facing native runtime session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nnrp.native import (
    NativePlatform,
    NativeRuntimeBackend,
    NativeRuntimeSession,
    select_native_runtime_backend,
)


@dataclass(frozen=True, slots=True)
class NativeClientSessionOptions:
    connection_id: int = 1
    connection_generation: int = 1
    transport_id: int = 1
    requested_session_id: int = 1
    session_generation: int = 1
    profile_id: int = 0
    schema_id: int = 0
    schema_version: int = 0


def select_client_native_backend(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
) -> NativeRuntimeBackend:
    return select_native_runtime_backend(
        artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
        fallback=fallback,
        require_native=require_native,
    )


@contextmanager
def connect_native_client_session(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    backend: NativeRuntimeBackend | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
    options: NativeClientSessionOptions | None = None,
) -> Iterator[NativeRuntimeSession]:
    resolved_options = options or NativeClientSessionOptions()
    resolved_backend = backend or select_client_native_backend(
        artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
        fallback=fallback,
        require_native=require_native,
    )
    connection = resolved_backend.connect(
        connection_id=resolved_options.connection_id,
        generation=resolved_options.connection_generation,
        transport_id=resolved_options.transport_id,
    )
    session = connection.open_session(
        requested_session_id=resolved_options.requested_session_id,
        generation=resolved_options.session_generation,
        profile_id=resolved_options.profile_id,
        schema_id=resolved_options.schema_id,
        schema_version=resolved_options.schema_version,
    )
    try:
        yield session
    finally:
        if not getattr(session, "_closed", False):
            session.close()
