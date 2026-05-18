"""Server-facing NNRP helpers."""

from dataclasses import dataclass


@dataclass(slots=True)
class ServerProfile:
    max_concurrent_frames: int = 1
    enable_cache: bool = True
    max_sections: int = 16
    max_body_bytes: int = 32 * 1024 * 1024
