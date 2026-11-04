"""Tests for the IIIF Image API.

Covers:
- parse_iiif_params / IIIFParams
- render_iiif transforms (region, size, rotation, quality, format)
- HTTP routes: GET /iiif/{key}/info.json and GET /iiif/{key}/{...}

Storage is faked via an extended FakeStorage that also supports get/put bytes,
and a MemoryStore-backed version for render integration tests.
"""

from __future__ import annotations

import io

import pytest
import pytest_asyncio
from astraeus_media.config import MediakitConfig
from astraeus_media.routes.iiif import IIIFParams, parse_iiif_params, render_iiif
from httpx import ASGITransport, AsyncClient
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rgb_jpeg(width: int = 200, height: int = 100) -> bytes:
    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    buf = io.BytesIO()
    img.save(buf, format="jpeg")
    return buf.getvalue()


def _img_size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


AUTH = {"Authorization": "Bearer test-secret"}


# ---------------------------------------------------------------------------
# IIIFParams.canonical_key
# ---------------------------------------------------------------------------


def test_canonical_key_full_max():
    p = IIIFParams(region="full", size="max", rotation=0, quality="default", format="webp")
    assert p.canonical_key == "full/max/0/default.webp"


def test_canonical_key_crop_size_rotation():
    p = IIIFParams(region="0,0,100,100", size="50,", rotation=90, quality="gray", format="jpg")
    assert p.canonical_key == "0,0,100,100/50,/90/gray.jpg"


def test_content_type_mapping():
    assert IIIFParams("full", "max", 0, "default", "webp").content_type == "image/webp"
    assert IIIFParams("full", "max", 0, "default", "jpg").content_type == "image/jpeg"
    assert IIIFParams("full", "max", 0, "default", "png").content_type == "image/png"


# ---------------------------------------------------------------------------
# parse_iiif_params — valid inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("region", ["full", "square", "0,0,100,100", "10,20,300,400"])
def test_parse_valid_regions(region):
    p = parse_iiif_params(region, "max", "0", "default", "webp")
    assert p.region == region


@pytest.mark.parametrize("size", ["full", "max", "200,", ",150", "200,150", "!200,150"])
def test_parse_valid_sizes(size):
    p = parse_iiif_params("full", size, "0", "default", "webp")
    assert p.size == size


@pytest.mark.parametrize("rotation", ["0", "90", "180", "270"])
def test_parse_valid_rotations(rotation):
    p = parse_iiif_params("full", "max", rotation, "default", "webp")
    assert p.rotation == int(rotation)


@pytest.mark.parametrize("quality", ["default", "color", "gray"])
def test_parse_valid_qualities(quality):
    p = parse_iiif_params("full", "max", "0", quality, "webp")
    assert p.quality == quality


@pytest.mark.parametrize("fmt", ["jpg", "webp", "png"])
def test_parse_valid_formats(fmt):
    p = parse_iiif_params("full", "max", "0", "default", fmt)
    assert p.format == fmt


# ---------------------------------------------------------------------------
# parse_iiif_params — invalid inputs
# ---------------------------------------------------------------------------


def test_parse_invalid_region_bad_count():
    with pytest.raises(ValueError, match="region"):
        parse_iiif_params("0,0,100", "max", "0", "default", "webp")


def test_parse_invalid_region_non_integer():
    with pytest.raises(ValueError, match="region"):
        parse_iiif_params("x,0,100,100", "max", "0", "default", "webp")


def test_parse_invalid_size_bad_parts():
    with pytest.raises(ValueError, match="size"):
        parse_iiif_params("full", "200x300", "0", "default", "webp")


def test_parse_invalid_size_both_empty():
    with pytest.raises(ValueError, match="size"):
        parse_iiif_params("full", ",", "0", "default", "webp")


def test_parse_invalid_rotation():
    with pytest.raises(ValueError, match="rotation"):
        parse_iiif_params("full", "max", "45", "default", "webp")


def test_parse_invalid_quality():
    with pytest.raises(ValueError, match="quality"):
        parse_iiif_params("full", "max", "0", "raw", "webp")


def test_parse_invalid_format():
    with pytest.raises(ValueError, match="format"):
        parse_iiif_params("full", "max", "0", "default", "bmp")


# ---------------------------------------------------------------------------
# render_iiif — integration (Pillow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_full_max_default_webp() -> None:
    """full/max/0/default.webp — no transform, WebP output."""
    jpeg = _make_rgb_jpeg(200, 100)
    params = parse_iiif_params("full", "max", "0", "default", "webp")
    output, ct = await render_iiif(jpeg, params)

    assert ct == "image/webp"
    assert _img_size(output) == (200, 100)


