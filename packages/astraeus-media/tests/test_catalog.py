"""Unit tests for all Catalog CRUD methods against a real temp SQLite file."""

from __future__ import annotations

import os

import pytest
from astraeus_media.catalog.catalog import Catalog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_asset(key: str = "originals/abc12345/photo.webp") -> dict:
    return {
        "key": key,
        "content_hash": "deadbeef",
        "bucket": "test-bucket",
        "filename": "photo.webp",
        "content_type": "image/webp",
        "size": 102400,
    }


# ---------------------------------------------------------------------------
# insert_asset / get_asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_and_get_asset(catalog: Catalog) -> None:
    s = _sample_asset()
    row = await catalog.insert_asset(**s)
    assert row["key"] == s["key"]
    assert row["filename"] == "photo.webp"
    assert row["size"] == 102400

    fetched = await catalog.get_asset(s["key"])
    assert fetched is not None
    assert fetched["key"] == s["key"]


@pytest.mark.asyncio
async def test_get_asset_not_found(catalog: Catalog) -> None:
    result = await catalog.get_asset("originals/nonexistent/file.webp")
    assert result is None


@pytest.mark.asyncio
async def test_insert_asset_with_optional_fields(catalog: Catalog) -> None:
    row = await catalog.insert_asset(
        key="originals/xyz/photo.webp",
        content_hash="abc123",
        bucket="test-bucket",
        filename="photo.webp",
        content_type="image/webp",
        size=512,
        width=1920,
        height=1080,
        alt_text="A lovely photo",
        tags=["landscape", "nature"],
    )
    assert row["width"] == 1920
    assert row["height"] == 1080
    assert row["alt_text"] == "A lovely photo"
    # tags stored as JSON — raw value is a JSON string
    import json

    assert json.loads(row["tags"]) == ["landscape", "nature"]


# ---------------------------------------------------------------------------
# list_assets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_assets_empty(catalog: Catalog) -> None:
    results = await catalog.list_assets()
    assert results == []


@pytest.mark.asyncio
async def test_list_assets_multiple(catalog: Catalog) -> None:
    for i in range(3):
        await catalog.insert_asset(
            key=f"originals/{i:03d}/photo.webp",
            content_hash=f"hash{i}",
            bucket="b",
            filename=f"photo{i}.webp",
            content_type="image/webp",
            size=100 + i,
        )
    results = await catalog.list_assets()
    assert len(results) == 3


