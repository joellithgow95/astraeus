"""S3-compatible storage backend using obstore.

Supports AWS S3, Cloudflare R2 (via S3-compatible endpoint), and Google Cloud Storage.
See ADR 011 for the rationale for choosing obstore over boto3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from astraeus_media.config import MediakitConfig

if TYPE_CHECKING:
    import obstore.store as obs

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class S3CompatibleBackend:
    """obstore-based storage backend.

    Covers AWS S3 and any S3-compatible endpoint (Cloudflare R2, MinIO, etc.)
    when ``config.endpoint_url`` is set, and GCS when ``config.provider == "gcs"``.

    Implements the ``StorageBackend`` protocol defined in ``astraeus_media.storage.backend``.
    """

    def __init__(self, config: MediakitConfig) -> None:
        self.config = config
        self._store: obs.S3Store | obs.GCSStore | None = None

    def _get_store(self) -> obs.S3Store | obs.GCSStore:
        if self._store is None:
            import obstore.store as obs_store

            if self.config.provider == "gcs":
                self._store = obs_store.GCSStore.from_url(
                    f"gs://{self.config.bucket}/",
                )
            else:
                # AWS S3 or any S3-compatible endpoint (R2, MinIO, etc.)
                store_config: dict[str, str] = {}
                if self.config.region_name:
                    store_config["AWS_REGION"] = self.config.region_name
                if self.config.endpoint_url:
                    store_config["AWS_ENDPOINT"] = self.config.endpoint_url
                if self.config.aws_access_key_id:
                    store_config["AWS_ACCESS_KEY_ID"] = self.config.aws_access_key_id
                if self.config.aws_secret_access_key:
                    store_config["AWS_SECRET_ACCESS_KEY"] = self.config.aws_secret_access_key
                self._store = obs_store.S3Store.from_url(
                    f"s3://{self.config.bucket}/",
                    config=store_config,
                )
        return self._store

    async def prepare_upload(self, key: str, content_type: str, expires_in: int = 900) -> dict:
        """Return a presigned PUT URL for direct browser-to-bucket upload.

        Returns ``{ "upload_url": str, "key": str, "expires_at": str }``.
        The browser PUTs directly to ``upload_url`` — astraeus-media never sees the bytes.
        """
        from datetime import UTC, datetime, timedelta

        import obstore

        with tracer.start_as_current_span("astraeus_media.storage.prepare_upload") as span:
            span.set_attribute("key", key)
            try:
                store = self._get_store()
                url = await obstore.sign_async(store, "PUT", key, timedelta(seconds=expires_in))
                expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
                return {"upload_url": url, "key": key, "expires_at": expires_at}
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

    async def confirm_exists(self, key: str) -> bool:
        """Return True if the object exists in the bucket."""
        import obstore

        with tracer.start_as_current_span("astraeus_media.storage.confirm_exists") as span:
            span.set_attribute("key", key)
            try:
                await obstore.head_async(self._get_store(), key)
                return True
            except Exception:
                logger.debug("astraeus_media.storage.object_not_found", key=key)
                return False

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a presigned GET URL (or public URL if bucket is public-read).

        Used by the IIIF endpoint to issue a 302 redirect — the response bytes
        never flow through the application server.
        """
        if self.config.public_read:
            # Construct public URL — no signing needed
            base = self.config.endpoint_url or f"https://{self.config.bucket}.s3.amazonaws.com"
            return f"{base.rstrip('/')}/{key}"

        from datetime import timedelta

        import obstore

        return await obstore.sign_async(
            self._get_store(), "GET", key, timedelta(seconds=expires_in)
        )

    async def delete(self, key: str) -> None:
        """Delete the object from the bucket."""
        import obstore

        await obstore.delete_async(self._get_store(), [key])

    async def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        """List objects under the given prefix.

        Returns ``[{ "key": str, "size": int, "last_modified": str }, ...]``.
        Used by ``astraeus-media sync`` and ``astraeus-media gc``.
        """
        import obstore

        results: list[dict] = []
        async for batch in obstore.list(self._get_store(), prefix=prefix or None):
            for obj in batch:
                results.append(
                    {
                        "key": obj["path"],
                        "size": obj.get("size", 0),
                        "last_modified": str(obj.get("last_modified", "")),
                    }
                )
                if len(results) >= max_keys:
                    return results
        return results
