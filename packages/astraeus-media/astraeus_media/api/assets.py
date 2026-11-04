"""Asset CRUD routes.

Read endpoints (GET) are public; mutating endpoints (PATCH, DELETE) require auth.

Note: ``{key:path}`` captures slashes, so keys like
``originals/abc12345/photo.webp`` work without encoding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

if TYPE_CHECKING:
    from astraeus_media.app import MediaKit

logger = structlog.get_logger(__name__)


def make_asset_routes(mk: MediaKit) -> list[Route]:
    """Return the asset CRUD route list for *mk*."""

    async def list_assets(request: Request) -> JSONResponse:
        """``GET /assets`` — list assets with optional filters."""
        params = request.query_params
        try:
            limit = int(params.get("limit", 50))
            offset = int(params.get("offset", 0))
        except ValueError:
            return JSONResponse({"error": "limit and offset must be integers"}, status_code=422)

        content_type = params.get("content_type") or None
        tags_param = params.get("tags")
        tags = [t.strip() for t in tags_param.split(",")] if tags_param else None

        assets = await mk.catalog.list_assets(
            limit=limit,
            offset=offset,
            content_type=content_type,
            tags=tags,
        )
        return JSONResponse({"assets": assets, "limit": limit, "offset": offset})

    async def get_asset(request: Request) -> JSONResponse:
        """``GET /assets/{key:path}`` — get one asset plus a presigned URL."""
        key = request.path_params["key"]
        asset = await mk.catalog.get_asset(key)
        if asset is None:
            return JSONResponse({"error": "Asset not found"}, status_code=404)

        url = await mk.storage.get_url(key, mk.config.presign_expires)
        return JSONResponse({**asset, "url": url})

    async def patch_asset(request: Request) -> JSONResponse:
        """``PATCH /assets/{key:path}`` — update alt_text and/or tags."""
        from astraeus_media.auth import require_auth

        if (err := await require_auth(request, mk)) is not None:
            return err

        key = request.path_params["key"]

        try:
            body = await request.json()
        except Exception:
            logger.warning("astraeus_media.assets.invalid_json", key=key, endpoint="patch")
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        alt_text = body.get("alt_text")
        tags = body.get("tags")

        updated = await mk.catalog.update_asset(key, alt_text=alt_text, tags=tags)
        if updated is None:
            return JSONResponse({"error": "Asset not found"}, status_code=404)
        return JSONResponse(updated)

    async def delete_asset(request: Request) -> Response:
        """``DELETE /assets/{key:path}`` — delete from bucket and catalog."""
        from astraeus_media.auth import require_auth

        if (err := await require_auth(request, mk)) is not None:
            return err

        key = request.path_params["key"]

        # Check asset exists in catalog first
        asset = await mk.catalog.get_asset(key)
        if asset is None:
            return JSONResponse({"error": "Asset not found"}, status_code=404)

        # Renditions are separate objects (possibly public): remove them with the original.
        for rendition in await mk.derivative_storage.list_objects(prefix=f"derivatives/{key}/"):
            await mk.derivative_storage.delete(rendition["key"])
        await mk.storage.delete(key)
        await mk.catalog.delete_asset(key)
        return Response(status_code=204)

    return [
        Route("/assets", endpoint=list_assets, methods=["GET"]),
        Route("/assets/{key:path}", endpoint=get_asset, methods=["GET"]),
        Route("/assets/{key:path}", endpoint=patch_asset, methods=["PATCH"]),
        Route("/assets/{key:path}", endpoint=delete_asset, methods=["DELETE"]),
    ]
