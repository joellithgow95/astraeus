"""Tests for the astraeus-media admin UI.

Covers:
- Asset browser (normal mode + picker mode + filters + pagination)
- Upload page
- Asset detail (200 + 404)
- Metadata update (success + unauthorized)

All fixtures are local — only ``mk_config`` from conftest is reused.
Uses the same httpx.AsyncClient + ASGITransport pattern as all other HTTP tests.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from astraeus_media.app import MediaKit
from astraeus_media.catalog.catalog import Catalog
from astraeus_media.config import MediakitConfig
from astraeus_media.storage.backend import StorageBackend as _StorageBackend
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# FakeStorage — inline copy to avoid cross-package conftest resolution issues
# ---------------------------------------------------------------------------


class FakeStorage:
    """Minimal in-memory StorageBackend for tests."""

    def __init__(self, config: MediakitConfig) -> None:
        self.config = config
        self._deleted: set[str] = set()

    async def prepare_upload(self, key: str, content_type: str, expires_in: int = 900) -> dict:
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
        return {
            "upload_url": f"https://fake-bucket.example.com/{key}?presigned=1",
            "key": key,
            "expires_at": expires_at,
        }

    async def confirm_exists(self, key: str) -> bool:
        return key not in self._deleted

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://fake-bucket.example.com/{key}"

    async def delete(self, key: str) -> None:
        self._deleted.add(key)

    async def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        return []


assert isinstance(FakeStorage(MediakitConfig(bucket="x")), _StorageBackend), (
    "FakeStorage must satisfy StorageBackend protocol"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUTH = {"Authorization": "Bearer test-secret"}

# Three distinct assets seeded into the catalog for filtering/pagination tests
_SEED_ASSETS = [
    {
        "key": "originals/aaa/photo_a.webp",
        "content_hash": "hash_a",
        "bucket": "test-bucket",
        "filename": "photo_a.webp",
        "content_type": "image/webp",
        "size": 1024,
        "width": 400,
        "height": 300,
    },
    {
        "key": "originals/bbb/photo_b.jpeg",
        "content_hash": "hash_b",
        "bucket": "test-bucket",
        "filename": "photo_b.jpeg",
        "content_type": "image/jpeg",
        "size": 2048,
        "width": 800,
        "height": 600,
    },
    {
        "key": "originals/ccc/photo_c.webp",
        "content_hash": "hash_c",
        "bucket": "test-bucket",
        "filename": "photo_c.webp",
        "content_type": "image/webp",
        "size": 512,
        "width": 200,
        "height": 150,
    },
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mk_with_assets(mk_config: MediakitConfig) -> AsyncGenerator[MediaKit, None]:
    """MediaKit instance with FakeStorage and 3 seeded catalog assets."""
    instance = MediaKit(config=mk_config)
    instance._catalog = Catalog(mk_config.catalog_path)
    await instance._catalog.initialize()
    instance._storage = FakeStorage(mk_config)  # type: ignore[assignment]

    for asset in _SEED_ASSETS:
        await instance._catalog.insert_asset(**asset)

    yield instance
    await instance._catalog.close()


@pytest_asyncio.fixture
async def admin_client(mk_with_assets: MediaKit) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient wired to the full MediaKit app (with admin routes)."""
    app = mk_with_assets._build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", headers=AUTH
    ) as c:
        yield c


@pytest_asyncio.fixture
async def anon_client(mk_with_assets: MediaKit) -> AsyncGenerator[AsyncClient, None]:
    """Same app, no credentials."""
    app = mk_with_assets._build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture
async def mounted_client(mk_with_assets: MediaKit) -> AsyncGenerator[AsyncClient, None]:
    """The app mounted at /media under a host, as joellithgow serves it."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    host = Starlette(routes=[Mount("/media", app=mk_with_assets._build_app())])
    async with AsyncClient(
        transport=ASGITransport(app=host), base_url="http://testserver", headers=AUTH
    ) as c:
        yield c


@pytest_asyncio.fixture
async def empty_admin_client(mk_config: MediakitConfig) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient backed by a MediaKit with no assets."""
    instance = MediaKit(config=mk_config)
    instance._catalog = Catalog(mk_config.catalog_path)
    await instance._catalog.initialize()
    instance._storage = FakeStorage(mk_config)  # type: ignore[assignment]

    app = instance._build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", headers=AUTH
    ) as c:
        yield c
        await instance._catalog.close()


