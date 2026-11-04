"""HTTP tests for the asset references endpoints."""

from __future__ import annotations

import json as _json

import pytest
from astraeus_media.app import MediaKit
from httpx import AsyncClient


def _delete_with_body(client: AsyncClient, url: str, *, body: dict, headers: dict | None = None):
    """httpx.AsyncClient.delete() doesn't accept body params; use request() instead."""
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    return client.request("DELETE", url, content=_json.dumps(body), headers=all_headers)


AUTH = {"Authorization": "Bearer test-secret"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_asset(mk: MediaKit, key: str = "originals/abc12345/photo.webp") -> dict:
    return await mk.catalog.insert_asset(
        key=key,
        content_hash="hash123",
        bucket="test-bucket",
        filename="photo.webp",
        content_type="image/webp",
        size=1024,
    )


# ---------------------------------------------------------------------------
# POST /references
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_references_success(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk)
    resp = await client.post(
        "/references",
        json={
            "host_model": "BlogPost",
            "host_id": "post-1",
            "asset_keys": ["originals/abc12345/photo.webp"],
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_set_references_empty_keys(client: AsyncClient, mk: MediaKit) -> None:
    resp = await client.post(
        "/references",
        json={"host_model": "BlogPost", "host_id": "post-1", "asset_keys": []},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


@pytest.mark.asyncio
async def test_set_references_replaces_existing(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk, key="originals/a/img.webp")
    await _seed_asset(mk, key="originals/b/img.webp")

    await client.post(
        "/references",
        json={
            "host_model": "BlogPost",
            "host_id": "post-2",
            "asset_keys": ["originals/a/img.webp"],
        },
        headers=AUTH,
    )

    # Replace with second asset
    resp = await client.post(
        "/references",
        json={
            "host_model": "BlogPost",
            "host_id": "post-2",
            "asset_keys": ["originals/b/img.webp"],
        },
        headers=AUTH,
    )
    assert resp.status_code == 200

    # Verify first is now an orphan
    orphans = await mk.catalog.find_orphans()
    assert "originals/a/img.webp" in orphans


@pytest.mark.asyncio
async def test_set_references_unauthorized(client: AsyncClient) -> None:
    resp = await client.post(
        "/references",
        json={"host_model": "M", "host_id": "1", "asset_keys": []},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_set_references_missing_fields(client: AsyncClient) -> None:
    resp = await client.post(
        "/references",
        json={"host_model": "M"},
        headers=AUTH,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_set_references_invalid_asset_keys_type(client: AsyncClient) -> None:
    resp = await client.post(
        "/references",
        json={"host_model": "M", "host_id": "1", "asset_keys": "not-a-list"},
        headers=AUTH,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /references
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_references_success(client: AsyncClient, mk: MediaKit) -> None:
    await _seed_asset(mk)
    # Set first
    await client.post(
        "/references",
        json={
            "host_model": "BlogPost",
            "host_id": "post-3",
            "asset_keys": ["originals/abc12345/photo.webp"],
        },
        headers=AUTH,
    )
    # Now remove
    resp = await _delete_with_body(
        client,
        "/references",
        body={"host_model": "BlogPost", "host_id": "post-3"},
        headers=AUTH,
    )
    assert resp.status_code == 204

    # Asset is now orphan
    orphans = await mk.catalog.find_orphans()
    assert "originals/abc12345/photo.webp" in orphans


@pytest.mark.asyncio
async def test_remove_references_idempotent(client: AsyncClient) -> None:
    """Removing refs for a non-existent host silently succeeds."""
    resp = await _delete_with_body(
        client,
        "/references",
        body={"host_model": "Never", "host_id": "existed"},
        headers=AUTH,
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_remove_references_unauthorized(client: AsyncClient) -> None:
    resp = await _delete_with_body(
        client,
        "/references",
        body={"host_model": "M", "host_id": "1"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_remove_references_missing_fields(client: AsyncClient) -> None:
    resp = await _delete_with_body(
        client,
        "/references",
        body={"host_model": "M"},
        headers=AUTH,
    )
    assert resp.status_code == 422
