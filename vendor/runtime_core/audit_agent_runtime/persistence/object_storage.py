from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from audit_agent_runtime.config.settings import Settings


def build_minio_client(settings: Settings) -> Any:
    try:
        from minio import Minio
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("minio is required for object storage") from exc

    endpoint, secure = normalize_minio_endpoint(
        settings.minio_endpoint,
        settings.minio_secure,
    )
    return Minio(
        endpoint=endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=secure,
    )


def normalize_minio_endpoint(endpoint: str, secure: bool) -> tuple[str, bool]:
    raw = endpoint.strip()
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"unsupported MinIO endpoint scheme: {parsed.scheme}")
        normalized_endpoint = parsed.netloc or parsed.path
        if not normalized_endpoint:
            raise ValueError(f"invalid MinIO endpoint: {endpoint!r}")
        derived_secure = parsed.scheme == "https"
        if secure != derived_secure:
            raise ValueError(
                "Settings.minio_endpoint and minio_secure are inconsistent: "
                f"endpoint={endpoint!r} implies secure={derived_secure} but secure={secure}"
            )
        return normalized_endpoint, derived_secure
    if not raw:
        raise ValueError("MinIO endpoint cannot be empty")
    return raw, secure

