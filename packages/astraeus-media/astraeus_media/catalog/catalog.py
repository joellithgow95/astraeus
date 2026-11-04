"""Catalog — async SQLite interface for asset metadata."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

import aiosqlite

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS assets (
    key             TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL,
    bucket          TEXT NOT NULL,
    original_key    TEXT,
    filename        TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size            INTEGER NOT NULL,
    width           INTEGER,
    height          INTEGER,
    alt_text        TEXT,
    tags            TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS derivatives (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    original_key    TEXT NOT NULL REFERENCES assets(key),
    iiif_params     TEXT NOT NULL,
    derivative_key  TEXT NOT NULL,
    width           INTEGER,
    height          INTEGER,
    format          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(original_key, iiif_params)
);

CREATE TABLE IF NOT EXISTS asset_references (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_key       TEXT NOT NULL REFERENCES assets(key),
    host_model      TEXT NOT NULL,
    host_id         TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(asset_key, host_model, host_id, field_name)
);

CREATE INDEX IF NOT EXISTS idx_references_host ON asset_references(host_model, host_id);
CREATE INDEX IF NOT EXISTS idx_references_asset ON asset_references(asset_key);
"""


def _row_to_dict(row: aiosqlite.Row, description) -> dict:
    """Convert an aiosqlite row to a plain dict using cursor description."""
    return {description[i][0]: row[i] for i in range(len(description))}


