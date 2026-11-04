"""
MCP server implementation for astraeus-media.

All tools are thin wrappers around the astraeus-media HTTP API.
No business logic lives here — only tool definitions and httpx calls.
"""

from __future__ import annotations

import difflib
import mimetypes
from pathlib import PurePosixPath
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from pydantic import Field, model_validator

from astraeus_media.mcp.fetch import FetchError, fetch_image

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The MCP server requires the 'mcp' extra. Install with: pip install astraeus-media[mcp]"
    ) from exc


DEFAULT_LIST_LIMIT = 10
MAX_LIST_LIMIT = 50

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
UPLOAD = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
UPDATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
DELETE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)

LIMIT_DESCRIPTION = (
    f"Max assets to return, 1-{MAX_LIST_LIMIT} (default {DEFAULT_LIST_LIMIT}). "
    "Use offset to page through more."
)
OFFSET_DESCRIPTION = "Assets to skip. Use `next_offset` from the previous page."


def _error(message: str, hint: str | None = None) -> dict[str, Any]:
    """Build an error payload: what went wrong, plus what to try next."""
    payload: dict[str, Any] = {"error": message}
    if hint:
        payload["hint"] = hint
    return payload


def _page(data: dict[str, Any], limit: int, offset: int) -> dict[str, Any]:
    """
    Add pagination metadata to a list response.

    The API does not report a total, so ``has_more`` is true whenever the page
    came back full; the next page may then turn out to be empty.
    """
    assets: list[dict[str, Any]] = data.get("assets", [])
    returned = len(assets)
    has_more = returned >= limit
    return {
        **data,
        "assets": assets,
        "has_more": has_more,
        "limit": limit,
        "next_offset": offset + returned if has_more else None,
        "offset": offset,
        "returned": returned,
    }


def _filename_for_upload(source_url: str, filename: str | None, content_type: str) -> str:
    """
    Pick the stored filename: the caller's, else the URL's last path segment,
    else ``upload`` plus an extension guessed from the content type.

    Only the final path segment is kept, so a name can't add key prefixes.
    """
    name = PurePosixPath(filename).name if filename else ""
    if not name:
        name = PurePosixPath(urlsplit(source_url).path).name
    if not name:
        name = "upload" + (mimetypes.guess_extension(content_type) or "")
    return name


def _unknown_parameter_message(tool_name: str, unknown: list[str], valid: list[str]) -> str:
    """Explain unknown tool arguments, suggesting the closest valid name."""
    parts: list[str] = []
    for name in unknown:
        close = difflib.get_close_matches(name, valid, n=1, cutoff=0.5)
        suggestion = f" Did you mean `{close[0]}`?" if close else ""
        parts.append(f"Unknown parameter `{name}` for {tool_name}.{suggestion}")
    valid_list = ", ".join(f"`{v}`" for v in valid) or "(none)"
    return " ".join(parts) + f" Valid parameters: {valid_list}."


def _reject_unknown_arguments(mcp: FastMCP) -> None:
    """
    Make every registered tool fail loudly on unknown arguments.

    FastMCP validates arguments with a pydantic model whose default is to
    silently ignore extra keys, so a misnamed filter was dropped and the call
    ran unfiltered.  Each tool's argument model is replaced by a subclass whose
    ``before`` validator raises a message naming the closest valid parameter.
    """
    for tool in mcp._tool_manager.list_tools():
        arg_model = tool.fn_metadata.arg_model
        valid = sorted(arg_model.model_fields)
        tool_name = tool.name

        def _check(cls: Any, data: Any, _tool: str = tool_name, _valid: list[str] = valid) -> Any:
            if isinstance(data, dict):
                unknown = sorted(set(data) - set(_valid))
                if unknown:
                    raise ValueError(_unknown_parameter_message(_tool, unknown, _valid))
            return data

        tool.fn_metadata.arg_model = type(
            arg_model.__name__,
            (arg_model,),
            {"check_unknown_arguments": model_validator(mode="before")(classmethod(_check))},
        )
        tool.parameters["additionalProperties"] = False