@pytest.mark.asyncio
async def test_list_assets_limit_offset(catalog: Catalog) -> None:
    for i in range(5):
        await catalog.insert_asset(
            key=f"originals/{i:03d}/f.webp",
            content_hash=f"h{i}",
            bucket="b",
            filename=f"f{i}.webp",
            content_type="image/webp",
            size=100,
        )
    page1 = await catalog.list_assets(limit=2, offset=0)
    page2 = await catalog.list_assets(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {r["key"] for r in page1}.isdisjoint({r["key"] for r in page2})


@pytest.mark.asyncio
async def test_list_assets_filter_content_type(catalog: Catalog) -> None:
    await catalog.insert_asset(
        key="originals/a/img.webp",
        content_hash="h1",
        bucket="b",
        filename="img.webp",
        content_type="image/webp",
        size=100,
    )
    await catalog.insert_asset(
        key="originals/b/doc.pdf",
        content_hash="h2",
        bucket="b",
        filename="doc.pdf",
        content_type="application/pdf",
        size=200,
    )
    results = await catalog.list_assets(content_type="image/webp")
    assert len(results) == 1
    assert results[0]["content_type"] == "image/webp"


@pytest.mark.asyncio
async def test_list_assets_filter_tags(catalog: Catalog) -> None:
    await catalog.insert_asset(
        key="originals/a/img.webp",
        content_hash="h1",
        bucket="b",
        filename="img.webp",
        content_type="image/webp",
        size=100,
        tags=["hero", "banner"],
    )
    await catalog.insert_asset(
        key="originals/b/img2.webp",
        content_hash="h2",
        bucket="b",
        filename="img2.webp",
        content_type="image/webp",
        size=100,
        tags=["thumbnail"],
    )
    results = await catalog.list_assets(tags=["hero"])
    assert len(results) == 1
    assert results[0]["key"] == "originals/a/img.webp"


# ---------------------------------------------------------------------------
# update_asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_asset_alt_text(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    updated = await catalog.update_asset("originals/abc12345/photo.webp", alt_text="New alt")
    assert updated is not None
    assert updated["alt_text"] == "New alt"


@pytest.mark.asyncio
async def test_update_asset_tags(catalog: Catalog) -> None:
    import json

    await catalog.insert_asset(**_sample_asset())
    updated = await catalog.update_asset("originals/abc12345/photo.webp", tags=["foo", "bar"])
    assert updated is not None
    assert json.loads(updated["tags"]) == ["foo", "bar"]


@pytest.mark.asyncio
async def test_update_asset_not_found(catalog: Catalog) -> None:
    result = await catalog.update_asset("nonexistent/key.webp", alt_text="x")
    assert result is None


# ---------------------------------------------------------------------------
# delete_asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_asset(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    deleted = await catalog.delete_asset("originals/abc12345/photo.webp")
    assert deleted is True

    fetched = await catalog.get_asset("originals/abc12345/photo.webp")
    assert fetched is None


@pytest.mark.asyncio
async def test_delete_asset_not_found(catalog: Catalog) -> None:
    result = await catalog.delete_asset("originals/nope/img.webp")
    assert result is False


# ---------------------------------------------------------------------------
# Derivatives
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_derivative(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    row = await catalog.insert_derivative(
        original_key="originals/abc12345/photo.webp",
        iiif_params="full/800,/0/default.jpg",
        derivative_key="derivatives/abc12345/photo_800.jpg",
        width=800,
        height=600,
        format="jpg",
    )
    assert row["original_key"] == "originals/abc12345/photo.webp"
    assert row["width"] == 800
    assert row["format"] == "jpg"


@pytest.mark.asyncio
async def test_get_or_create_derivative_creates(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    row, created = await catalog.get_or_create_derivative(
        original_key="originals/abc12345/photo.webp",
        iiif_params="full/400,/0/default.webp",
        derivative_key="derivatives/abc12345/photo_400.webp",
        width=400,
        height=300,
        format="webp",
    )
    assert created is True
    assert row["derivative_key"] == "derivatives/abc12345/photo_400.webp"


@pytest.mark.asyncio
async def test_get_or_create_derivative_returns_existing(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    row1, c1 = await catalog.get_or_create_derivative(
        original_key="originals/abc12345/photo.webp",
        iiif_params="full/400,/0/default.webp",
        derivative_key="derivatives/abc12345/photo_400.webp",
        format="webp",
    )
    row2, c2 = await catalog.get_or_create_derivative(
        original_key="originals/abc12345/photo.webp",
        iiif_params="full/400,/0/default.webp",
        derivative_key="derivatives/abc12345/photo_400.webp",
        format="webp",
    )
    assert c1 is True
    assert c2 is False
    assert row1["id"] == row2["id"]


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_references(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    await catalog.set_references("BlogPost", "post-1", ["originals/abc12345/photo.webp"])
    # No error means success; verify via find_orphans (no orphans now)
    orphans = await catalog.find_orphans()
    assert "originals/abc12345/photo.webp" not in orphans


@pytest.mark.asyncio
async def test_set_references_replaces_existing(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    await catalog.insert_asset(
        key="originals/xyz/other.webp",
        content_hash="otherhash",
        bucket="b",
        filename="other.webp",
        content_type="image/webp",
        size=50,
    )

    await catalog.set_references("BlogPost", "post-1", ["originals/abc12345/photo.webp"])
    # Replace with different key
    await catalog.set_references("BlogPost", "post-1", ["originals/xyz/other.webp"])

    # photo.webp is now an orphan (no longer referenced by post-1)
    orphans = await catalog.find_orphans()
    assert "originals/abc12345/photo.webp" in orphans
    assert "originals/xyz/other.webp" not in orphans


@pytest.mark.asyncio
async def test_remove_references(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    await catalog.set_references("BlogPost", "post-1", ["originals/abc12345/photo.webp"])
    await catalog.remove_references("BlogPost", "post-1")

    orphans = await catalog.find_orphans()
    assert "originals/abc12345/photo.webp" in orphans


# ---------------------------------------------------------------------------
# find_orphans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_orphans_all_orphans(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    orphans = await catalog.find_orphans()
    assert "originals/abc12345/photo.webp" in orphans


@pytest.mark.asyncio
async def test_find_orphans_none_when_referenced(catalog: Catalog) -> None:
    await catalog.insert_asset(**_sample_asset())
    await catalog.set_references("M", "1", ["originals/abc12345/photo.webp"])
    orphans = await catalog.find_orphans()
    assert "originals/abc12345/photo.webp" not in orphans


# ---------------------------------------------------------------------------
# export_csv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_csv(catalog: Catalog, tmp_path) -> None:
    import csv

    await catalog.insert_asset(**_sample_asset())
    out_path = str(tmp_path / "export.csv")
    await catalog.export_csv(out_path)

    assert os.path.exists(out_path)
    with open(out_path) as f:
        rows = list(csv.reader(f))
    # header + 1 data row
    assert len(rows) == 2
    assert rows[0][0] == "key"  # first column header
    assert rows[1][0] == "originals/abc12345/photo.webp"


@pytest.mark.asyncio
async def test_export_csv_empty(catalog: Catalog, tmp_path) -> None:
    import csv

    out_path = str(tmp_path / "empty.csv")
    await catalog.export_csv(out_path)
    with open(out_path) as f:
        rows = list(csv.reader(f))
    # header only
    assert len(rows) == 1
