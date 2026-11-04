"""Image processing pipeline — EXIF strip, dimension cap, WebP conversion.

All steps are conditional on :class:`~astraeus_media.config.MediakitConfig` flags so
callers can tune or disable individual steps without forking the pipeline.

Usage::

    from astraeus_media.processing import run_pipeline, ProcessingResult

    result: ProcessingResult = await run_pipeline(source_path, config)
    # result.path  — path to the processed file (may be same as source_path
    #                if no changes were needed)
    # result.width, result.height  — final pixel dimensions
    # result.content_type          — e.g. "image/webp"
    # result.size                  — bytes of the processed file
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.trace import StatusCode

if TYPE_CHECKING:
    from astraeus_media.config import MediakitConfig

tracer = trace.get_tracer(__name__)


@dataclass
class ProcessingResult:
    """Outcome of :func:`run_pipeline`."""

    path: Path
    width: int
    height: int
    content_type: str
    size: int


def _sync_run_pipeline(source_path: Path, config: MediakitConfig) -> ProcessingResult:
    """Synchronous inner implementation — called from a thread via :func:`asyncio.to_thread`.

    Pillow I/O is CPU-bound; offloading to a thread keeps the event loop free.
    """
    from PIL import Image, ImageOps

    image = Image.open(source_path)

    # ------------------------------------------------------------------
    # 1. EXIF orientation correction + strip
    # ------------------------------------------------------------------
    if config.strip_exif:
        # exif_transpose corrects orientation, returns a copy if transpose was
        # applied (or the same object if no-op). We then convert to a format
        # without EXIF to guarantee the metadata is gone.
        image = ImageOps.exif_transpose(image) or image
        # Re-encode into a clean in-memory buffer to drop all EXIF chunks.
        # We do this by converting to RGB/RGBA (dropping palette modes) first.
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        else:
            # Force a clean copy (no EXIF info block)
            image = image.copy()
        # Remove any lingering _getexif data by clearing info dict keys
        image.info = {}

    # ------------------------------------------------------------------
    # 2. Max dimension cap
    # ------------------------------------------------------------------
    max_dim = config.max_dimension
    w, h = image.size
    if max(w, h) > max_dim:
        if w >= h:
            new_w = max_dim
            new_h = max(1, round(h * max_dim / w))
        else:
            new_h = max_dim
            new_w = max(1, round(w * max_dim / h))
        image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # ------------------------------------------------------------------
    # 3. Format conversion
    # ------------------------------------------------------------------
    upload_format = config.upload_format.lower()
    if upload_format == "webp":
        content_type = "image/webp"
        ext = ".webp"
        save_kwargs: dict = {"format": "webp", "quality": config.upload_quality}
    elif upload_format == "jpeg":
        content_type = "image/jpeg"
        ext = ".jpg"
        # Ensure no alpha channel for JPEG
        if image.mode == "RGBA":
            image = image.convert("RGB")
        save_kwargs = {"format": "jpeg", "quality": config.upload_quality}
    elif upload_format == "png":
        content_type = "image/png"
        ext = ".png"
        save_kwargs = {"format": "png"}
    else:
        # Fallback: keep source format
        content_type = "image/webp"
        ext = ".webp"
        save_kwargs = {"format": "webp", "quality": config.upload_quality}

    # ------------------------------------------------------------------
    # 4. Write output file
    # ------------------------------------------------------------------
    out_path = source_path.with_suffix(ext)
    final_w, final_h = image.size

    # JPEG can't have alpha; ensure RGB
    if upload_format == "jpeg" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    image.save(out_path, **save_kwargs)

    return ProcessingResult(
        path=out_path,
        width=final_w,
        height=final_h,
        content_type=content_type,
        size=out_path.stat().st_size,
    )


async def run_pipeline(source_path: Path, config: MediakitConfig) -> ProcessingResult:
    """Run the image processing pipeline on *source_path*.

    Offloads Pillow work to a thread pool so the event loop stays responsive.

    :param source_path: Path to the uploaded image file.
    :param config: :class:`~astraeus_media.config.MediakitConfig` instance controlling
        EXIF stripping, dimension cap, and output format/quality.
    :returns: :class:`ProcessingResult` with final dimensions, content type, and size.
    """
    with tracer.start_as_current_span("astraeus_media.processing.pipeline") as span:
        span.set_attribute("key", str(source_path))
        try:
            return await asyncio.to_thread(_sync_run_pipeline, source_path, config)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            raise