def build_mcp_server(
    base_url: str, api_key: str | None = None, public_url: str | None = None
) -> FastMCP:
    """
    Build and return a FastMCP server wired to the astraeus-media HTTP API.

    :param base_url: Base URL of the deployed astraeus-media instance,
        e.g. ``https://mysite.com/media``.  Must not have a trailing slash.
    :param api_key: API key for ``Authorization: Bearer`` header on all
        requests.  Pass ``None`` when running without auth.
    :param public_url: Where readers reach the instance, used for the image
        URLs the tools hand back.  Defaults to *base_url*; set it when the
        server calls the API over an internal address (e.g. a cluster service).

    All tools share one httpx.AsyncClient (connection-pooled).  The client is
    created lazily on the first tool call so the server can be imported without
    making any network connections.
    """

    public_base = (public_url or base_url).rstrip("/")
    _client: httpx.AsyncClient | None = None

    def _get_client() -> httpx.AsyncClient:
        nonlocal _client
        if _client is None or _client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            _client = httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=30.0,
            )
        return _client

    def _with_iiif_url(asset: dict[str, Any]) -> dict[str, Any]:
        """Add a ready-to-embed 1200px-wide WebP URL to an asset."""
        iiif = f"{public_base}/iiif/{asset['key']}/full/1200,/0/default.webp"
        return {**asset, "iiif_url": iiif}

    def _read_headers() -> dict[str, str]:
        if api_key:
            return {"Authorization": f"Bearer {api_key}"}
        return {}

    mcp = FastMCP(
        name="astraeus-media",
        instructions=(
            "Tools for managing media assets in a astraeus-media instance.\n"
            "Workflow:\n"
            "1. list_assets (browse) or search_assets (filter by content_type / tags) to "
            "find assets. Keep limit small and page with offset / next_offset.\n"
            "2. get_asset(key) for one asset's metadata and a presigned download URL.\n"
            "3. get_iiif_url(key, size=...) to build an embeddable derivative image URL; "
            "it makes no network call.\n"
            "4. upload_asset(source_url) to add an image from a public https URL; it "
            "returns the new asset, its `key` and an embeddable `iiif_url`.\n"
            "5. To add a file you hold yourself (not on a public URL): prepare_upload "
            "returns a presigned `upload_url`; HTTP PUT the raw bytes there with the same "
            "Content-Type; then confirm_upload. Needs a shell or HTTP client.\n"
            "6. update_asset to set alt_text or tags. delete_asset permanently removes "
            "the file and its catalog entry; only call it when asked.\n"
            "Parameter names are exact: an unknown parameter is rejected with a "
            "suggestion, not ignored."
        ),
    )

    # ------------------------------------------------------------------
    # Asset read tools
    # ------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def list_assets(
        limit: Annotated[
            int, Field(ge=1, le=MAX_LIST_LIMIT, description=LIMIT_DESCRIPTION)
        ] = DEFAULT_LIST_LIMIT,
        offset: Annotated[int, Field(ge=0, description=OFFSET_DESCRIPTION)] = 0,
    ) -> dict[str, Any]:
        """
        Browse the asset catalog, one page at a time.

        Use search_assets instead when you want to filter by content type or tag.
        Each asset includes its `key`, which get_asset, update_asset and
        delete_asset take.

        Returns `assets` plus `limit`, `offset`, `returned`, `has_more` and
        `next_offset` (pass it as `offset` to get the next page).
        """
        client = _get_client()
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        r = await client.get("/assets", params=params, headers=_read_headers())
        r.raise_for_status()
        return _page(r.json(), limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    async def search_assets(
        content_type: Annotated[
            str | None, Field(description='MIME type filter, e.g. "image/webp".')
        ] = None,
        tags: Annotated[
            str | None,
            Field(description='Comma-separated tag filter, e.g. "nature,landscape".'),
        ] = None,
        limit: Annotated[
            int, Field(ge=1, le=MAX_LIST_LIMIT, description=LIMIT_DESCRIPTION)
        ] = DEFAULT_LIST_LIMIT,
        offset: Annotated[int, Field(ge=0, description=OFFSET_DESCRIPTION)] = 0,
    ) -> dict[str, Any]:
        """
        Find assets by content type and/or tags, one page at a time.

        Both filters are optional; with neither it behaves like list_assets.
        Returns the same shape as list_assets, including pagination metadata.
        """
        client = _get_client()
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if content_type is not None:
            params["content_type"] = content_type
        if tags is not None:
            params["tags"] = tags
        r = await client.get("/assets", params=params, headers=_read_headers())
        r.raise_for_status()
        return _page(r.json(), limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    async def get_asset(
        key: Annotated[
            str,
            Field(
                description=(
                    'The asset storage key, e.g. "originals/abc123/photo.jpg". '
                    "Get keys from list_assets or search_assets."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """
        Return one asset's metadata and a presigned download URL.

        The download URL is temporary. For an image to embed in a page, use
        get_iiif_url instead.
        """
        client = _get_client()
        r = await client.get(f"/assets/{key}", headers=_read_headers())
        if r.status_code == 404:
            return _error(
                f"Asset {key!r} not found",
                hint="Use a `key` exactly as returned by list_assets or search_assets.",
            )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Asset write tools
    # ------------------------------------------------------------------

    @mcp.tool(annotations=UPLOAD)
    async def upload_asset(
        source_url: Annotated[
            str,
            Field(
                description=(
                    "Public https URL of an image to add. Plain http, private or "
                    "internal hosts, non-images and files over 25 MiB are refused."
                )
            ),
        ],
        filename: Annotated[
            str | None,
            Field(description="Stored filename. Defaults to the last segment of the URL path."),
        ] = None,
        alt_text: Annotated[
            str | None, Field(description="Alt text to set on the new asset.")
        ] = None,
        tags: Annotated[
            list[str] | None, Field(description="Tags to set on the new asset.")
        ] = None,
    ) -> dict[str, Any]:
        """
        Add an image to the library by fetching it from a public https URL.

        Not idempotent: every call creates a new asset, even for the same URL.
        Returns the asset (including its `key`) plus `iiif_url`, a ready-to-embed
        1200px-wide WebP URL; use get_iiif_url for other sizes.
        """
        try:
            data, content_type = await fetch_image(source_url)
        except FetchError as exc:
            return _error(
                str(exc),
                hint="Pass a public https URL that serves an image directly.",
            )

        name = _filename_for_upload(source_url, filename, content_type)
        client = _get_client()

        # 1. Ask astraeus-media for a presigned PUT URL and a storage key.
        r = await client.post(
            "/upload/prepare",
            json={"filename": name, "content_type": content_type, "size": len(data)},
        )
        r.raise_for_status()
        prepared = r.json()

        # 2. PUT the bytes straight to the bucket. The URL is presigned, so this
        # uses a bare client: the media API key must not be sent to the bucket.
        async with httpx.AsyncClient(timeout=60.0) as storage:
            put = await storage.put(
                prepared["upload_url"], content=data, headers={"Content-Type": content_type}
            )
        put.raise_for_status()

        # 3. Confirm: astraeus-media verifies the object, processes it, and catalogs it.
        r = await client.post(
            "/upload/confirm",
            json={
                "key": prepared["key"],
                "filename": name,
                "content_type": content_type,
                "size": len(data),
            },
        )
        r.raise_for_status()
        asset = r.json()

        if alt_text is not None or tags is not None:
            asset = await update_asset(key=asset["key"], alt_text=alt_text, tags=tags)

        return _with_iiif_url(asset)

    @mcp.tool(annotations=UPLOAD)
    async def prepare_upload(
        filename: Annotated[
            str, Field(description='Name to store the file under, e.g. "harbour.tif".')
        ],
        content_type: Annotated[
            str, Field(description='MIME type of the file, e.g. "image/tiff".')
        ],
        size: Annotated[int, Field(ge=1, description="File size in bytes.")],
    ) -> dict[str, Any]:
        """
        Step 1 of 2 for adding a file you already hold: get a presigned upload URL.

        Use this when the file is not on a public URL (otherwise use upload_asset).
        You must then HTTP PUT the raw file bytes to `upload_url` with the header
        `Content-Type` set to the same `content_type`, before `expires_at`, and call
        confirm_upload. The bytes go straight to storage, not through this server,
        so the file size is not limited by the tool call.
        """
        client = _get_client()
        r = await client.post(
            "/upload/prepare",
            json={"filename": filename, "content_type": content_type, "size": size},
        )
        r.raise_for_status()
        prepared = r.json()
        return {
            **prepared,
            "content_type": content_type,
            "next": (
                f"PUT the raw bytes to upload_url with header Content-Type: {content_type}, "
                "then call confirm_upload with this key, filename, content_type and size."
            ),
        }

    @mcp.tool(annotations=UPLOAD)
    async def confirm_upload(
        key: Annotated[str, Field(description="The `key` returned by prepare_upload.")],
        filename: Annotated[str, Field(description="The filename given to prepare_upload.")],
        content_type: Annotated[
            str, Field(description="The content_type given to prepare_upload.")
        ],
        size: Annotated[int, Field(ge=1, description="The size given to prepare_upload.")],
        alt_text: Annotated[
            str | None, Field(description="Alt text to set on the new asset.")
        ] = None,
        tags: Annotated[
            list[str] | None, Field(description="Tags to set on the new asset.")
        ] = None,
    ) -> dict[str, Any]:
        """
        Step 2 of 2: catalog a file you PUT to the URL from prepare_upload.

        Call it only after the PUT succeeded. Returns the asset (including its
        `key`) plus `iiif_url`, a ready-to-embed 1200px-wide WebP URL; use
        get_iiif_url for other sizes.
        """
        client = _get_client()
        r = await client.post(
            "/upload/confirm",
            json={"key": key, "filename": filename, "content_type": content_type, "size": size},
        )
        if r.status_code == 404:
            return _error(
                f"No uploaded file found for {key!r}",
                hint=(
                    "The PUT did not complete or the URL expired. Call prepare_upload "
                    "again and PUT the bytes before confirming."
                ),
            )
        r.raise_for_status()
        asset = r.json()

        if alt_text is not None or tags is not None:
            asset = await update_asset(key=asset["key"], alt_text=alt_text, tags=tags)

        return _with_iiif_url(asset)

    @mcp.tool(annotations=UPDATE)
    async def update_asset(
        key: Annotated[str, Field(description="The asset storage key from list_assets.")],
        alt_text: Annotated[
            str | None, Field(description="New alt text (accessibility / SEO).")
        ] = None,
        tags: Annotated[
            list[str] | None,
            Field(description="New tag list. REPLACES all existing tags."),
        ] = None,
    ) -> dict[str, Any]:
        """
        Update an asset's metadata (PATCH: only the fields you provide change).

        Safe to repeat. Note that `tags` replaces the whole list; call get_asset
        first if you want to add a tag without losing the existing ones.
        """
        client = _get_client()
        payload: dict[str, Any] = {}
        if alt_text is not None:
            payload["alt_text"] = alt_text
        if tags is not None:
            payload["tags"] = tags
        r = await client.patch(f"/assets/{key}", json=payload)
        if r.status_code == 404:
            return _error(
                f"Asset {key!r} not found",
                hint="Use a `key` exactly as returned by list_assets or search_assets.",
            )
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=DELETE)
    async def delete_asset(
        key: Annotated[str, Field(description="The asset storage key from list_assets.")],
    ) -> dict[str, Any]:
        """
        Permanently delete an asset from the storage bucket and the catalog.

        This cannot be undone and breaks any page that embeds the asset. Only call
        it when asked to delete.
        """
        client = _get_client()
        r = await client.delete(f"/assets/{key}")
        if r.status_code == 404:
            return _error(
                f"Asset {key!r} not found",
                hint="Use a `key` exactly as returned by list_assets; it may already be deleted.",
            )
        r.raise_for_status()
        return {"deleted": True, "key": key}

    # ------------------------------------------------------------------
    # IIIF URL construction (pure, no HTTP call)
    # ------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def get_iiif_url(
        key: str,
        region: str = "full",
        size: str = "max",
        rotation: str = "0",
        quality: str = "default",
        format: str = "webp",
    ) -> str:
        """
        Construct a IIIF Image API URL for a derivative of the given asset.

        No HTTP call is made — this is a pure URL construction helper.
        Use the returned URL in HTML ``<img>`` tags or pass it to other tools.

        :param key: The asset's storage key.
        :param region: IIIF region parameter (default ``"full"``).
        :param size: IIIF size parameter (default ``"max"``).
        :param rotation: IIIF rotation parameter (default ``"0"``).
        :param quality: IIIF quality parameter (default ``"default"``).
        :param format: Output format extension (default ``"webp"``).

        Example — thumbnail at 200 px wide::

            get_iiif_url(key="originals/abc/photo.jpg", size="200,")
            # → "https://mysite.com/media/iiif/originals/abc/photo.jpg/full/200,/0/default.webp"
        """
        return f"{public_base}/iiif/{key}/{region}/{size}/{rotation}/{quality}.{format}"

    _reject_unknown_arguments(mcp)

    return mcp
