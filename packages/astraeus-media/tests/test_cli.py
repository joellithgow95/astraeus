"""Tests for astraeus-media CLI commands: sync, gc, export."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from unittest.mock import AsyncMock, patch

from astraeus_media.catalog.catalog import Catalog
from astraeus_media.cli import main
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_catalog(db_path: str, key: str = "originals/abc/photo.webp") -> None:
    """Synchronously insert one asset into the catalog at *db_path*."""

    async def _insert():
        catalog = Catalog(db_path)
        await catalog.initialize()
        await catalog.insert_asset(
            key=key,
            content_hash="deadbeef",
            bucket="test-bucket",
            filename="photo.webp",
            content_type="image/webp",
            size=1024,
        )
        await catalog.close()

    asyncio.run(_insert())


def _add_reference(db_path: str, key: str = "originals/abc/photo.webp") -> None:
    """Attach a reference to *key* so it is no longer an orphan."""

    async def _ref():
        catalog = Catalog(db_path)
        await catalog.initialize()
        await catalog.set_references("Article", "1", [key])
        await catalog.close()

    asyncio.run(_ref())


# ---------------------------------------------------------------------------
# export tests
# ---------------------------------------------------------------------------


def test_help_shows_commands():
    """Root --help lists sync, gc, and export."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "sync" in result.output
    assert "gc" in result.output
    assert "export" in result.output


def test_export_creates_csv_empty(tmp_path):
    """Empty catalog → CSV with header only (one row)."""
    db = str(tmp_path / "mk.db")
    out = str(tmp_path / "out.csv")
    runner = CliRunner()
    result = runner.invoke(main, ["export", "--catalog", db, "--output", out])
    assert result.exit_code == 0, result.output
    assert Path(out).exists()
    with open(out, newline="") as f:
        rows = list(csv.reader(f))
    # Only the header row — no data rows
    assert len(rows) == 1
    assert "key" in rows[0]


def test_export_creates_csv_with_data(tmp_path):
    """Catalog with one asset → CSV has header + one data row."""
    db = str(tmp_path / "mk.db")
    out = str(tmp_path / "out.csv")
    _seed_catalog(db)
    runner = CliRunner()
    result = runner.invoke(main, ["export", "--catalog", db, "--output", out])
    assert result.exit_code == 0, result.output
    with open(out, newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2  # header + 1 data row
    # key column should contain our asset key
    header = rows[0]
    key_col = header.index("key")
    assert rows[1][key_col] == "originals/abc/photo.webp"


def test_export_default_output(tmp_path):
    """No --output flag → creates astraeus-media-export.csv in the invoked directory."""
    db = str(tmp_path / "mk.db")
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["export", "--catalog", db])
        assert result.exit_code == 0, result.output
        assert Path("astraeus-media-export.csv").exists()


# ---------------------------------------------------------------------------
# gc tests
# ---------------------------------------------------------------------------


def test_gc_no_orphans(tmp_path):
    """Catalog with no orphans → 'No orphans found', exit 0."""
    db = str(tmp_path / "mk.db")
    _seed_catalog(db)
    _add_reference(db)  # asset now has a reference → not an orphan
    runner = CliRunner()
    result = runner.invoke(main, ["gc", "--catalog", db])
    assert result.exit_code == 0, result.output
    assert "No orphans found" in result.output


def test_gc_dry_run_lists_orphans(tmp_path):
    """Catalog with one orphan + --dry-run → prints key, no deletion."""
    db = str(tmp_path / "mk.db")
    key = "originals/abc/photo.webp"
    _seed_catalog(db, key=key)
    runner = CliRunner()
    result = runner.invoke(main, ["gc", "--catalog", db, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert key in result.output
    assert "Dry-run" in result.output

    # Asset should still be in the catalog
    async def _check():
        catalog = Catalog(db)
        await catalog.initialize()
        asset = await catalog.get_asset(key)
        await catalog.close()
        return asset

    assert asyncio.run(_check()) is not None


def test_gc_removes_orphans(tmp_path):
    """Catalog with one orphan → asset deleted, exit 0."""
    db = str(tmp_path / "mk.db")
    key = "originals/abc/photo.webp"
    _seed_catalog(db, key=key)
    runner = CliRunner()
    result = runner.invoke(main, ["gc", "--catalog", db])
    assert result.exit_code == 0, result.output
    assert "Removed 1" in result.output

    async def _check():
        catalog = Catalog(db)
        await catalog.initialize()
        asset = await catalog.get_asset(key)
        await catalog.close()
        return asset

    assert asyncio.run(_check()) is None


# ---------------------------------------------------------------------------
# sync tests
# ---------------------------------------------------------------------------

# FakeStorage list_objects return values
_ONE_OBJECT = [{"key": "originals/xyz/img.jpg", "size": 2048, "last_modified": "2024-01-01"}]


def test_sync_dry_run(tmp_path):
    """Storage has 1 key, catalog empty → --dry-run prints '1 new', no insert."""
    db = str(tmp_path / "mk.db")
    runner = CliRunner()

    with patch(
        "astraeus_media.storage.s3_compatible.S3CompatibleBackend.list_objects",
        new=AsyncMock(return_value=_ONE_OBJECT),
    ):
        result = runner.invoke(
            main,
            ["sync", "--catalog", db, "--dry-run", "--bucket", "test-bucket"],
        )

    assert result.exit_code == 0, result.output
    assert "1 new" in result.output

    # No asset should have been inserted
    async def _check():
        catalog = Catalog(db)
        await catalog.initialize()
        assets = await catalog.list_assets()
        await catalog.close()
        return assets

    assert asyncio.run(_check()) == []


def test_sync_inserts_missing(tmp_path):
    """Storage has 1 key, catalog empty → asset inserted, exit 0."""
    db = str(tmp_path / "mk.db")
    runner = CliRunner()

    with patch(
        "astraeus_media.storage.s3_compatible.S3CompatibleBackend.list_objects",
        new=AsyncMock(return_value=_ONE_OBJECT),
    ):
        result = runner.invoke(
            main,
            ["sync", "--catalog", db, "--bucket", "test-bucket"],
        )

    assert result.exit_code == 0, result.output
    assert "1 new" in result.output

    async def _check():
        catalog = Catalog(db)
        await catalog.initialize()
        assets = await catalog.list_assets()
        await catalog.close()
        return assets

    assets = asyncio.run(_check())
    assert len(assets) == 1
    assert assets[0]["key"] == "originals/xyz/img.jpg"
    assert assets[0]["content_type"] == "image/jpeg"


def test_sync_skips_existing(tmp_path):
    """Storage and catalog both have the same key → '0 new', no duplicate insert."""
    db = str(tmp_path / "mk.db")
    key = "originals/xyz/img.jpg"
    _seed_catalog(db, key=key)
    runner = CliRunner()

    with patch(
        "astraeus_media.storage.s3_compatible.S3CompatibleBackend.list_objects",
        new=AsyncMock(return_value=[{"key": key, "size": 1024, "last_modified": "2024-01-01"}]),
    ):
        result = runner.invoke(
            main,
            ["sync", "--catalog", db, "--bucket", "test-bucket"],
        )

    assert result.exit_code == 0, result.output
    assert "0 new" in result.output

    async def _check():
        catalog = Catalog(db)
        await catalog.initialize()
        assets = await catalog.list_assets()
        await catalog.close()
        return assets

    assets = asyncio.run(_check())
    assert len(assets) == 1  # still only the original
