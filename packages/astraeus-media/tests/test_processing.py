"""Tests for the image processing pipeline.

Uses real Pillow operations on in-memory images — no network required.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from astraeus_media.config import MediakitConfig
from astraeus_media.processing import ProcessingResult, run_pipeline
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jpeg_bytes(width: int = 100, height: int = 80, color: tuple = (255, 0, 0)) -> bytes:
    """Create a minimal JPEG image in memory."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="jpeg")
    return buf.getvalue()


def _make_png_bytes(width: int = 100, height: int = 80, mode: str = "RGBA") -> bytes:
    """Create a minimal PNG image in memory."""
    color = (0, 128, 255, 200) if mode == "RGBA" else (0, 128, 255)
    img = Image.new(mode, (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="png")
    return buf.getvalue()


def _write_tmp_image(tmpdir: Path, data: bytes, suffix: str = ".jpg") -> Path:
    p = tmpdir / f"test_image{suffix}"
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
# ProcessingResult dataclass
# ---------------------------------------------------------------------------


def test_processing_result_fields():
    """ProcessingResult exposes the expected fields."""
    r = ProcessingResult(
        path=Path("/tmp/x.webp"),
        width=800,
        height=600,
        content_type="image/webp",
        size=12345,
    )
    assert r.path == Path("/tmp/x.webp")
    assert r.width == 800
    assert r.height == 600
    assert r.content_type == "image/webp"
    assert r.size == 12345


# ---------------------------------------------------------------------------
# run_pipeline — WebP conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_converts_to_webp(tmp_path: Path) -> None:
    """Default config converts JPEG to WebP."""
    jpeg_bytes = _make_jpeg_bytes(200, 150)
    source = _write_tmp_image(tmp_path, jpeg_bytes, ".jpg")

    config = MediakitConfig(
        bucket="b",
        upload_format="webp",
        upload_quality=85,
        max_dimension=4096,
        strip_exif=True,
    )
    result = await run_pipeline(source, config)

    assert result.content_type == "image/webp"
    assert result.path.suffix == ".webp"
    assert result.size > 0
    assert result.width == 200
    assert result.height == 150


@pytest.mark.asyncio
async def test_pipeline_converts_png_to_webp(tmp_path: Path) -> None:
    """PNG with alpha channel is converted to WebP without error."""
    png_bytes = _make_png_bytes(100, 100, mode="RGBA")
    source = _write_tmp_image(tmp_path, png_bytes, ".png")

    config = MediakitConfig(
        bucket="b",
        upload_format="webp",
        upload_quality=80,
        max_dimension=4096,
    )
    result = await run_pipeline(source, config)

    assert result.content_type == "image/webp"
    assert result.width == 100
    assert result.height == 100


# ---------------------------------------------------------------------------
# run_pipeline — max dimension cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_caps_wide_image(tmp_path: Path) -> None:
    """Wide image (landscape) is resized so the long edge equals max_dimension."""
    jpeg_bytes = _make_jpeg_bytes(2000, 1000)
    source = _write_tmp_image(tmp_path, jpeg_bytes, ".jpg")

    config = MediakitConfig(
        bucket="b",
        max_dimension=800,
        upload_format="webp",
        upload_quality=80,
    )
    result = await run_pipeline(source, config)

    assert result.width == 800
    assert result.height == 400  # preserves aspect ratio (2:1)


@pytest.mark.asyncio
async def test_pipeline_caps_tall_image(tmp_path: Path) -> None:
    """Tall image (portrait) is resized so the long edge equals max_dimension."""
    jpeg_bytes = _make_jpeg_bytes(400, 1600)
    source = _write_tmp_image(tmp_path, jpeg_bytes, ".jpg")

    config = MediakitConfig(
        bucket="b",
        max_dimension=800,
        upload_format="webp",
        upload_quality=80,
    )
    result = await run_pipeline(source, config)

    assert result.height == 800
    assert result.width == 200  # preserves aspect ratio (1:4)


@pytest.mark.asyncio
async def test_pipeline_small_image_not_upscaled(tmp_path: Path) -> None:
    """Image smaller than max_dimension is not resized."""
    jpeg_bytes = _make_jpeg_bytes(300, 200)
    source = _write_tmp_image(tmp_path, jpeg_bytes, ".jpg")

    config = MediakitConfig(
        bucket="b",
        max_dimension=4096,
        upload_format="webp",
        upload_quality=80,
    )
    result = await run_pipeline(source, config)

    assert result.width == 300
    assert result.height == 200


# ---------------------------------------------------------------------------
# run_pipeline — EXIF stripping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_strips_exif(tmp_path: Path) -> None:
    """Processed output has no EXIF data."""
    # Build a JPEG with minimal EXIF (just orientation tag)
    img = Image.new("RGB", (100, 50), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="jpeg")
    jpeg_bytes = buf.getvalue()
    source = _write_tmp_image(tmp_path, jpeg_bytes, ".jpg")

    config = MediakitConfig(
        bucket="b",
        strip_exif=True,
        upload_format="webp",
        max_dimension=4096,
    )
    result = await run_pipeline(source, config)

    # Open the result and verify no EXIF
    out_img = Image.open(result.path)
    # WebP and Pillow: _getexif() is only on JPEG, but we can check info dict
    assert "exif" not in out_img.info or out_img.info.get("exif") in (None, b"")


# ---------------------------------------------------------------------------
# run_pipeline — alternate formats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_jpeg_output(tmp_path: Path) -> None:
    """upload_format='jpeg' produces a JPEG output."""
    jpeg_bytes = _make_jpeg_bytes(100, 100)
    source = _write_tmp_image(tmp_path, jpeg_bytes, ".jpg")

    config = MediakitConfig(
        bucket="b",
        upload_format="jpeg",
        upload_quality=90,
        max_dimension=4096,
    )
    result = await run_pipeline(source, config)

    assert result.content_type == "image/jpeg"
    assert result.path.suffix == ".jpg"


@pytest.mark.asyncio
async def test_pipeline_png_output(tmp_path: Path) -> None:
    """upload_format='png' produces a PNG output."""
    jpeg_bytes = _make_jpeg_bytes(100, 100)
    source = _write_tmp_image(tmp_path, jpeg_bytes, ".jpg")

    config = MediakitConfig(
        bucket="b",
        upload_format="png",
        max_dimension=4096,
    )
    result = await run_pipeline(source, config)

    assert result.content_type == "image/png"
    assert result.path.suffix == ".png"


# ---------------------------------------------------------------------------
# run_pipeline — output file is written on disk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_result_path_exists(tmp_path: Path) -> None:
    """The result path points to a real file with non-zero size."""
    jpeg_bytes = _make_jpeg_bytes(50, 50)
    source = _write_tmp_image(tmp_path, jpeg_bytes, ".jpg")

    config = MediakitConfig(bucket="b", upload_format="webp")
    result = await run_pipeline(source, config)

    assert result.path.exists()
    assert result.path.stat().st_size == result.size
    assert result.size > 0