# ---------------------------------------------------------------------------
# Browser — GET /admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_returns_200(admin_client: AsyncClient) -> None:
    """GET /admin returns 200 and lists asset filenames."""
    response = await admin_client.get("/admin")
    assert response.status_code == 200
    html = response.text
    assert "photo_a.webp" in html
    assert "photo_b.jpeg" in html
    assert "photo_c.webp" in html


@pytest.mark.asyncio
async def test_browser_empty(empty_admin_client: AsyncClient) -> None:
    """GET /admin with no assets returns 200 with empty-state message."""
    response = await empty_admin_client.get("/admin")
    assert response.status_code == 200
    assert "No assets found" in response.text


@pytest.mark.asyncio
async def test_browser_picker_mode(admin_client: AsyncClient) -> None:
    """GET /admin?picker=1 returns HTML with data-key attributes for picker JS."""
    response = await admin_client.get("/admin?picker=1")
    assert response.status_code == 200
    html = response.text
    assert 'data-key="originals/aaa/photo_a.webp"' in html
    assert 'data-key="originals/bbb/photo_b.jpeg"' in html


@pytest.mark.asyncio
async def test_browser_filter_content_type(admin_client: AsyncClient) -> None:
    """GET /admin?content_type=image/webp returns only webp assets."""
    response = await admin_client.get("/admin?content_type=image%2Fwebp")
    assert response.status_code == 200
    html = response.text
    assert "photo_a.webp" in html
    assert "photo_c.webp" in html
    # The JPEG should not appear
    assert "photo_b.jpeg" not in html


@pytest.mark.asyncio
async def test_browser_pagination(admin_client: AsyncClient) -> None:
    """GET /admin?limit=2&offset=2 returns only the last asset (the third)."""
    response = await admin_client.get("/admin?limit=2&offset=2")
    assert response.status_code == 200
    html = response.text
    # The oldest asset (created last in LIFO order — list_assets sorts by created_at DESC)
    # With 3 assets and offset=2 limit=2 we get at most 1
    # Exactly which one depends on insertion order; just verify it's non-empty and ≤2
    # photo_a, photo_b, photo_c all inserted in sequence, so newest → oldest order
    # offset=2 skips the first two → photo_a (the first inserted / oldest) appears
    assert "photo_a.webp" in html
    # The other two should not appear (they are in the first page)
    assert "photo_b.jpeg" not in html
    assert "photo_c.webp" not in html


# ---------------------------------------------------------------------------
# Upload page — GET /admin/upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_page_returns_200(admin_client: AsyncClient) -> None:
    """GET /admin/upload returns 200 with the drop zone."""
    response = await admin_client.get("/admin/upload")
    assert response.status_code == 200
    assert "drop-zone" in response.text


# ---------------------------------------------------------------------------
# Detail — GET /admin/assets/{key:path}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_returns_200(admin_client: AsyncClient) -> None:
    """GET /admin/assets/{key} returns 200 with the filename in the body."""
    key = "originals/aaa/photo_a.webp"
    response = await admin_client.get(f"/admin/assets/{key}")
    assert response.status_code == 200
    assert "photo_a.webp" in response.text


