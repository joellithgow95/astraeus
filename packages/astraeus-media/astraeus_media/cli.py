"""
CLI entry point — ``astraeus-media`` with ``mcp serve``, ``sync``, ``gc`` and ``export``

Examples::

    # Start the MCP server connected to a local dev instance
    astraeus-media mcp serve --url http://localhost:8000/media

    # Connect to production with an API key
    astraeus-media mcp serve --url https://mysite.com/media --api-key secret

    # Reconcile bucket with catalog (dry-run preview)
    astraeus-media sync --catalog ./media.db --dry-run

    # Remove orphaned assets
    astraeus-media gc --catalog ./media.db

    # Export catalog to CSV
    astraeus-media export --catalog ./media.db --output export.csv
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
def main():
    """astraeus-media management commands."""


# ---------------------------------------------------------------------------
# mcp group
# ---------------------------------------------------------------------------


@main.group()
def mcp():
    """MCP server commands (requires astraeus-media[mcp])."""


@mcp.command("serve")
@click.option(
    "--url",
    required=True,
    help="Base URL of the astraeus-media instance (e.g. https://mysite.com/media).",
)
@click.option(
    "--api-key",
    default=None,
    envvar="ASTRAEUS_MEDIA_API_KEY",
    show_default="ASTRAEUS_MEDIA_API_KEY env var",
    help="API key for Authorization: Bearer header.",
)
@click.option(
    "--transport",
    default="stdio",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    show_default=True,
    help="MCP transport protocol.",
)
def mcp_serve(url: str, api_key: str | None, transport: str) -> None:
    """
    Start the astraeus-media MCP server.

    Connects to the astraeus-media instance at --url and exposes its asset API
    as agent-callable tools.  Run locally; point at a deployed instance
    over HTTP.

    Examples::

        # Connect to a local dev server
        astraeus-media mcp serve --url http://localhost:8000/media

        # Connect to production with an API key
        astraeus-media mcp serve --url https://mysite.com/media --api-key secret
    """
    try:
        from astraeus_media.mcp.server import build_mcp_server
    except ImportError:
        raise click.ClickException(
            "MCP server requires the 'mcp' extra. Install with: pip install astraeus-media[mcp]"
        )

    server = build_mcp_server(base_url=url.rstrip("/"), api_key=api_key)
    server.run(transport=transport)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_with_catalog(catalog_path: str, coro):
    """Open a Catalog at *catalog_path*, run *coro(catalog)*, then close it."""
    from astraeus_media.catalog.catalog import Catalog

    async def _inner():
        catalog = Catalog(catalog_path)
        await catalog.initialize()
        try:
            await coro(catalog)
        finally:
            await catalog.close()

    asyncio.run(_inner())


def _run_with_mk(catalog_path: str, coro):
    """Construct a full MediaKit (storage from env), run *coro(mk)*, then close catalog."""
    from astraeus_media.app import MediaKit
    from astraeus_media.config import MediakitConfig

    async def _inner():
        config = MediakitConfig(
            bucket="",  # will be overridden by env var below if set
            catalog_path=catalog_path,
        )
        mk = MediaKit(config=config)
        async with mk.lifespan_context(None):
            await coro(mk)

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# sync command
# ---------------------------------------------------------------------------


@main.command("sync")
@click.option(
    "--catalog",
    "catalog_path",
    default="./media_catalog.db",
    show_default=True,
    envvar="ASTRAEUS_MEDIA_CATALOG_PATH",
    help="Path to the SQLite catalog file.",
)
@click.option(
    "--bucket",
    default=None,
    envvar="ASTRAEUS_MEDIA_BUCKET",
    help="S3 bucket name (overrides ASTRAEUS_MEDIA_BUCKET env var).",
)
@click.option(
    "--endpoint-url",
    default=None,
    envvar="ASTRAEUS_MEDIA_ENDPOINT_URL",
    help="S3-compatible endpoint URL (for R2, MinIO, etc.).",
)
@click.option(
    "--access-key-id",
    default=None,
    envvar="ASTRAEUS_MEDIA_ACCESS_KEY_ID",
    help="AWS / S3-compatible access key ID.",
)
@click.option(
    "--secret-access-key",
    default=None,
    envvar="ASTRAEUS_MEDIA_SECRET_ACCESS_KEY",
    help="AWS / S3-compatible secret access key.",
)
@click.option("--prefix", default="", show_default=True, help="Key prefix to scan.")
@click.option(
    "--max-keys",
    default=1000,
    show_default=True,
    help="Maximum number of objects to list from storage.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview changes without writing to the catalog.",
)
def sync_cmd(
    catalog_path: str,
    bucket: str | None,
    endpoint_url: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    prefix: str,
    max_keys: int,
    dry_run: bool,
) -> None:
    """Reconcile the storage bucket with the catalog.

    Lists all objects in storage and inserts any keys that are missing from
    the catalog.  Existing entries are left unchanged.

    Examples::

        # Dry-run against a local dev catalog
        astraeus-media sync --catalog ./media.db --dry-run

        # Full sync using environment variables for storage credentials
        ASTRAEUS_MEDIA_BUCKET=my-bucket astraeus-media sync --catalog ./media.db
    """
    from astraeus_media.catalog.catalog import Catalog
    from astraeus_media.config import MediakitConfig
    from astraeus_media.storage.s3_compatible import S3CompatibleBackend

    async def _sync():
        # Build storage
        config = MediakitConfig(
            bucket=bucket or "",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
        storage = S3CompatibleBackend(config)

        # Open catalog
        catalog = Catalog(catalog_path)
        await catalog.initialize()
        try:
            objects = await storage.list_objects(prefix=prefix, max_keys=max_keys)

            new_keys: list[str] = []
            for obj in objects:
                key: str = obj["key"]
                existing = await catalog.get_asset(key)
                if existing is None:
                    new_keys.append(key)

            if dry_run:
                for key in new_keys:
                    click.echo(f"  + {key}")
                click.echo(
                    f"Dry-run: {len(new_keys)} new, "
                    f"{len(objects) - len(new_keys)} already present"
                )
            else:
                for obj in objects:
                    key = obj["key"]
                    existing = await catalog.get_asset(key)
                    if existing is None:
                        content_type = (
                            mimetypes.guess_type(key)[0] or "application/octet-stream"
                        )
                        await catalog.insert_asset(
                            key=key,
                            content_hash="",
                            bucket=bucket or "",
                            filename=Path(key).name,
                            content_type=content_type,
                            size=obj.get("size", 0),
                        )
                click.echo(
                    f"Synced: {len(new_keys)} new, "
                    f"{len(objects) - len(new_keys)} already present"
                )
        finally:
            await catalog.close()

    asyncio.run(_sync())


# ---------------------------------------------------------------------------
# gc command
# ---------------------------------------------------------------------------


@main.command("gc")
@click.option(
    "--catalog",
    "catalog_path",
    default="./media_catalog.db",
    show_default=True,
    envvar="ASTRAEUS_MEDIA_CATALOG_PATH",
    help="Path to the SQLite catalog file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview orphans without deleting.",
)
def gc_cmd(catalog_path: str, dry_run: bool) -> None:
    """Remove orphaned assets from the catalog.

    An orphan is an asset row that has no entries in the ``asset_references``
    table.  Deleting it also removes its derivative rows.  Storage-side keys
    are **not** deleted (storage GC is a future phase).

    Examples::

        # Preview which assets would be removed
        astraeus-media gc --catalog ./media.db --dry-run

        # Actually remove orphans
        astraeus-media gc --catalog ./media.db
    """

    async def _gc():
        from astraeus_media.catalog.catalog import Catalog

        catalog = Catalog(catalog_path)
        await catalog.initialize()
        try:
            orphans = await catalog.find_orphans()
            if not orphans:
                click.echo("No orphans found.")
                return

            if dry_run:
                for key in orphans:
                    click.echo(f"  - {key}")
                click.echo(f"Dry-run: {len(orphans)} orphaned asset(s) would be removed")
            else:
                for key in orphans:
                    await catalog.delete_asset(key)
                click.echo(f"Removed {len(orphans)} orphaned asset(s)")
        finally:
            await catalog.close()

    asyncio.run(_gc())


# ---------------------------------------------------------------------------
# export command
# ---------------------------------------------------------------------------


@main.command("export")
@click.option(
    "--catalog",
    "catalog_path",
    default="./media_catalog.db",
    show_default=True,
    envvar="ASTRAEUS_MEDIA_CATALOG_PATH",
    help="Path to the SQLite catalog file.",
)
@click.option(
    "--output",
    "output_path",
    default=None,
    help="Destination CSV file (default: astraeus-media-export.csv in current directory).",
)
def export_cmd(catalog_path: str, output_path: str | None) -> None:
    """Export the asset catalog to a CSV file.

    Writes every row in the ``assets`` table to *--output*.  Useful for
    audits, backups, or feeding data into external tooling.

    Examples::

        # Export to default filename
        astraeus-media export --catalog ./media.db

        # Export to a specific path
        astraeus-media export --catalog ./media.db --output /tmp/assets.csv
    """
    resolved_output = output_path or "astraeus-media-export.csv"

    async def _export():
        from astraeus_media.catalog.catalog import Catalog

        catalog = Catalog(catalog_path)
        await catalog.initialize()
        try:
            await catalog.export_csv(resolved_output)
            click.echo(f"Exported catalog to {resolved_output}")
        finally:
            await catalog.close()

    asyncio.run(_export())
