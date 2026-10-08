from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from mimetypes import guess_type
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.persistence.object_storage import build_minio_client

_REPORT_OBJECT_PREFIX = "reports"


@dataclass(slots=True)
class ReportExportUploadResult:
    bucket: str
    object_key: str
    filename: str
    mime_type: str
    etag: str | None = None
    version_id: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ReportExportFileArtifact:
    name: str
    url: str
    bucket: str
    object_key: str
    mime_type: str
    size_bytes: int | None = None
    etag: str | None = None
    version_id: str | None = None
    expires_at: datetime | None = None
    file_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


def build_object_key(export_id: str, filename: str, generated_at: str | datetime | None = None) -> str:
    generated = _coerce_datetime(generated_at) if generated_at is not None else datetime.now(timezone.utc)
    return "/".join(
        [
            _REPORT_OBJECT_PREFIX,
            generated.strftime("%Y"),
            generated.strftime("%m"),
            generated.strftime("%d"),
            export_id,
            filename,
        ]
    )


class ReportExportMinioService:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or Settings()
        self.client = client or build_minio_client(self.settings)

    def upload(
        self,
        *,
        file_path: Path | str,
        export_id: str,
        generated_at: str | datetime | None = None,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ReportExportUploadResult:
        path = Path(file_path)
        self._ensure_bucket()
        object_key = build_object_key(export_id=export_id, filename=path.name, generated_at=generated_at)
        resolved_content_type = content_type or _guess_content_type(path)
        upload_result = self.client.fput_object(
            self.settings.minio_bucket,
            object_key,
            str(path),
            content_type=resolved_content_type,
            metadata=dict(metadata or {}),
        )
        return ReportExportUploadResult(
            bucket=self.settings.minio_bucket,
            object_key=object_key,
            filename=path.name,
            mime_type=resolved_content_type,
            etag=getattr(upload_result, "etag", None),
            version_id=getattr(upload_result, "version_id", None),
            size_bytes=path.stat().st_size,
            metadata=dict(metadata or {}),
        )

    def _ensure_bucket(self) -> None:
        bucket_exists = getattr(self.client, "bucket_exists", None)
        make_bucket = getattr(self.client, "make_bucket", None)
        if not callable(bucket_exists) or not callable(make_bucket):
            return
        if bucket_exists(self.settings.minio_bucket):
            return
        make_bucket(self.settings.minio_bucket)

    def presign(self, upload_result: ReportExportUploadResult) -> ReportExportFileArtifact:
        expires = timedelta(seconds=self.settings.minio_presign_expiry_seconds)
        # 下载入口交给业务后端代理，避免把 MinIO 内网/白名单地址暴露给浏览器。
        url = build_backend_download_url(
            bucket=upload_result.bucket,
            object_key=upload_result.object_key,
            filename=upload_result.filename,
            mime_type=upload_result.mime_type,
        )
        expires_at = datetime.now(timezone.utc) + expires
        return ReportExportFileArtifact(
            name=upload_result.filename,
            url=url,
            bucket=upload_result.bucket,
            object_key=upload_result.object_key,
            mime_type=upload_result.mime_type,
            size_bytes=upload_result.size_bytes,
            etag=upload_result.etag,
            version_id=upload_result.version_id,
            expires_at=expires_at,
            file_id=upload_result.object_key,
            metadata=dict(upload_result.metadata),
        )

    def get_object(self, *, bucket: str, object_key: str) -> Any:
        return self.client.get_object(bucket, object_key)

    def stat_object(self, *, bucket: str, object_key: str) -> Any:
        return self.client.stat_object(bucket, object_key)


def build_backend_download_url(*, bucket: str, object_key: str, filename: str, mime_type: str) -> str:
    query = urlencode(
        {
            "bucket": bucket,
            "object_key": object_key,
            "filename": filename,
            "mime_type": mime_type,
        }
    )
    return f"/api/storage/files/download?{query}"


def _guess_content_type(path: Path) -> str:
    mime_type, _ = guess_type(path.name)
    return mime_type or "application/octet-stream"


def _coerce_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        cleaned = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