class Catalog:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database connection and ensure schema exists."""
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    async def insert_asset(
        self,
        key: str,
        content_hash: str,
        bucket: str,
        filename: str,
        content_type: str,
        size: int,
        *,
        original_key: str | None = None,
        width: int | None = None,
        height: int | None = None,
        alt_text: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Insert a new asset row and return the row dict."""
        assert self._db is not None
        now = datetime.now(UTC).isoformat()
        tags_json = json.dumps(tags) if tags is not None else None
        await self._db.execute(
            """
            INSERT INTO assets
                (key, content_hash, bucket, original_key, filename,
                 content_type, size, width, height, alt_text, tags,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                content_hash,
                bucket,
                original_key,
                filename,
                content_type,
                size,
                width,
                height,
                alt_text,
                tags_json,
                now,
                now,
            ),
        )
        await self._db.commit()
        row = await self.get_asset(key)
        assert row is not None
        return row

    async def get_asset(self, key: str) -> dict | None:
        """Return the asset row dict for *key*, or ``None`` if not found."""
        assert self._db is not None
        async with self._db.execute("SELECT * FROM assets WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_dict(row, cursor.description)

    async def list_assets(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        content_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """Return a page of asset rows with optional filters.

        *content_type* filters by exact match (e.g. ``"image/webp"`` or prefix
        ``"image/"`` using SQL ``LIKE``).  *tags* filters to rows that contain
        **all** of the given tag strings in their JSON tags column.
        """
        assert self._db is not None
        conditions: list[str] = []
        params: list[object] = []

        if content_type is not None:
            if content_type.endswith("/"):
                conditions.append("content_type LIKE ?")
                params.append(f"{content_type}%")
            else:
                conditions.append("content_type = ?")
                params.append(content_type)

        if tags:
            for tag in tags:
                # Check if tag string appears anywhere in the JSON tags column
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])

        async with self._db.execute(
            f"SELECT * FROM assets {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_dict(row, cursor.description) for row in rows]

    async def update_asset(
        self,
        key: str,
        *,
        alt_text: str | None = None,
        tags: list[str] | None = None,
    ) -> dict | None:
        """Update *alt_text* and/or *tags* for the asset at *key*.

        Returns the updated row dict, or ``None`` if the key does not exist.
        """
        assert self._db is not None
        now = datetime.now(UTC).isoformat()
        updates: list[str] = ["updated_at = ?"]
        params: list[object] = [now]

        if alt_text is not None:
            updates.append("alt_text = ?")
            params.append(alt_text)
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags))

        params.append(key)
        await self._db.execute(
            f"UPDATE assets SET {', '.join(updates)} WHERE key = ?",
            params,
        )
        await self._db.commit()
        return await self.get_asset(key)

    async def delete_asset(self, key: str) -> bool:
        """Delete the asset row (cascades to derivatives/references via FK).

        Returns ``True`` if a row was deleted, ``False`` if the key did not exist.
        """
        assert self._db is not None
        # Delete references and derivatives manually (aiosqlite doesn't enforce FK by default)
        await self._db.execute("DELETE FROM asset_references WHERE asset_key = ?", (key,))
        await self._db.execute("DELETE FROM derivatives WHERE original_key = ?", (key,))
        cursor = await self._db.execute("DELETE FROM assets WHERE key = ?", (key,))
        await self._db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Derivatives
    # ------------------------------------------------------------------

    async def insert_derivative(
        self,
        original_key: str,
        iiif_params: str,
        derivative_key: str,
        *,
        width: int | None = None,
        height: int | None = None,
        format: str,
    ) -> dict:
        """Insert a derivative row and return it as a dict."""
        assert self._db is not None
        now = datetime.now(UTC).isoformat()
        cursor = await self._db.execute(
            """
            INSERT INTO derivatives
                (original_key, iiif_params, derivative_key, width, height, format, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (original_key, iiif_params, derivative_key, width, height, format, now),
        )
        await self._db.commit()
        row_id = cursor.lastrowid
        async with self._db.execute("SELECT * FROM derivatives WHERE id = ?", (row_id,)) as cur:
            row = await cur.fetchone()
            assert row is not None
            return _row_to_dict(row, cur.description)

    async def get_or_create_derivative(
        self,
        original_key: str,
        iiif_params: str,
        derivative_key: str,
        *,
        width: int | None = None,
        height: int | None = None,
        format: str,
    ) -> tuple[dict, bool]:
        """Return ``(row, created)`` — fetch existing or insert new derivative."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM derivatives WHERE original_key = ? AND iiif_params = ?",
            (original_key, iiif_params),
        ) as cursor:
            row = await cursor.fetchone()
            if row is not None:
                return _row_to_dict(row, cursor.description), False

        created_row = await self.insert_derivative(
            original_key,
            iiif_params,
            derivative_key,
            width=width,
            height=height,
            format=format,
        )
        return created_row, True

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------

    async def set_references(
        self,
        host_model: str,
        host_id: str,
        asset_keys: list[str],
    ) -> None:
        """Replace all asset references for *(host_model, host_id)*.

        Deletes existing refs then inserts new ones. Uses an empty string for
        ``field_name`` (v1 — field-level tracking is Phase 7+).
        """
        assert self._db is not None
        now = datetime.now(UTC).isoformat()
        await self._db.execute(
            "DELETE FROM asset_references WHERE host_model = ? AND host_id = ?",
            (host_model, host_id),
        )
        for asset_key in asset_keys:
            await self._db.execute(
                """
                INSERT OR IGNORE INTO asset_references
                    (asset_key, host_model, host_id, field_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (asset_key, host_model, host_id, "", now),
            )
        await self._db.commit()

    async def remove_references(self, host_model: str, host_id: str) -> None:
        """Delete all asset references for *(host_model, host_id)*."""
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM asset_references WHERE host_model = ? AND host_id = ?",
            (host_model, host_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    async def find_orphans(self) -> list[str]:
        """Return keys for assets that have no asset_references rows."""
        assert self._db is not None
        async with self._db.execute(
            """
            SELECT a.key FROM assets a
            WHERE a.key NOT IN (SELECT DISTINCT asset_key FROM asset_references)
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def export_csv(self, path: str) -> None:
        """Write the assets table to a CSV file at *path*."""
        assert self._db is not None
        async with self._db.execute("SELECT * FROM assets") as cursor:
            rows = await cursor.fetchall()
            col_names = [desc[0] for desc in cursor.description]

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(col_names)
            writer.writerows(rows)
