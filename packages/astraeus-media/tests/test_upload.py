"""HTTP tests for the upload prepare + confirm endpoints."""

from __future__ import annotations

import io

import obstore
import pytest
import pytest_asyncio
from astraeus_media.app import MediaKit
from astraeus_media.catalog.catalog import Catalog
from astraeus_media.config import MediakitConfig
from astraeus_media.routes.iiif import PRERENDERED_RENDITIONS, ensure_derivative, parse_iiif_params
from httpx import ASGITransport, AsyncClient
from obstore.store import MemoryStore
from PIL import Image

AUTH = {"Authorization": "Bearer test-secret"}


# ---------------------------------------------------------------------------
# POST /upload/prepare
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_upload_success(client: AsyncClient) -> None:
    resp = await client.post(
        "/upload/prepare",
        json={"filename": "photo.webp", "content_type": "image/webp", "size": 204800},
        headers=AUTH,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "upload_url" in data
    assert "key" in data
    assert "expires_at" in data
    assert data["key"].startswith("originals/")
    assert "photo.webp" in data["key"]


@pytest.mark.asyncio
async def test_prepare_upload_unauthorized(client: AsyncClient) -> None:
    resp = await client.post(
        "/upload/prepare",
        json={"filename": "photo.webp", "content_type": "image/webp", "size": 100},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_prepare_upload_missing_fields(client: AsyncClient) -> None:
    resp = await client.post(
        "/upload/prepare",
        json={"filename": "photo.webp"},
        headers=AUTH,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_prepare_upload_invalid_json(client: AsyncClient) -> None:
    resp = await client.post(
        "/upload/prepare",
        content=b"not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /upload/confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_upload_success(client: AsyncClient) -> None:
    resp = await client.post(
        "/upload/confirm",
        json={
            "key": "originals/abc12345/photo.webp",
            "filename": "photo.webp",
            "content_type": "image/webp",
            "size": 102400,
        },
        headers=AUTH,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["key"] == "originals/abc12345/photo.webp"
    assert data["content_type"] == "image/webp"


@pytest.mark.asyncio
async def test_confirm_upload_with_dimensions(client: AsyncClient) -> None:
    resp = await client.post(
        "/upload/confirm",
        json={
            "key": "originals/dim/photo.webp",
            "filename": "photo.webp",
            "content_type": "image/webp",
            "size": 50000,
            "width": 1920,
            "height": 1080,
        },
        headers=AUTH,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["width"] == 1920
    assert data["height"] == 1080


@pytest.mark.asyncio
async def test_confirm_upload_not_in_bucket(client: AsyncClient, mk) -> None:
    """If the key is 'deleted' from FakeStorage, confirm should 404."""
    mk.storage.remove("originals/gone/photo.webp")  # type: ignore[attr-defined]
    resp = await client.post(
        "/upload/confirm",
        json={
            "key": "originals/gone/photo.webp",
            "filename": "photo.webp",
            "content_type": "image/webp",
            "size": 100,
        },
        headers=AUTH,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_confirm_upload_unauthorized(client: AsyncClient) -> None:
    resp = await client.post(
        "/upload/confirm",
        json={
            "key": "originals/abc/photo.webp",
            "filename": "photo.webp",
            "content_type": "image/webp",
            "size": 100,
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_confirm_upload_missing_fields(client: AsyncClient) -> None:
    resp = await client.post(
        "/upload/confirm",
        json={"key": "originals/abc/photo.webp"},
        headers=AUTH,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /upload/confirm — what happens to the uploaded object
# ---------------------------------------------------------------------------

ORIGINAL_KEY = "originals/abc12345/artwork.tif"


class MemoryStorage:
    """The storage calls confirm makes, backed by a real in-memory object store."""

    def __init__(self, config: MediakitConfig) -> None:
        self.config = config
        self.mk: MediaKit | None = None
        self.objects = MemoryStore()

    def _get_store(self) -> MemoryStore:
        return self.objects

    async def confirm_exists(self, key: str) -> bool:
        try:
            await obstore.head_async(self.objects, key)
        except FileNotFoundError:
            return False
        return True

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://signed.example/{self.config.bucket}/{key}"

    async def delete(self, key: str) -> None:
        await obstore.delete_async(self.objects, [key])

    async def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        found: list[dict] = []
        async for batch in obstore.list(self.objects, prefix=prefix or None):
            found.extend({"key": obj["path"]} for obj in batch)
        return found


def _image_bytes(
    fmt: str, size: tuple[int, int] = (300, 200), mode: str = "RGB", **save_kwargs
) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (200, 60, 60) if mode == "RGB" else None).save(
        buf, format=fmt, **save_kwargs
    )
    return buf.getvalue()


def _jpeg_rotated_a_quarter_turn(size: tuple[int, int] = (300, 200)) -> bytes:
    """A JPEG whose pixels are `size` but whose EXIF orientation says "rotate 90"."""
    exif = Image.Exif()
    exif[0x0112] = 6
    return _image_bytes("JPEG", size, exif=exif.tobytes())


async def _confirm(client: AsyncClient, key: str, filename: str, content_type: str):
    return await client.post(
        "/upload/confirm",
        json={"key": key, "filename": filename, "content_type": content_type, "size": 1},
        headers=AUTH,
    )


@pytest_asyncio.fixture
async def make_client(tmp_path):
    """Factory: ``(client, storage)`` for a MediaKit with the given settings."""
    opened: list[tuple[MediaKit, AsyncClient]] = []

    async def build(**config_overrides) -> tuple[AsyncClient, MemoryStorage]:
        config = MediakitConfig(
            bucket="test-bucket",
            catalog_path=str(tmp_path / f"media-{len(opened)}.db"),
            api_key="test-secret",
            auth="apikey",
            **config_overrides,
        )
        instance = MediaKit(config=config)
        instance._catalog = Catalog(config.catalog_path)
        await instance._catalog.initialize()
        storage = MemoryStorage(config)
        instance._storage = storage  # type: ignore[assignment]
        storage.mk = instance
        if config.derivatives_bucket:
            instance._derivative_storage = MemoryStorage(
                config.model_copy(update={"bucket": config.derivatives_bucket})
            )  # type: ignore[assignment]
        http = AsyncClient(
            transport=ASGITransport(app=instance._build_app()), base_url="http://testserver"
        )
        opened.append((instance, http))
        return http, storage

    yield build

    for instance, http in opened:
        await http.aclose()
        await instance._catalog.close()


@pytest.mark.asyncio
async def test_confirm_keeps_the_original_untouched_by_default(make_client) -> None:
    client, storage = await make_client()
    original = _image_bytes("TIFF", (300, 200))
    await obstore.put_async(storage.objects, ORIGINAL_KEY, original)

    resp = await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")

    assert resp.status_code == 201
    asset = resp.json()
    assert (asset["content_type"], asset["width"], asset["height"]) == ("image/tiff", 300, 200)
    assert asset["size"] == len(original)
    stored = await obstore.get_async(storage.objects, ORIGINAL_KEY)
    assert bytes(await stored.bytes_async()) == original


@pytest.mark.asyncio
async def test_confirm_does_not_cap_large_originals(make_client) -> None:
    client, storage = await make_client()
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (5000, 100)))

    asset = (await _confirm(client, ORIGINAL_KEY, "wide.tif", "image/tiff")).json()

    assert (asset["width"], asset["height"]) == (5000, 100)


@pytest.mark.asyncio
async def test_confirm_records_the_displayed_size_of_a_rotated_photo(make_client) -> None:
    client, storage = await make_client()
    key = "originals/abc12345/phone.jpg"
    await obstore.put_async(storage.objects, key, _jpeg_rotated_a_quarter_turn((300, 200)))

    asset = (await _confirm(client, key, "phone.jpg", "image/jpeg")).json()

    assert (asset["width"], asset["height"]) == (200, 300)


@pytest.mark.asyncio
async def test_confirm_falls_back_to_client_metadata_for_a_non_image(make_client) -> None:
    client, storage = await make_client()
    key = "originals/abc12345/notes.pdf"
    await obstore.put_async(storage.objects, key, b"%PDF-1.7 not an image")

    resp = await _confirm(client, key, "notes.pdf", "application/pdf")

    assert resp.status_code == 201
    assert resp.json()["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_confirm_can_still_normalise_when_originals_are_not_kept(make_client) -> None:
    client, storage = await make_client(keep_originals=False, max_dimension=100)
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (300, 200)))

    asset = (await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")).json()

    assert asset["content_type"] == "image/webp"
    assert (asset["width"], asset["height"]) == (100, 67)
    stored = await obstore.get_async(storage.objects, ORIGINAL_KEY)
    assert Image.open(io.BytesIO(bytes(await stored.bytes_async()))).format == "WEBP"


def _derivative_key(key: str, region: str, size: str) -> str:
    params = parse_iiif_params(region, size, "0", "default", "webp")
    return f"derivatives/{key}/{params.canonical_key}"


@pytest.mark.asyncio
async def test_confirm_prerenders_the_admin_renditions(make_client) -> None:
    client, storage = await make_client()
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (900, 600)))

    await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")

    for region, size in PRERENDERED_RENDITIONS:
        key = _derivative_key(ORIGINAL_KEY, region, size)
        stored = await obstore.get_async(storage.objects, key)
        assert Image.open(io.BytesIO(bytes(await stored.bytes_async()))).format == "WEBP"


@pytest.mark.asyncio
async def test_a_prerender_failure_does_not_fail_the_upload(make_client) -> None:
    client, storage = await make_client()
    await obstore.put_async(storage.objects, ORIGINAL_KEY, b"%PDF-1.7 not an image")

    resp = await _confirm(client, ORIGINAL_KEY, "doc.pdf", "application/pdf")

    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_a_rendition_row_without_a_file_is_rendered_when_asked_for(make_client) -> None:
    """A request that lost a race, or a failed render, leaves a row with no file."""
    client, storage = await make_client()
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (900, 600)))
    await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")

    params = parse_iiif_params("full", "300,", "0", "default", "webp")
    derivative_key = _derivative_key(ORIGINAL_KEY, "full", "300,")
    # The state a lost race leaves behind: a row, no dimensions, no object.
    await storage.mk.catalog.get_or_create_derivative(
        ORIGINAL_KEY, params.canonical_key, derivative_key, format="webp"
    )

    returned = await ensure_derivative(storage.mk, ORIGINAL_KEY, params)

    assert returned == derivative_key
    stored = await obstore.get_async(storage.objects, derivative_key)
    assert Image.open(io.BytesIO(bytes(await stored.bytes_async()))).size == (300, 200)


async def _exists(store: MemoryStore, key: str) -> bool:
    try:
        await obstore.head_async(store, key)
    except FileNotFoundError:
        return False
    return True


@pytest.mark.asyncio
async def test_renditions_go_to_the_derivatives_bucket_with_serving_headers(make_client) -> None:
    client, storage = await make_client(derivatives_bucket="media-public")
    derivatives = storage.mk.derivative_storage
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (900, 600)))

    await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")

    key = _derivative_key(ORIGINAL_KEY, "square", "256,")
    assert await _exists(derivatives.objects, key)
    assert not await _exists(storage.objects, key)
    rendition = await obstore.get_async(derivatives.objects, key)
    assert rendition.attributes["Content-Type"] == "image/webp"
    assert rendition.attributes["Cache-Control"] == "public, max-age=2592000"


@pytest.mark.asyncio
async def test_renditions_stay_beside_the_originals_by_default(make_client) -> None:
    client, storage = await make_client()
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (900, 600)))

    await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")

    assert storage.mk.derivative_storage is storage
    assert await _exists(storage.objects, _derivative_key(ORIGINAL_KEY, "square", "256,"))