@pytest.mark.asyncio
async def test_detail_404(admin_client: AsyncClient) -> None:
    """GET /admin/assets/nope/missing returns 404."""
    response = await admin_client.get("/admin/assets/nope/missing")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Metadata update — POST /admin/assets/{key:path}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_metadata(admin_client: AsyncClient, mk_with_assets: MediaKit) -> None:
    """POST /admin/assets/{key} with valid form data updates catalog + redirects 303."""
    key = "originals/aaa/photo_a.webp"
    response = await admin_client.post(
        f"/admin/assets/{key}",
        content="alt_text=A+nice+photo&tags=nature%2C+landscape",
        headers={**AUTH, "Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert f"/admin/assets/{key}" in response.headers["location"]

    # Confirm the catalog was actually updated
    import json

    asset = await mk_with_assets.catalog.get_asset(key)
    assert asset is not None
    assert asset["alt_text"] == "A nice photo"
    tags = json.loads(asset["tags"])
    assert "nature" in tags
    assert "landscape" in tags


@pytest.mark.asyncio
async def test_update_metadata_unauthorized(anon_client: AsyncClient) -> None:
    """POST /admin/assets/{key} without auth returns 401."""
    key = "originals/aaa/photo_a.webp"
    response = await anon_client.post(
        f"/admin/assets/{key}",
        content="alt_text=Sneaky",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        # No Authorization header
        follow_redirects=False,
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Auth — the admin pages take the same auth as the write endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/admin", "/admin/upload", "/admin/assets/originals/aaa/photo_a.webp", "/admin/assets/nope"],
)
async def test_admin_pages_require_auth(anon_client: AsyncClient, path: str) -> None:
    """Unauthenticated GETs on the admin pages return 401, not the asset list."""
    response = await anon_client.get(path)
    assert response.status_code == 401
    assert "photo_a" not in response.text


@pytest.mark.asyncio
async def test_iiif_and_asset_reads_stay_public(anon_client: AsyncClient) -> None:
    """The site's <img> tags carry no credentials, so reads must not need them."""
    assert (await anon_client.get("/assets")).status_code == 200
    info = await anon_client.get("/iiif/originals/aaa/photo_a.webp/info.json")
    assert info.status_code == 200


# ---------------------------------------------------------------------------
# Mounted under a prefix — every link the admin renders must keep the prefix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mounted_admin_links_keep_prefix(mounted_client: AsyncClient) -> None:
    """Under /media, no rendered href/src/action points at the host's root."""
    import re

    key = "originals/aaa/photo_a.webp"
    for path in ("/media/admin", "/media/admin/upload", f"/media/admin/assets/{key}"):
        response = await mounted_client.get(path)
        assert response.status_code == 200, path
        html = response.text
        assert 'data-media-base="/media"' in html
        urls = re.findall(r'(?:href|src|action)="(/[^"]*)"', html)
        assert urls, path
        bad = [u for u in urls if not u.startswith("/media/")]
        assert not bad, f"{path}: unprefixed {bad}"


@pytest.mark.asyncio
async def test_mounted_update_redirects_under_prefix(mounted_client: AsyncClient) -> None:
    key = "originals/aaa/photo_a.webp"
    response = await mounted_client.post(
        f"/media/admin/assets/{key}",
        content="alt_text=x",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/media/admin/assets/{key}"


def test_admin_js_sends_size_and_uses_base() -> None:
    """The upload routes require ``size``; the admin script must send it, and it
    must build every request URL from the mount prefix."""
    from pathlib import Path

    import astraeus_media

    js = (Path(astraeus_media.__file__).parent / "static" / "admin.js").read_text()
    assert js.count("size: file.size") == 2  # prepare and confirm
    assert "fetch('/" not in js and 'fetch("/' not in js
    assert "BASE" in js


@pytest.mark.asyncio
async def test_browser_requests_sized_squares_not_originals(admin_client: AsyncClient) -> None:
    response = await admin_client.get("/admin")

    assert "/iiif/originals/aaa/photo_a.webp/square/256,/0/default.webp" in response.text


@pytest.mark.asyncio
async def test_detail_shows_the_whole_image_not_a_square_crop(admin_client: AsyncClient) -> None:
    response = await admin_client.get("/admin/assets/originals/aaa/photo_a.webp")

    assert "/iiif/originals/aaa/photo_a.webp/full/800,/0/default.webp" in response.text


# ---------------------------------------------------------------------------
# Signed-out redirect and the portal's app bar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_pages_redirect_to_login_when_one_is_set(
    mk_with_assets: MediaKit,
) -> None:
    """With login_url set, a signed-out visitor is sent to sign in and back."""
    mk_with_assets.config.login_url = "/api/auth/login"
    app = mk_with_assets._build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        response = await c.get("/admin/upload?picker=1")
    assert response.status_code == 302
    assert response.headers["location"] == "/api/auth/login?next=%2Fadmin%2Fupload%3Fpicker%3D1"


@pytest.mark.asyncio
async def test_writes_still_answer_401_with_a_login_url(mk_with_assets: MediaKit) -> None:
    mk_with_assets.config.login_url = "/api/auth/login"
    app = mk_with_assets._build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        response = await c.post("/admin/assets/originals/aaa/photo_a.webp", data={"alt_text": "x"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_loads_the_app_bar_when_given_a_portal(mk_with_assets: MediaKit) -> None:
    for portal_url, expected in ((None, False), ("/", True)):
        mk_with_assets.config.portal_url = portal_url
        app = mk_with_assets._build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver", headers=AUTH
        ) as c:
            response = await c.get("/admin")
        assert ('<script src="/app-bar.js" defer></script>' in response.text) is expected
