"""Schema introspection endpoints — /api/schema"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from starlette_cms.auth import require_auth

if TYPE_CHECKING:
    from starlette_cms.app import CMS


def _block_schema(
    model: type, block_name: str, group: str | None = None, label: str | None = None
) -> dict[str, Any]:
    """
    Return the JSON Schema for a block model, augmented with ``cms:field_meta``
    per field where present.

    Piccolo's ``json_schema_extra`` is stored per-field under ``properties``;
    we hoist ``cms:field_meta`` from each property into a top-level
    ``field_meta`` map for convenience.
    """
    raw = model.model_json_schema()

    # Attach field_meta at the schema root for easy consumption
    field_meta_map: dict[str, Any] = {}
    for field_name, field_schema in raw.get("properties", {}).items():
        meta = field_schema.get("cms:field_meta")
        if meta:
            field_meta_map[field_name] = meta

    result: dict[str, Any] = {
        "block_type": block_name,
        "schema": raw,
    }
    if field_meta_map:
        result["field_meta"] = field_meta_map
    if group:
        result["group"] = group
    if label:
        result["label"] = label
    return result


def make_schema_routes(cms: CMS) -> list[Route]:
    """Build and return all schema introspection routes, closed over ``cms``."""

    async def list_schema(request: Request) -> JSONResponse:
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        blocks = cms.registry.all()
        return JSONResponse({
            name: _block_schema(
                model, name, (reg := cms.registry.get_registration(name)).group, reg.label
            )
            for name, model in blocks.items()
        })

    async def get_block_schema(request: Request) -> JSONResponse:
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        block_type = request.path_params["block_type"]
        blocks = cms.registry.all()
        if block_type not in blocks:
            return JSONResponse(
                {"error": f"Block type {block_type!r} is not registered"},
                status_code=404,
            )
        reg = cms.registry.get_registration(block_type)
        return JSONResponse(_block_schema(blocks[block_type], block_type, reg.group, reg.label))

    async def get_schema_version(request: Request) -> JSONResponse:
        from starlette_cms import __version__

        return JSONResponse({"version": __version__})

    return [
        Route("/api/schema", endpoint=list_schema, methods=["GET"]),
        Route("/api/schema/version", endpoint=get_schema_version, methods=["GET"]),
        Route("/api/schema/{block_type}", endpoint=get_block_schema, methods=["GET"]),
    ]
