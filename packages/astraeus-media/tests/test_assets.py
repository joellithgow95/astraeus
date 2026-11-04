"""HTTP tests for asset CRUD endpoints."""

from __future__ import annotations

import pytest
from astraeus_media.app import MediaKit
from httpx import AsyncClient

AUTH = {"Authorization": "Bearer test-secret"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_asset(mk: MediaKit, key: str = "originals/abc12345/photo.webp") -> dict:
    """Insert a test asset directly via catalog."""
    return await mk.catalog.insert_asset(
        key=key,
        content_hash="deadbeef",
        bucket="test-bucket",
        filename="photo.webp",
        content_type="image/webp",
        size=102400,
        alt_text="A photo",
        tags=["hero"],
    )


# ---------------------------------------------------------------------------
# GET /assets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_assets_empty(client: AsyncClient) -> None:
    resp = await client.get("/assets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["assets"] == []


@pytest.mark.asyncio
async def test_list_assets_returns_items(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk)
    resp = await client.get("/assets")
    assert resp.status_code == 200
    assert len(resp.json()["assets"]) == 1


@pytest.mark.asyncio
async def test_list_assets_pagination(client: AsyncClient, mk: MediaKit) -> None:
    for i in range(5):
        await mk.catalog.insert_asset(
            key=f"originals/{i:03d}/img.webp",
            content_hash=f"h{i}",
            bucket="b",
            filename=f"img{i}.webp",
            content_type="image/webp",
            size=100,
        )
    resp = await client.get("/assets?limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()["assets"]) == 2


@pytest.mark.asyncio
async def test_list_assets_filter_content_type(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk, key="originals/a/img.webp")
    await mk.catalog.insert_asset(
        key="originals/b/doc.pdf",
        content_hash="h2",
        bucket="b",
        filename="doc.pdf",
        content_type="application/pdf",
        size=200,
    )
    resp = await client.get("/assets?content_type=image/webp")
    assert resp.status_code == 200
    assert len(resp.json()["assets"]) == 1
    assert resp.json()["assets"][0]["content_type"] == "image/webp"


@pytest.mark.asyncio
async def test_list_assets_no_auth_required(client: AsyncClient) -> None:
    """GET /assets is public."""
    resp = await client.get("/assets")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /assets/{key:path}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_asset_success(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk)
    resp = await client.get("/assets/originals/abc12345/photo.webp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "originals/abc12345/photo.webp"
    assert "url" in data


@pytest.mark.asyncio
async def test_get_asset_not_found(client: AsyncClient) -> None:
    resp = await client.get("/assets/originals/nonexistent/file.webp")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_asset_no_auth_required(client: AsyncClient, mk: MediaKit) -> None:
    """GET /assets/{key} is public."""
    await _seed_asset(mk)
    resp = await client.get("/assets/originals/abc12345/photo.webp")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PATCH /assets/{key:path}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_asset_alt_text(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk)
    resp = await client.patch(
        "/assets/originals/abc12345/photo.webp",
        json={"alt_text": "Updated alt text"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["alt_text"] == "Updated alt text"


@pytest.mark.asyncio
async def test_patch_asset_tags(client: AsyncClient, mk: MediaKit) -> None:
    import json

    await _seed_asset(mk)
    resp = await client.patch(
        "/assets/originals/abc12345/photo.webp",
        json={"tags": ["landscape", "nature"]},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert json.loads(resp.json()["tags"]) == ["landscape", "nature"]


@pytest.mark.asyncio
async def test_patch_asset_unauthorized(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk)
    resp = await client.patch(
        "/assets/originals/abc12345/photo.webp",
        json={"alt_text": "x"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_patch_asset_not_found(client: AsyncClient) -> None:
    resp = await client.patch(
        "/assets/originals/nonexistent/file.webp",
        json={"alt_text": "x"},
        headers=AUTH,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /assets/{key:path}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_asset_success(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk)
    resp = await client.delete(
        "/assets/originals/abc12345/photo.webp",
        headers=AUTH,
    )
    assert resp.status_code == 204

    # Verify gone from catalog
    get_resp = await client.get("/assets/originals/abc12345/photo.webp")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_asset_unauthorized(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk)
    resp = await client.delete("/assets/originals/abc12345/photo.webp")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_asset_not_found(client: AsyncClient) -> None:
    resp = await client.delete(
        "/assets/originals/nonexistent/file.webp",
        headers=AUTH,
    )
    assert resp.status_code == 404
