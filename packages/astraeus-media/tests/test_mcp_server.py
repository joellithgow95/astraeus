"""
Tests for the MCP server (astraeus_media/mcp/server.py).

Strategy: build the MCP server with a mocked httpx.AsyncClient so we don't
need a live HTTP server.  Each test directly calls the tool coroutine via the
FastMCP instance's tool registry and verifies the correct HTTP calls are made.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest_plugins = ["anyio"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, json_data: Any) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from httpx import HTTPStatusError

        resp.raise_for_status.side_effect = HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _build_server(api_key: str | None = None):
    """Build a server instance pointed at a test base URL."""
    from astraeus_media.mcp.server import build_mcp_server

    return build_mcp_server(base_url="http://testmedia.local", api_key=api_key)


async def _call_tool(mcp_server, tool_name: str, **kwargs) -> Any:
    """Look up a tool by name and call it directly with the given kwargs."""
    tools = await mcp_server.list_tools()
    for t in tools:
        if t.name == tool_name:
            break
    else:
        raise KeyError(f"Tool {tool_name!r} not registered")

    tool_fn = mcp_server._tool_manager._tools[tool_name].fn
    return await tool_fn(**kwargs)


# ---------------------------------------------------------------------------
# list_assets
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_assets_returns_page():
    mcp = _build_server()
    payload = {"assets": [{"key": "originals/abc/photo.jpg"}], "limit": 20, "offset": 0}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        result = await _call_tool(mcp, "list_assets")

    assert result["assets"] == payload["assets"]
    assert mock_get.called


@pytest.mark.anyio
async def test_list_assets_pagination():
    mcp = _build_server()
    payload = {"assets": [], "limit": 10, "offset": 20}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        result = await _call_tool(mcp, "list_assets", limit=10, offset=20)

    assert result["assets"] == []
    params = mock_get.call_args.kwargs.get("params", {})
    assert params.get("limit") == 10
    assert params.get("offset") == 20


# ---------------------------------------------------------------------------
# search_assets
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_assets_by_content_type():
    mcp = _build_server()
    payload = {"assets": [], "limit": 20, "offset": 0}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        await _call_tool(mcp, "search_assets", content_type="image/webp")

    params = mock_get.call_args.kwargs.get("params", {})
    assert params.get("content_type") == "image/webp"
    assert "tags" not in params


@pytest.mark.anyio
async def test_search_assets_by_tags():
    mcp = _build_server()
    payload = {"assets": [], "limit": 20, "offset": 0}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        await _call_tool(mcp, "search_assets", tags="nature,landscape")

    params = mock_get.call_args.kwargs.get("params", {})
    assert params.get("tags") == "nature,landscape"
    assert "content_type" not in params


# ---------------------------------------------------------------------------
# get_asset
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_asset_found():
    mcp = _build_server()
    asset = {
        "key": "originals/abc/photo.jpg",
        "content_type": "image/jpeg",
        "download_url": "https://bucket.example.com/originals/abc/photo.jpg?sig=...",
    }
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(200, asset),
    ):
        result = await _call_tool(mcp, "get_asset", key="originals/abc/photo.jpg")

    assert result == asset
    assert result["key"] == "originals/abc/photo.jpg"


@pytest.mark.anyio
async def test_get_asset_not_found():
    mcp = _build_server()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(404, {"error": "not found"}),
    ):
        result = await _call_tool(mcp, "get_asset", key="originals/nope/missing.jpg")

    assert "error" in result
    assert "originals/nope/missing.jpg" in result["error"]


# ---------------------------------------------------------------------------
# update_asset
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_asset_success():
    mcp = _build_server()
    updated = {
        "key": "originals/abc/photo.jpg",
        "alt_text": "A beautiful sunset",
        "tags": ["nature", "sunset"],
    }
    mock_patch = AsyncMock(return_value=_mock_response(200, updated))

    with patch("httpx.AsyncClient.patch", mock_patch):
        result = await _call_tool(
            mcp,
            "update_asset",
            key="originals/abc/photo.jpg",
            alt_text="A beautiful sunset",
            tags=["nature", "sunset"],
        )

    assert result == updated
    payload = mock_patch.call_args.kwargs.get("json", {})
    assert payload["alt_text"] == "A beautiful sunset"
    assert payload["tags"] == ["nature", "sunset"]


@pytest.mark.anyio
async def test_update_asset_not_found():
    mcp = _build_server()
    mock_patch = AsyncMock(return_value=_mock_response(404, {"error": "not found"}))

    with patch("httpx.AsyncClient.patch", mock_patch):
        result = await _call_tool(
            mcp, "update_asset", key="originals/gone/missing.jpg", alt_text="test"
        )

    assert "error" in result
    assert "originals/gone/missing.jpg" in result["error"]


# ---------------------------------------------------------------------------
# delete_asset
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_asset_success():
    mcp = _build_server()
    mock_delete = AsyncMock(return_value=_mock_response(204, None))
    mock_delete.return_value.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.delete", mock_delete):
        result = await _call_tool(mcp, "delete_asset", key="originals/abc/photo.jpg")

    assert result["deleted"] is True
    assert result["key"] == "originals/abc/photo.jpg"


@pytest.mark.anyio
async def test_delete_asset_not_found():
    mcp = _build_server()
    mock_delete = AsyncMock(return_value=_mock_response(404, {"error": "not found"}))

    with patch("httpx.AsyncClient.delete", mock_delete):
        result = await _call_tool(mcp, "delete_asset", key="originals/gone/missing.jpg")

    assert "error" in result
    assert "originals/gone/missing.jpg" in result["error"]


# ---------------------------------------------------------------------------
# get_iiif_url
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_iiif_url_defaults():
    """No HTTP call; returns a well-formed IIIF URL with default params."""
    mcp = _build_server()
    key = "originals/abc/photo.jpg"

    result = await _call_tool(mcp, "get_iiif_url", key=key)

    assert result == f"http://testmedia.local/iiif/{key}/full/max/0/default.webp"


@pytest.mark.anyio
async def test_get_iiif_url_custom_params():
    """Custom region/size/format are reflected in the returned URL."""
    mcp = _build_server()
    key = "originals/abc/photo.jpg"

    result = await _call_tool(
        mcp,
        "get_iiif_url",
        key=key,
        region="square",
        size="200,",
        rotation="90",
        quality="color",
        format="jpg",
    )

    assert result == f"http://testmedia.local/iiif/{key}/square/200,/90/color.jpg"


# ---------------------------------------------------------------------------
# All tools registered
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_all_tools_registered():
    """All six expected tools are registered."""
    from astraeus_media.mcp.server import build_mcp_server

    mcp = build_mcp_server(base_url="http://example.com")
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    expected = {
        "list_assets",
        "search_assets",
        "get_asset",
        "update_asset",
        "delete_asset",
        "get_iiif_url",
    }
    assert expected <= tool_names, f"Missing tools: {expected - tool_names}"


# ---------------------------------------------------------------------------
# Pagination metadata, argument validation, annotations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_assets_default_limit_and_metadata_when_page_full():
    mcp = _build_server()
    assets = [{"key": f"k{i}"} for i in range(10)]
    payload = {"assets": assets, "limit": 10, "offset": 0}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        result = await _call_tool(mcp, "list_assets")

    assert mock_get.call_args.kwargs["params"]["limit"] == 10
    assert result["returned"] == 10
    assert result["has_more"] is True
    assert result["next_offset"] == 10


@pytest.mark.anyio
async def test_list_assets_metadata_on_last_page():
    mcp = _build_server()
    payload = {"assets": [{"key": "a"}], "limit": 10, "offset": 20}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        result = await _call_tool(mcp, "list_assets", offset=20)

    assert result["returned"] == 1
    assert result["has_more"] is False
    assert result["next_offset"] is None


@pytest.mark.anyio
async def test_unknown_parameter_is_rejected_with_suggestion():
    from mcp.server.fastmcp.exceptions import ToolError

    mcp = _build_server()
    mock_get = AsyncMock()

    with patch("httpx.AsyncClient.get", mock_get):
        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("search_assets", {"tag": "nature"})

    message = str(exc_info.value)
    assert "Unknown parameter `tag`" in message
    assert "Did you mean `tags`?" in message
    mock_get.assert_not_called()


@pytest.mark.anyio
async def test_limit_above_maximum_is_rejected():
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as exc_info:
        await _build_server().call_tool("list_assets", {"limit": 500})

    assert "50" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_asset_not_found_has_hint():
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(404, {"error": "not found"}),
    ):
        result = await _call_tool(_build_server(), "get_asset", key="nope")

    assert "list_assets" in result["hint"]


@pytest.mark.anyio
async def test_tool_annotations_distinguish_read_write_destructive():
    tools = {t.name: t for t in await _build_server().list_tools()}

    assert tools["list_assets"].annotations.readOnlyHint is True
    assert tools["get_iiif_url"].annotations.readOnlyHint is True
    assert tools["update_asset"].annotations.readOnlyHint is False
    assert tools["delete_asset"].annotations.destructiveHint is True
    assert all(t.inputSchema["additionalProperties"] is False for t in tools.values())


# ---------------------------------------------------------------------------
# upload_asset
# ---------------------------------------------------------------------------

PRESIGNED_URL = "https://bucket.r2.example/originals/ab12cd34/photo.png?X-Amz-Signature=sig"
UPLOADED = {"key": "originals/ab12cd34/photo.png", "filename": "photo.png"}


def _patch_upload_flow(image: tuple[bytes, str] = (b"png-bytes", "image/png")):
    """Patch the fetch and the three HTTP calls; returns the post and put mocks."""
    fetch = patch("astraeus_media.mcp.server.fetch_image", AsyncMock(return_value=image))
    post = AsyncMock(
        side_effect=[
            _mock_response(200, {"upload_url": PRESIGNED_URL, "key": UPLOADED["key"]}),
            _mock_response(201, UPLOADED),
        ]
    )
    put = AsyncMock(return_value=_mock_response(200, {}))
    return fetch, post, put


@pytest.mark.anyio
async def test_upload_asset_runs_prepare_put_confirm():
    fetch, post, put = _patch_upload_flow()

    with fetch, patch("httpx.AsyncClient.post", post), patch("httpx.AsyncClient.put", put):
        result = await _call_tool(
            _build_server(), "upload_asset", source_url="https://img.example.com/photo.png"
        )

    prepare_call, confirm_call = post.call_args_list
    assert prepare_call.args[0] == "/upload/prepare"
    assert prepare_call.kwargs["json"] == {
        "filename": "photo.png",
        "content_type": "image/png",
        "size": 9,
    }
    assert put.call_args.args[0] == PRESIGNED_URL
    assert put.call_args.kwargs["content"] == b"png-bytes"
    assert confirm_call.args[0] == "/upload/confirm"
    assert confirm_call.kwargs["json"]["key"] == UPLOADED["key"]
    assert result["key"] == UPLOADED["key"]
    assert result["iiif_url"].endswith(f"/iiif/{UPLOADED['key']}/full/1200,/0/default.webp")


@pytest.mark.anyio
async def test_upload_asset_does_not_send_the_api_key_to_the_bucket():
    fetch, post, put = _patch_upload_flow()

    with fetch, patch("httpx.AsyncClient.post", post), patch("httpx.AsyncClient.put", put):
        await _call_tool(
            _build_server(api_key="secret"),
            "upload_asset",
            source_url="https://img.example.com/photo.png",
        )

    assert "Authorization" not in put.call_args.kwargs["headers"]


@pytest.mark.anyio
async def test_upload_asset_sets_alt_text_and_tags_when_given():
    fetch, post, put = _patch_upload_flow()
    patched = {**UPLOADED, "alt_text": "A harbour", "tags": ["boats"]}
    patch_call = AsyncMock(return_value=_mock_response(200, patched))

    with (
        fetch,
        patch("httpx.AsyncClient.post", post),
        patch("httpx.AsyncClient.put", put),
        patch("httpx.AsyncClient.patch", patch_call),
    ):
        result = await _call_tool(
            _build_server(),
            "upload_asset",
            source_url="https://img.example.com/photo.png",
            alt_text="A harbour",
            tags=["boats"],
        )

    assert patch_call.call_args.kwargs["json"] == {"alt_text": "A harbour", "tags": ["boats"]}
    assert result["alt_text"] == "A harbour"


@pytest.mark.anyio
async def test_upload_asset_returns_a_refused_url_as_an_error():
    from astraeus_media.mcp.fetch import FetchError

    refused = AsyncMock(side_effect=FetchError("Only https URLs are accepted"))

    with patch("astraeus_media.mcp.server.fetch_image", refused):
        result = await _call_tool(
            _build_server(), "upload_asset", source_url="http://internal.local/a.png"
        )

    assert result["error"] == "Only https URLs are accepted"
    assert "https" in result["hint"]


@pytest.mark.anyio
async def test_upload_asset_keeps_only_the_last_segment_of_a_filename():
    fetch, post, put = _patch_upload_flow()

    with fetch, patch("httpx.AsyncClient.post", post), patch("httpx.AsyncClient.put", put):
        await _call_tool(
            _build_server(),
            "upload_asset",
            source_url="https://img.example.com/x",
            filename="../../evil/name.png",
        )

    assert post.call_args_list[0].kwargs["json"]["filename"] == "name.png"


@pytest.mark.anyio
async def test_iiif_urls_use_the_public_url_when_given():
    mcp = _build_server_with(public_url="https://cms.example.com/media/")

    result = await _call_tool(mcp, "get_iiif_url", key="originals/a/p.webp", size="200,")

    assert (
        result == "https://cms.example.com/media/iiif/originals/a/p.webp/full/200,/0/default.webp"
    )


def _build_server_with(**kwargs):
    from astraeus_media.mcp.server import build_mcp_server

    return build_mcp_server(base_url="http://astraeus-cms:8000/media", **kwargs)


# ---------------------------------------------------------------------------
# prepare_upload / confirm_upload
# ---------------------------------------------------------------------------

PREPARED = {
    "upload_url": "https://bucket.r2.example/originals/ab12cd34/harbour.tif?X-Amz-Signature=sig",
    "key": "originals/ab12cd34/harbour.tif",
    "expires_at": "2026-10-12T21:00:00+00:00",
}
CONFIRM_BODY = {
    "key": PREPARED["key"],
    "filename": "harbour.tif",
    "content_type": "image/tiff",
    "size": 1048576,
}


@pytest.mark.anyio
async def test_prepare_upload_returns_url_and_put_instructions():
    post = AsyncMock(return_value=_mock_response(200, PREPARED))

    with patch("httpx.AsyncClient.post", post):
        result = await _call_tool(
            _build_server(),
            "prepare_upload",
            filename="harbour.tif",
            content_type="image/tiff",
            size=1048576,
        )

    assert post.call_args.args[0] == "/upload/prepare"
    assert post.call_args.kwargs["json"] == {
        "filename": "harbour.tif",
        "content_type": "image/tiff",
        "size": 1048576,
    }
    assert result["upload_url"] == PREPARED["upload_url"]
    assert result["key"] == PREPARED["key"]
    assert "Content-Type: image/tiff" in result["next"]


@pytest.mark.anyio
async def test_confirm_upload_catalogs_the_file_and_returns_an_embed_url():
    post = AsyncMock(return_value=_mock_response(201, {"key": PREPARED["key"]}))

    with patch("httpx.AsyncClient.post", post):
        result = await _call_tool(_build_server(), "confirm_upload", **CONFIRM_BODY)

    assert post.call_args.args[0] == "/upload/confirm"
    assert post.call_args.kwargs["json"] == CONFIRM_BODY
    assert result["iiif_url"].endswith(f"/iiif/{PREPARED['key']}/full/1200,/0/default.webp")


@pytest.mark.anyio
async def test_confirm_upload_sets_alt_text_and_tags_when_given():
    post = AsyncMock(return_value=_mock_response(201, {"key": PREPARED["key"]}))
    patched = {"key": PREPARED["key"], "alt_text": "A harbour", "tags": ["boats"]}
    patch_call = AsyncMock(return_value=_mock_response(200, patched))

    with patch("httpx.AsyncClient.post", post), patch("httpx.AsyncClient.patch", patch_call):
        result = await _call_tool(
            _build_server(), "confirm_upload", alt_text="A harbour", tags=["boats"], **CONFIRM_BODY
        )

    assert patch_call.call_args.kwargs["json"] == {"alt_text": "A harbour", "tags": ["boats"]}
    assert result["alt_text"] == "A harbour"


@pytest.mark.anyio
async def test_confirm_upload_without_a_put_tells_the_agent_what_to_do():
    post = AsyncMock(return_value=_mock_response(404, {"error": "not found"}))

    with patch("httpx.AsyncClient.post", post):
        result = await _call_tool(_build_server(), "confirm_upload", **CONFIRM_BODY)

    assert "No uploaded file" in result["error"]
    assert "prepare_upload" in result["hint"]