@pytest.mark.asyncio
async def test_iiif_redirects_to_the_public_url_without_signing(make_client) -> None:
    client, storage = await make_client(
        derivatives_bucket="media-public", derivatives_public_url="https://media.example.com/"
    )
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (900, 600)))
    await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")

    resp = await client.get(f"/iiif/{ORIGINAL_KEY}/square/256,/0/default.webp")

    assert resp.status_code == 302
    assert resp.headers["location"] == (
        f"https://media.example.com/{_derivative_key(ORIGINAL_KEY, 'square', '256,')}"
    )


@pytest.mark.asyncio
async def test_iiif_presigns_from_the_derivatives_bucket_without_a_public_url(make_client) -> None:
    client, storage = await make_client(derivatives_bucket="media-public")
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (900, 600)))
    await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")

    resp = await client.get(f"/iiif/{ORIGINAL_KEY}/square/256,/0/default.webp")

    assert resp.headers["location"].startswith("https://signed.example/media-public/derivatives/")


@pytest.mark.asyncio
async def test_deleting_an_asset_deletes_its_renditions(make_client) -> None:
    client, storage = await make_client(derivatives_bucket="media-public")
    derivatives = storage.mk.derivative_storage
    await obstore.put_async(storage.objects, ORIGINAL_KEY, _image_bytes("TIFF", (900, 600)))
    await _confirm(client, ORIGINAL_KEY, "artwork.tif", "image/tiff")
    rendition = _derivative_key(ORIGINAL_KEY, "full", "800,")
    assert await _exists(derivatives.objects, rendition)

    resp = await client.delete(f"/assets/{ORIGINAL_KEY}", headers=AUTH)

    assert resp.status_code == 204
    assert not await _exists(derivatives.objects, rendition)
    assert not await _exists(storage.objects, ORIGINAL_KEY)