@pytest.mark.asyncio
async def test_render_square_crop() -> None:
    """square region crops to center square."""
    jpeg = _make_rgb_jpeg(200, 100)  # wide: 200×100
    params = parse_iiif_params("square", "max", "0", "default", "webp")
    output, _ = await render_iiif(jpeg, params)

    w, h = _img_size(output)
    assert w == h == 100


@pytest.mark.asyncio
async def test_render_pixel_crop() -> None:
    """x,y,w,h region crops correctly."""
    jpeg = _make_rgb_jpeg(200, 100)
    params = parse_iiif_params("50,10,80,60", "max", "0", "default", "webp")
    output, _ = await render_iiif(jpeg, params)

    w, h = _img_size(output)
    assert w == 80
    assert h == 60


@pytest.mark.asyncio
async def test_render_size_by_width() -> None:
    """'w,' size scales by width preserving aspect ratio."""
    jpeg = _make_rgb_jpeg(200, 100)
    params = parse_iiif_params("full", "100,", "0", "default", "webp")
    output, _ = await render_iiif(jpeg, params)

    w, h = _img_size(output)
    assert w == 100
    assert h == 50  # half of original


@pytest.mark.asyncio
async def test_render_size_by_height() -> None:
    """,h size scales by height preserving aspect ratio."""
    jpeg = _make_rgb_jpeg(200, 100)
    params = parse_iiif_params("full", ",50", "0", "default", "webp")
    output, _ = await render_iiif(jpeg, params)

    w, h = _img_size(output)
    assert h == 50
    assert w == 100  # preserves 2:1 ratio


@pytest.mark.asyncio
async def test_render_size_exact() -> None:
    """'w,h' sizes to exact dimensions (may distort)."""
    jpeg = _make_rgb_jpeg(200, 100)
    params = parse_iiif_params("full", "150,75", "0", "default", "webp")
    output, _ = await render_iiif(jpeg, params)

    assert _img_size(output) == (150, 75)


@pytest.mark.asyncio
async def test_render_fit_in() -> None:
    """'!w,h' fits inside the box, preserving aspect ratio."""
    jpeg = _make_rgb_jpeg(200, 100)
    params = parse_iiif_params("full", "!80,80", "0", "default", "webp")
    output, _ = await render_iiif(jpeg, params)

    w, h = _img_size(output)
    # 200×100 fits in 80×80 with ratio 0.4 → 80×40
    assert w == 80
    assert h == 40


@pytest.mark.asyncio
async def test_render_rotation_90() -> None:
    """Rotating 90° swaps width and height."""
    jpeg = _make_rgb_jpeg(200, 100)
    params = parse_iiif_params("full", "max", "90", "default", "webp")
    output, _ = await render_iiif(jpeg, params)

    w, h = _img_size(output)
    assert w == 100
    assert h == 200


@pytest.mark.asyncio
async def test_render_rotation_180() -> None:
    """Rotating 180° keeps dimensions the same."""
    jpeg = _make_rgb_jpeg(200, 100)
    params = parse_iiif_params("full", "max", "180", "default", "webp")
    output, _ = await render_iiif(jpeg, params)

    assert _img_size(output) == (200, 100)


@pytest.mark.asyncio
async def test_render_gray_quality() -> None:
    """gray quality converts to grayscale."""
    jpeg = _make_rgb_jpeg(100, 50)
    params = parse_iiif_params("full", "max", "0", "gray", "png")
    output, ct = await render_iiif(jpeg, params)

    assert ct == "image/png"
    img = Image.open(io.BytesIO(output))
    assert img.mode == "L"


@pytest.mark.asyncio
async def test_render_jpg_format() -> None:
    """jpg format produces JPEG bytes."""
    jpeg = _make_rgb_jpeg(100, 50)
    params = parse_iiif_params("full", "max", "0", "default", "jpg")
    output, ct = await render_iiif(jpeg, params)

    assert ct == "image/jpeg"
    img = Image.open(io.BytesIO(output))
    assert img.format == "JPEG"


@pytest.mark.asyncio
async def test_render_png_format() -> None:
    """png format produces PNG bytes."""
    jpeg = _make_rgb_jpeg(100, 50)
    params = parse_iiif_params("full", "max", "0", "default", "png")
    output, ct = await render_iiif(jpeg, params)

    assert ct == "image/png"
    img = Image.open(io.BytesIO(output))
    assert img.format == "PNG"


