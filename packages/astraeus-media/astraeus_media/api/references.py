"""Asset-reference routes — track which content records use which assets.

Both endpoints require auth. References are identified by (host_model, host_id)
pairs — e.g. ``("BlogPost", "post-123")``.
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


def make_reference_routes(mk: MediaKit) -> list[Route]:
    """Return the references route list for *mk*."""

    async def set_references(request: Request) -> JSONResponse:
        """``POST /references`` — upsert all asset refs for a content record.

        Body: ``{ "host_model": str, "host_id": str, "asset_keys": [str] }``
        """
        from astraeus_media.auth import require_auth

        if (err := await require_auth(request, mk)) is not None:
            return err

        try:
            body = await request.json()
        except Exception:
            logger.warning("astraeus_media.references.invalid_json", endpoint="set_references")
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        host_model = body.get("host_model")
        host_id = body.get("host_id")
        asset_keys = body.get("asset_keys")

        if not host_model or not host_id or asset_keys is None:
            return JSONResponse(
                {"error": "host_model, host_id, and asset_keys are required"},
                status_code=422,
            )

        if not isinstance(asset_keys, list):
            return JSONResponse({"error": "asset_keys must be a list"}, status_code=422)

        await mk.catalog.set_references(str(host_model), str(host_id), asset_keys)
        return JSONResponse({"ok": True, "count": len(asset_keys)})

    async def remove_references(request: Request) -> Response:
        """``DELETE /references`` — remove all asset refs for a content record.

        Body: ``{ "host_model": str, "host_id": str }``
        """
        from astraeus_media.auth import require_auth

        if (err := await require_auth(request, mk)) is not None:
            return err

        try:
            body = await request.json()
        except Exception:
            logger.warning("astraeus_media.references.invalid_json", endpoint="remove_references")
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        host_model = body.get("host_model")
        host_id = body.get("host_id")

        if not host_model or not host_id:
            return JSONResponse(
                {"error": "host_model and host_id are required"},
                status_code=422,
            )

        await mk.catalog.remove_references(str(host_model), str(host_id))
        return Response(status_code=204)

    return [
        Route("/references", endpoint=set_references, methods=["POST"]),
        Route("/references", endpoint=remove_references, methods=["DELETE"]),
    ]
