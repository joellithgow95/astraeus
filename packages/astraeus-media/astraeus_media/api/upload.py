"""Upload flow routes — presigned PUT preparation and post-upload confirmation.

Two-step flow:

1. Client POSTs ``/upload/prepare`` → receives a presigned PUT URL.
2. Client PUTs the file directly to the bucket (no bytes through this server).
3. Client POSTs ``/upload/confirm`` → astraeus-media verifies the object exists, reads
   its type and dimensions, inserts it into the catalog, returns asset metadata.
   The original is left untouched (``keep_originals``, the default); with it off,
   astraeus-media first normalises the file (EXIF strip, WebP conversion, dimension cap)
   and replaces the original in storage.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import obstore
import structlog
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from PIL import Image
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from astraeus_media.processing import run_pipeline
from astraeus_media.routes.iiif import prerender

if TYPE_CHECKING:
    from astraeus_media.app import MediaKit

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

# EXIF Orientation values that turn the image 90 degrees, so width and height swap.
_EXIF_ORIENTATION_TAG = 0x0112
_QUARTER_TURN_ORIENTATIONS = {5, 6, 7, 8}


@dataclass
class StoredImage:
    """What astraeus-media read from an object in the bucket after upload."""

    content_type: str
    height: int
    size: int
    width: int


async def _download_to(store, key: str, path: Path) -> int:
    """Stream *key* to *path* chunk by chunk and return its size in bytes."""
    result = await obstore.get_async(store, key)
    size = 0
    with path.open("wb") as out:
        async for chunk in result:
            out.write(chunk)
            size += len(chunk)
    return size


async def _inspect_original(
    mk: MediaKit, key: str, filename: str, content_type: str
) -> StoredImage:
    """
    Read an object's type and dimensions without changing it.

    The object is streamed to a temp file, so a large original never sits whole
    in memory, and Pillow reads only the header. Dimensions are the *displayed*
    ones: an EXIF quarter-turn swaps them, matching what IIIF renders.
    """
    store = mk.storage._get_store()
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"upload{Path(filename).suffix or '.bin'}"
        size = await _download_to(store, key, path)
        with Image.open(path) as image:
            width, height = image.size
            if image.getexif().get(_EXIF_ORIENTATION_TAG) in _QUARTER_TURN_ORIENTATIONS:
                width, height = height, width
            detected = Image.MIME.get(image.format or "", content_type)
    return StoredImage(content_type=detected, height=height, size=size, width=width)


async def _normalize_in_place(mk: MediaKit, key: str, filename: str) -> StoredImage:
    """Process the upload (EXIF strip, cap, convert) and replace the original."""
    store = mk.storage._get_store()
    with tempfile.TemporaryDirectory() as tmpdir:
        # Keep the original file extension so Pillow can infer format
        source_path = Path(tmpdir) / f"upload{Path(filename).suffix or '.bin'}"
        await _download_to(store, key, source_path)

        proc = await run_pipeline(source_path, mk.config)
        await obstore.put_async(store, key, proc.path.read_bytes())

    return StoredImage(
        content_type=proc.content_type, height=proc.height, size=proc.size, width=proc.width
    )


def _make_key(filename: str, content_type: str, timestamp: str) -> str:
    """Generate a content-addressed storage key.

    ``originals/{sha256_prefix8}/{filename}``

    The prefix is the first 8 hex chars of SHA-256(filename + content_type + timestamp).
    This is deterministic enough for deduplication without needing the actual bytes.
    """
    raw = filename + content_type + timestamp
    prefix = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"originals/{prefix}/{filename}"


def make_upload_routes(mk: MediaKit) -> list[Route]:
    """Return the upload route list for *mk*."""

    async def prepare(request: Request) -> JSONResponse:
        """``POST /upload/prepare`` — generate a presigned PUT URL."""
        from astraeus_media.auth import require_auth

        if (err := await require_auth(request, mk)) is not None:
            return err

        try:
            body = await request.json()
        except Exception:
            logger.warning("astraeus_media.upload.invalid_json", endpoint="prepare")
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        filename = body.get("filename")
        content_type = body.get("content_type")
        size = body.get("size")

        if not filename or not content_type or size is None:
            return JSONResponse(
                {"error": "filename, content_type, and size are required"},
                status_code=422,
            )

        from datetime import UTC, datetime

        timestamp = datetime.now(UTC).isoformat()
        key = _make_key(str(filename), str(content_type), timestamp)

        result = await mk.storage.prepare_upload(key, str(content_type), mk.config.upload_expires)
        return JSONResponse(result)

    async def confirm(request: Request) -> JSONResponse:
        """``POST /upload/confirm`` — verify upload, insert into catalog, return asset."""
        from astraeus_media.auth import require_auth

        if (err := await require_auth(request, mk)) is not None:
            return err

        try:
            body = await request.json()
        except Exception:
            logger.warning("astraeus_media.upload.invalid_json", endpoint="confirm")
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        key = body.get("key")
        filename = body.get("filename")
        content_type = body.get("content_type")
        size = body.get("size")

        if not key or not filename or not content_type or size is None:
            return JSONResponse(
                {"error": "key, filename, content_type, and size are required"},
                status_code=422,
            )

        with tracer.start_as_current_span("astraeus_media.upload.confirm") as span:
            span.set_attribute("key", str(key))
            try:
                exists = await mk.storage.confirm_exists(str(key))
                if not exists:
                    return JSONResponse(
                        {"error": "Object not found in bucket — upload may not have completed"},
                        status_code=404,
                    )

                # Client-supplied values stand when the object can't be read as an
                # image (a PDF, say) or the backend can't be read from.
                final_content_type = str(content_type)
                final_size = int(size)
                final_width = body.get("width")
                final_height = body.get("height")

                try:
                    if mk.config.keep_originals:
                        stored = await _inspect_original(
                            mk, str(key), str(filename), final_content_type
                        )
                    else:
                        stored = await _normalize_in_place(mk, str(key), str(filename))

                    final_content_type = stored.content_type
                    final_size = stored.size
                    final_width = stored.width
                    final_height = stored.height

                except Exception:
                    # Reading is best-effort: fall back to client-supplied metadata.
                    logger.warning(
                        "astraeus_media.upload.pipeline_failed",
                        key=key,
                        filename=filename,
                        content_type=content_type,
                        exc_info=True,
                    )

                # Use a hash of the key as a stable content_hash (no bytes available
                # in the base case; real deduplication is a Phase 9+ concern).
                content_hash = hashlib.sha256(str(key).encode()).hexdigest()[:16]

                asset = await mk.catalog.insert_asset(
                    key=str(key),
                    content_hash=content_hash,
                    bucket=mk.config.bucket,
                    filename=str(filename),
                    content_type=final_content_type,
                    size=final_size,
                    width=final_width,
                    height=final_height,
                )
                # Render the admin's sizes after the response, so the first view is warm.
                return JSONResponse(
                    asset, status_code=201, background=BackgroundTask(prerender, mk, str(key))
                )
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

    return [
        Route("/upload/prepare", endpoint=prepare, methods=["POST"]),
        Route("/upload/confirm", endpoint=confirm, methods=["POST"]),
    ]