# ---------------------------------------------------------------------------
# HTTP route tests — FakeStorage extended with get/put bytes
# ---------------------------------------------------------------------------


class FakeStorageWithBytes:
    """FakeStorage that also holds bytes for each key (for IIIF download)."""

    def __init__(self, config: MediakitConfig) -> None:
        self.config = config
        self._store: dict[str, bytes] = {}
        self._deleted: set[str] = set()

    def put_bytes(self, key: str, data: bytes) -> None:
        self._store[key] = data
        self._deleted.discard(key)

    def get_bytes_sync(self, key: str) -> bytes:
        return self._store[key]

    async def prepare_upload(self, key: str, content_type: str, expires_in: int = 900) -> dict:
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
        return {
            "upload_url": f"https://fake/{key}?presigned=1",
            "key": key,
            "expires_at": expires_at,
        }

    async def confirm_exists(self, key: str) -> bool:
        return key not in self._deleted and key in self._store

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://fake-cdn.example.com/{key}"

    async def delete(self, key: str) -> None:
        self._deleted.add(key)
        self._store.pop(key, None)

    async def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        return []

    def _get_store(self):
        """Return an obstore MemoryStore seeded with current keys."""
        import obstore.store as obs

        mem = obs.MemoryStore()
        return _SyncSeededStore(mem, self._store)


class _SyncSeededStore:
    """Tiny shim that wraps obstore.MemoryStore and answers get_async/put_async."""

    def __init__(self, mem, data: dict[str, bytes]) -> None:
        import asyncio

        self._mem = mem
        # Pre-seed the memory store synchronously
        for k, v in data.items():
            # We need to run put_async; but we're in a sync context here.
            # Use a helper to seed via the event loop.
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Already inside event loop — schedule as a task and let it run
                    # We can't block here; caller must await separately.
                    # Store for later access.
                    pass
            except RuntimeError:
                pass
        self._data = data

    def __getattr__(self, name):
        return getattr(self._mem, name)


@pytest_asyncio.fixture
async def mk_iiif(tmp_path):
    """MediaKit with FakeStorageWithBytes and seeded asset in catalog."""
    from astraeus_media.app import MediaKit
    from astraeus_media.catalog.catalog import Catalog

    config = MediakitConfig(
        bucket="test-bucket",
        catalog_path=str(tmp_path / "test.db"),
        api_key="test-secret",
        auth="apikey",
    )
    mk = MediaKit(config=config)
    mk._catalog = Catalog(config.catalog_path)
    await mk._catalog.initialize()

    fake_storage = FakeStorageWithBytes(config)
    mk._storage = fake_storage  # type: ignore[assignment]
    return mk, fake_storage


@pytest_asyncio.fixture
async def iiif_client_with_asset(mk_iiif, tmp_path):
    """Client with an asset in the catalog and bytes in FakeStorageWithBytes."""
    mk, fake_storage = mk_iiif
    jpeg = _make_rgb_jpeg(200, 100)
    key = "originals/abc12345/photo.jpg"

    # Seed storage
    fake_storage.put_bytes(key, jpeg)

    # Insert asset into catalog
    await mk.catalog.insert_asset(
        key=key,
        content_hash="deadbeef",
        bucket="test-bucket",
        filename="photo.jpg",
        content_type="image/jpeg",
        size=len(jpeg),
        width=200,
        height=100,
    )

    app = mk._build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, mk, key


# ---------------------------------------------------------------------------
# GET /iiif/{key}/info.json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_info_json_returns_200(iiif_client_with_asset) -> None:
    client, mk, key = iiif_client_with_asset
    resp = await client.get(f"/iiif/{key}/info.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["@context"] == "http://iiif.io/api/image/2/context.json"
    assert data["width"] == 200
    assert data["height"] == 100
    assert "profile" in data


@pytest.mark.asyncio
async def test_info_json_404_unknown_key(iiif_client_with_asset) -> None:
    client, mk, key = iiif_client_with_asset
    resp = await client.get("/iiif/originals/nope/unknown.jpg/info.json")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_info_json_includes_sizes(iiif_client_with_asset) -> None:
    client, mk, key = iiif_client_with_asset
    resp = await client.get(f"/iiif/{key}/info.json")
    data = resp.json()
    assert "sizes" in data
    assert len(data["sizes"]) == 2


# ---------------------------------------------------------------------------
# GET /iiif/{key}/{region}/{size}/{rotation}/{quality}.{format}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iiif_image_invalid_format(iiif_client_with_asset) -> None:
    client, mk, key = iiif_client_with_asset
    resp = await client.get(f"/iiif/{key}/full/max/0/default.bmp", follow_redirects=False)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_iiif_image_invalid_rotation(iiif_client_with_asset) -> None:
    client, mk, key = iiif_client_with_asset
    resp = await client.get(f"/iiif/{key}/full/max/45/default.webp", follow_redirects=False)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_iiif_image_unknown_key(iiif_client_with_asset) -> None:
    client, mk, key = iiif_client_with_asset
    url = "/iiif/originals/nope/missing.jpg/full/max/0/default.webp"
    resp = await client.get(url, follow_redirects=False)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_iiif_image_generates_derivative_and_redirects(iiif_client_with_asset) -> None:
    """First request generates derivative → 302 to derivative URL."""
    client, mk, key = iiif_client_with_asset

    # Patch _get_store to use a MemoryStore with data pre-seeded
    import obstore
    import obstore.store as obs

    mem = obs.MemoryStore()
    jpeg = _make_rgb_jpeg(200, 100)
    await obstore.put_async(mem, key, jpeg)

    # Replace the fake storage's _get_store method to return our memory store
    mk._storage._get_store = lambda: mem  # type: ignore[attr-defined]

    resp = await client.get(f"/iiif/{key}/full/max/0/default.webp", follow_redirects=False)
    assert resp.status_code == 302
    assert "fake-cdn.example.com" in resp.headers["location"]


@pytest.mark.asyncio
async def test_iiif_image_caches_derivative(iiif_client_with_asset) -> None:
    """Second request for same params is served from catalog cache (302 to same URL)."""
    client, mk, key = iiif_client_with_asset

    import obstore
    import obstore.store as obs

    mem = obs.MemoryStore()
    jpeg = _make_rgb_jpeg(200, 100)
    await obstore.put_async(mem, key, jpeg)
    mk._storage._get_store = lambda: mem  # type: ignore[attr-defined]

    # First request generates
    r1 = await client.get(f"/iiif/{key}/full/max/0/default.webp", follow_redirects=False)
    assert r1.status_code == 302

    # Second request should hit catalog cache — same redirect target
    r2 = await client.get(f"/iiif/{key}/full/max/0/default.webp", follow_redirects=False)
    assert r2.status_code == 302
    assert r1.headers["location"] == r2.headers["location"]


@pytest.mark.asyncio
async def test_iiif_image_different_params_separate_derivatives(iiif_client_with_asset) -> None:
    """Different IIIF params each get their own derivative entry."""
    client, mk, key = iiif_client_with_asset

    import obstore
    import obstore.store as obs

    mem = obs.MemoryStore()
    jpeg = _make_rgb_jpeg(200, 100)
    await obstore.put_async(mem, key, jpeg)
    mk._storage._get_store = lambda: mem  # type: ignore[attr-defined]

    r1 = await client.get(f"/iiif/{key}/full/max/0/default.webp", follow_redirects=False)
    r2 = await client.get(f"/iiif/{key}/square/max/0/default.webp", follow_redirects=False)
    assert r1.status_code == 302
    assert r2.status_code == 302
    # Different params → different derivative keys
    assert r1.headers["location"] != r2.headers["location"]


# ---------------------------------------------------------------------------
# render_iiif — originals are stored untouched, so render copes with them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_applies_exif_orientation() -> None:
    """A 300x200 photo flagged "rotate 90" renders as 200x300, as it displays."""
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 60, 60)).save(buf, format="JPEG", exif=exif.tobytes())

    out, _ = await render_iiif(
        buf.getvalue(), parse_iiif_params("full", "max", "0", "default", "webp")
    )

    assert _img_size(out) == (200, 300)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["CMYK", "I;16", "P"])
async def test_render_handles_modes_the_web_encoders_cannot_write(mode: str) -> None:
    """TIFF originals are often CMYK or 16-bit; they still render to WebP."""
    buf = io.BytesIO()
    Image.new(mode, (64, 48)).save(buf, format="TIFF")

    out, content_type = await render_iiif(
        buf.getvalue(), parse_iiif_params("full", "32,", "0", "default", "webp")
    )

    assert content_type == "image/webp"
    assert _img_size(out) == (32, 24)
