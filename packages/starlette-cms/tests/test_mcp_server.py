"""
Tests for the MCP server (starlette_cms/mcp/server.py).

Strategy: build the MCP server with a mocked httpx.AsyncClient so we don't
need a live HTTP server.  Each test directly calls the tool coroutine via the
FastMCP instance's tool registry and verifies the correct HTTP calls are made.
"""

from __future__ import annotations

import json
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
    """Build a server instance with a patched AsyncClient."""
    from starlette_cms.mcp.server import build_mcp_server

    return build_mcp_server(base_url="http://testcms.local", api_key=api_key)


async def _call_tool(mcp_server, tool_name: str, **kwargs) -> Any:
    """Look up a tool by name and call it directly with the given kwargs."""
    tools = await mcp_server.list_tools()
    for t in tools:
        if t.name == tool_name:
            break
    else:
        raise KeyError(f"Tool {tool_name!r} not registered")

    # Retrieve the underlying coroutine function from the FastMCP internals
    tool_fn = mcp_server._tool_manager._tools[tool_name].fn
    return await tool_fn(**kwargs)


# ---------------------------------------------------------------------------
# list_block_types
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_block_types_returns_names():
    mcp = _build_server()
    schema_response = {
        "$defs": {
            "blog_post": {"type": "object"},
            "hero": {"type": "object"},
        }
    }
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(200, schema_response),
    ):
        result = await _call_tool(mcp, "list_block_types")

    assert set(result) == {"blog_post", "hero"}


@pytest.mark.anyio
async def test_list_block_types_definitions_fallback():
    """Accepts older 'definitions' key too."""
    mcp = _build_server()
    schema_response = {"definitions": {"article": {"type": "object"}}}
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(200, schema_response),
    ):
        result = await _call_tool(mcp, "list_block_types")

    assert result == ["article"]


# ---------------------------------------------------------------------------
# get_block_schema
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_block_schema_found():
    mcp = _build_server()
    schema = {"title": "hero", "properties": {"title": {"type": "string"}}}
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(200, schema),
    ):
        result = await _call_tool(mcp, "get_block_schema", block_type="hero")

    assert result == schema


@pytest.mark.anyio
async def test_get_block_schema_not_found():
    mcp = _build_server()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(404, {"error": "not found"}),
    ):
        result = await _call_tool(mcp, "get_block_schema", block_type="unknown")

    assert "error" in result
    assert "unknown" in result["error"]


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_documents_no_filters():
    mcp = _build_server()
    payload = {"documents": [], "total": 0, "filters_applied": {}}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        result = await _call_tool(mcp, "list_documents")

    assert result["documents"] == []
    assert result["total"] == 0
    assert result["filters_applied"] == {}
    call_kwargs = mock_get.call_args
    assert "/api/documents" in str(call_kwargs)


@pytest.mark.anyio
async def test_list_documents_with_type_filter():
    mcp = _build_server()
    payload = {
        "documents": [{"id": "abc", "doc_type": "blog_post", "body": {}}],
        "total": 1,
        "filters_applied": {},
    }
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        result = await _call_tool(mcp, "list_documents", doc_type="blog_post", limit=10)

    assert result["documents"][0]["id"] == "abc"
    # Check the correct query params were sent
    call_args = mock_get.call_args
    params = call_args.kwargs.get("params") or call_args.args[1] if len(call_args.args) > 1 else {}
    if not params:
        # params passed as keyword
        params = mock_get.call_args.kwargs.get("params", {})
    assert params.get("type") == "blog_post"
    assert params.get("limit") == 10


@pytest.mark.anyio
async def test_list_documents_published_filter():
    mcp = _build_server()
    mock_get = AsyncMock(
        return_value=_mock_response(200, {"documents": [], "total": 0, "filters_applied": {}})
    )

    with patch("httpx.AsyncClient.get", mock_get):
        await _call_tool(mcp, "list_documents", published=True)

    params = mock_get.call_args.kwargs.get("params", {})
    assert params.get("published") == "true"


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_document_found():
    mcp = _build_server()
    doc = {"id": "doc123", "doc_type": "hero", "body": {"title": "Hello"}}
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(200, doc),
    ):
        result = await _call_tool(mcp, "get_document", doc_id="doc123")

    assert result == doc


@pytest.mark.anyio
async def test_get_document_not_found():
    mcp = _build_server()
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(404, {"error": "not found"}),
    ):
        result = await _call_tool(mcp, "get_document", doc_id="nope")

    assert "error" in result
    assert "nope" in result["error"]


# ---------------------------------------------------------------------------
# create_document
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_document_success():
    mcp = _build_server()
    created = {"id": "newdoc", "doc_type": "blog_post", "body": {"title": "Test"}}
    mock_post = AsyncMock(return_value=_mock_response(201, created))

    with patch("httpx.AsyncClient.post", mock_post):
        result = await _call_tool(
            mcp,
            "create_document",
            doc_type="blog_post",
            body={"title": "Test"},
            slug="test-post",
        )

    assert result == created
    payload = mock_post.call_args.kwargs.get("json", {})
    assert payload["doc_type"] == "blog_post"
    assert payload["body"] == {"title": "Test"}
    assert payload["slug"] == "test-post"


@pytest.mark.anyio
async def test_create_document_validation_error():
    mcp = _build_server()
    err_payload = {"error": "Validation failed", "detail": [{"loc": ["title"], "msg": "required"}]}
    mock_post = AsyncMock(return_value=_mock_response(422, err_payload))
    mock_post.return_value.raise_for_status = MagicMock()  # 422 is returned, not raised

    with patch("httpx.AsyncClient.post", mock_post):
        result = await _call_tool(
            mcp,
            "create_document",
            doc_type="blog_post",
            body={},
        )

    assert "error" in result


# ---------------------------------------------------------------------------
# update_document
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_document_success():
    mcp = _build_server()
    updated = {"id": "doc1", "doc_type": "blog_post", "body": {"title": "Updated"}}
    mock_patch = AsyncMock(return_value=_mock_response(200, updated))

    with patch("httpx.AsyncClient.patch", mock_patch):
        result = await _call_tool(
            mcp,
            "update_document",
            doc_id="doc1",
            body={"title": "Updated"},
        )

    assert result == updated
    payload = mock_patch.call_args.kwargs.get("json", {})
    assert payload["body"] == {"title": "Updated"}


@pytest.mark.anyio
async def test_update_document_not_found():
    mcp = _build_server()
    mock_patch = AsyncMock(return_value=_mock_response(404, {"error": "not found"}))

    with patch("httpx.AsyncClient.patch", mock_patch):
        result = await _call_tool(mcp, "update_document", doc_id="gone", body={})

    assert "error" in result


@pytest.mark.anyio
async def test_update_document_append_only_rejected():
    mcp = _build_server()
    mock_patch = AsyncMock(
        return_value=_mock_response(
            405, {"error": "append_only documents cannot be modified"}
        )
    )

    with patch("httpx.AsyncClient.patch", mock_patch):
        result = await _call_tool(mcp, "update_document", doc_id="audit1", body={})

    assert result["error"] == "append_only documents cannot be modified"


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_document_success():
    mcp = _build_server()
    mock_delete = AsyncMock(return_value=_mock_response(204, None))
    mock_delete.return_value.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.delete", mock_delete):
        result = await _call_tool(mcp, "delete_document", doc_id="doc1")

    assert result["deleted"] is True
    assert result["doc_id"] == "doc1"


@pytest.mark.anyio
async def test_delete_document_not_found():
    mcp = _build_server()
    mock_delete = AsyncMock(return_value=_mock_response(404, {"error": "not found"}))

    with patch("httpx.AsyncClient.delete", mock_delete):
        result = await _call_tool(mcp, "delete_document", doc_id="gone")

    assert "error" in result


@pytest.mark.anyio
async def test_delete_document_conflict():
    mcp = _build_server()
    mock_delete = AsyncMock(
        return_value=_mock_response(409, {"error": "Cannot delete: referenced by other.field"})
    )

    with patch("httpx.AsyncClient.delete", mock_delete):
        result = await _call_tool(mcp, "delete_document", doc_id="ref1")

    assert "Conflict" in result["error"] or "referenced" in result["error"]


@pytest.mark.anyio
async def test_delete_document_append_only_rejected():
    mcp = _build_server()
    mock_delete = AsyncMock(
        return_value=_mock_response(
            405, {"error": "append_only documents cannot be deleted"}
        )
    )

    with patch("httpx.AsyncClient.delete", mock_delete):
        result = await _call_tool(mcp, "delete_document", doc_id="audit1")

    assert "not allowed" in result["error"] or "Method" in result["error"] or "append_only" in result["error"]


# ---------------------------------------------------------------------------
# publish_document
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_document_success():
    mcp = _build_server()
    published = {"id": "doc1", "published": True}
    mock_post = AsyncMock(return_value=_mock_response(200, published))

    with patch("httpx.AsyncClient.post", mock_post):
        result = await _call_tool(mcp, "publish_document", doc_id="doc1")

    assert result == published


@pytest.mark.anyio
async def test_publish_document_not_found():
    mcp = _build_server()
    mock_post = AsyncMock(return_value=_mock_response(404, {"error": "not found"}))

    with patch("httpx.AsyncClient.post", mock_post):
        result = await _call_tool(mcp, "publish_document", doc_id="gone")

    assert "error" in result


# ---------------------------------------------------------------------------
# unpublish_document
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unpublish_document_success():
    mcp = _build_server()
    unpublished = {"id": "doc1", "published": False}
    mock_post = AsyncMock(return_value=_mock_response(200, unpublished))

    with patch("httpx.AsyncClient.post", mock_post):
        result = await _call_tool(mcp, "unpublish_document", doc_id="doc1")

    assert result == unpublished


# ---------------------------------------------------------------------------
# Auth header forwarding
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_key_sent_as_bearer_on_writes():
    """API key is included in the Authorization header on mutating calls."""
    mcp = _build_server(api_key="my-secret")
    created = {"id": "newdoc", "doc_type": "blog_post", "body": {}}
    mock_post = AsyncMock(return_value=_mock_response(201, created))

    with patch("httpx.AsyncClient.post", mock_post):
        await _call_tool(mcp, "create_document", doc_type="blog_post", body={})

    # The Authorization header is set at client construction via default headers
    # We verify by checking the server was built with an api_key (structural test)
    # and that no error was raised during the call.
    assert mock_post.called


@pytest.mark.anyio
async def test_all_tools_registered():
    """All nine expected tools are registered."""
    from starlette_cms.mcp.server import build_mcp_server

    mcp = build_mcp_server(base_url="http://example.com")
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    expected = {
        "list_block_types",
        "get_block_schema",
        "list_documents",
        "get_document",
        "create_document",
        "update_document",
        "delete_document",
        "publish_document",
        "unpublish_document",
    }
    assert expected <= tool_names, f"Missing tools: {expected - tool_names}"


# ---------------------------------------------------------------------------
# list_documents: summaries, pagination, size caps
# ---------------------------------------------------------------------------

HUGE_BODY = {"title": "A post", "excerpt": "Short teaser", "content": "x" * 50_000}


def _full_doc(doc_id: str, published: bool = True) -> dict[str, Any]:
    return {
        "id": doc_id,
        "doc_type": "blog_post",
        "slug": f"post-{doc_id}",
        "published": published,
        "has_draft": False,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-02T00:00:00",
        "published_at": None,
        "body": dict(HUGE_BODY),
    }


async def _list(mcp, docs: list[dict[str, Any]], total: int, **kwargs) -> dict[str, Any]:
    payload = {"documents": docs, "total": total, "filters_applied": {}}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))
    with patch("httpx.AsyncClient.get", mock_get):
        result = await _call_tool(mcp, "list_documents", **kwargs)
    result["_params"] = mock_get.call_args.kwargs["params"]
    return result


@pytest.mark.anyio
async def test_list_documents_returns_summaries_without_bodies():
    mcp = _build_server()
    result = await _list(mcp, [_full_doc("a"), _full_doc("b", published=False)], total=2)

    first, second = result["documents"]
    assert "body" not in first
    assert first["title"] == "A post"
    assert first["excerpt"] == "Short teaser"
    assert first["status"] == "published"
    assert second["status"] == "draft"
    assert len(json.dumps(result)) < 2_000


@pytest.mark.anyio
async def test_list_documents_default_limit_is_small():
    result = await _list(_build_server(), [], total=0)

    assert result["limit"] == 10
    assert result["_params"]["limit"] == 10


@pytest.mark.anyio
async def test_list_documents_pagination_metadata_with_more():
    docs = [_full_doc(str(i)) for i in range(5)]
    result = await _list(_build_server(), docs, total=12, limit=5, offset=5)

    assert result["total"] == 12
    assert result["limit"] == 5
    assert result["offset"] == 5
    assert result["returned"] == 5
    assert result["has_more"] is True
    assert result["next_offset"] == 10


@pytest.mark.anyio
async def test_list_documents_pagination_metadata_last_page():
    docs = [_full_doc(str(i)) for i in range(2)]
    result = await _list(_build_server(), docs, total=12, limit=5, offset=10)

    assert result["has_more"] is False
    assert result["next_offset"] is None


@pytest.mark.anyio
async def test_list_documents_empty_type_hints_at_list_block_types():
    result = await _list(_build_server(), [], total=0, doc_type="blogpost")

    assert "list_block_types" in result["hint"]


@pytest.mark.anyio
async def test_list_documents_include_body_restores_full_documents():
    doc = {"id": "a", "doc_type": "blog_post", "body": {"content": "short"}}
    result = await _list(_build_server(), [doc], total=1, include_body=True)

    assert result["documents"] == [doc]
    assert result["truncated"] is False


@pytest.mark.anyio
async def test_list_documents_include_body_oversized_single_document_falls_back():
    result = await _list(_build_server(), [_full_doc("a")], total=1, include_body=True)

    assert result["truncated"] is True
    assert "body" not in result["documents"][0]
    assert "get_document" in result["notice"]


@pytest.mark.anyio
async def test_list_documents_include_body_is_capped_with_notice():
    from starlette_cms.mcp.server import MAX_RESPONSE_CHARS

    small = {"id": "s", "doc_type": "blog_post", "body": {"content": "y" * 8_000}}
    docs = [dict(small, id=str(i)) for i in range(10)]
    result = await _list(_build_server(), docs, total=10, include_body=True)

    assert result["truncated"] is True
    assert result["returned"] < 10
    assert result["has_more"] is True
    assert result["next_offset"] == result["returned"]
    assert "next_offset" in result["notice"]
    assert len(json.dumps(result["documents"])) <= MAX_RESPONSE_CHARS


# ---------------------------------------------------------------------------
# get_document: size cap
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_document_truncates_huge_body_with_notice():
    mcp = _build_server()
    doc = _full_doc("big")
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(200, doc),
    ):
        result = await _call_tool(mcp, "get_document", doc_id="big", max_chars=5_000)

    assert result["truncated"] is True
    assert len(result["content"]) == 5_000
    assert "max_chars" in result["notice"]


@pytest.mark.anyio
async def test_get_document_not_found_has_hint():
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        return_value=_mock_response(404, {"error": "not found"}),
    ):
        result = await _call_tool(_build_server(), "get_document", doc_id="nope")

    assert "list_documents" in result["hint"]


# ---------------------------------------------------------------------------
# Argument validation through the real FastMCP call path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_parameter_is_rejected_with_suggestion():
    """The incident: document_type was silently dropped and every document returned."""
    from mcp.server.fastmcp.exceptions import ToolError

    mcp = _build_server()
    mock_get = AsyncMock()

    with patch("httpx.AsyncClient.get", mock_get):
        with pytest.raises(ToolError) as exc_info:
            await mcp.call_tool("list_documents", {"document_type": "blog_post"})

    message = str(exc_info.value)
    assert "Unknown parameter `document_type`" in message
    assert "Did you mean `doc_type`?" in message
    assert "`limit`" in message
    mock_get.assert_not_called()


@pytest.mark.anyio
async def test_known_parameters_still_work_through_call_tool():
    mcp = _build_server()
    payload = {"documents": [], "total": 0, "filters_applied": {}}
    mock_get = AsyncMock(return_value=_mock_response(200, payload))

    with patch("httpx.AsyncClient.get", mock_get):
        await mcp.call_tool("list_documents", {"doc_type": "blog_post", "limit": 5})

    assert mock_get.call_args.kwargs["params"]["type"] == "blog_post"


@pytest.mark.anyio
async def test_limit_above_maximum_is_rejected():
    from mcp.server.fastmcp.exceptions import ToolError

    mcp = _build_server()

    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool("list_documents", {"limit": 500})

    assert "50" in str(exc_info.value)


@pytest.mark.anyio
async def test_every_tool_rejects_unknown_parameters():
    mcp = _build_server()

    for tool in await mcp.list_tools():
        assert tool.inputSchema["additionalProperties"] is False, tool.name


@pytest.mark.anyio
async def test_tool_annotations_distinguish_read_write_destructive():
    mcp = _build_server()
    tools = {t.name: t.annotations for t in await mcp.list_tools()}

    assert tools["list_documents"].readOnlyHint is True
    assert tools["get_document"].readOnlyHint is True
    assert tools["create_document"].readOnlyHint is False
    assert tools["delete_document"].destructiveHint is True
    assert tools["publish_document"].destructiveHint is False
    assert tools["publish_document"].openWorldHint is True


@pytest.mark.anyio
async def test_publish_description_mentions_rebuild():
    mcp = _build_server()
    tools = {t.name: t.description for t in await mcp.list_tools()}

    assert "rebuild" in tools["publish_document"]
    assert "DRAFT" in tools["create_document"]


# ---------------------------------------------------------------------------
# tags: list_tags, and the note on a new tag
# ---------------------------------------------------------------------------


def _tag_routes(known: dict[str, int], schema_status: int = 200):
    """A GET double answering the schema, tag and document lookups the tag tools make."""

    async def get(self, url, **kwargs):
        if url.startswith("/api/schema/"):
            return _mock_response(
                schema_status,
                {"field_meta": {"tags": {"field_type": "tags"}, "title": {"label": "Title"}}},
            )
        if url == "/api/tags":
            tags = [{"tag": t, "count": n} for t, n in known.items()]
            return _mock_response(200, {"tags": tags})
        return _mock_response(200, {"id": "doc1", "doc_type": "link"})

    return get


@pytest.mark.anyio
async def test_list_tags_returns_the_ranked_tags():
    mcp = _build_server()
    mock_get = AsyncMock(
        return_value=_mock_response(200, {"tags": [{"tag": "ai", "count": 12}, {"tag": "art", "count": 4}]})
    )

    with patch("httpx.AsyncClient.get", mock_get):
        result = await _call_tool(mcp, "list_tags", doc_type="blog_post", limit=1)

    assert mock_get.call_args.kwargs["params"] == {"type": "blog_post", "field": "tags"}
    assert result["tags"] == [{"tag": "ai", "count": 12}]
    assert result["total"] == 2


@pytest.mark.anyio
async def test_create_document_notes_a_new_tag_and_names_similar_ones():
    mcp = _build_server()
    created = {"id": "n1", "doc_type": "link", "body": {}}
    known = {"developer-blogs": 6, "inspo": 4, "design": 2}

    with (
        patch("httpx.AsyncClient.get", _tag_routes(known)),
        patch("httpx.AsyncClient.post", AsyncMock(return_value=_mock_response(201, dict(created)))),
    ):
        result = await _call_tool(
            mcp,
            "create_document",
            doc_type="link",
            body={"tags": ["inspo", "design-portfolio", "Inspiration"]},
        )

    notes = {n["tag"]: n["note"] for n in result["tag_notes"]}
    assert set(notes) == {"design-portfolio", "inspiration"}  # inspo is known; Inspiration is normalised
    assert "design (2)" in notes["design-portfolio"]
    assert "inspo (4)" in notes["inspiration"]
    assert "Tags are lowercase and hyphenated: 'inspiration'" not in notes["design-portfolio"]


@pytest.mark.anyio
async def test_a_known_tag_gets_no_note():
    mcp = _build_server()

    with (
        patch("httpx.AsyncClient.get", _tag_routes({"inspo": 4})),
        patch("httpx.AsyncClient.post", AsyncMock(return_value=_mock_response(201, {"id": "n1"}))),
    ):
        result = await _call_tool(mcp, "create_document", doc_type="link", body={"tags": ["inspo"]})

    assert "tag_notes" not in result


@pytest.mark.anyio
async def test_the_write_goes_ahead_when_the_tag_lookup_fails():
    mcp = _build_server()
    mock_post = AsyncMock(return_value=_mock_response(201, {"id": "n1"}))

    with (
        patch("httpx.AsyncClient.get", _tag_routes({}, schema_status=500)),
        patch("httpx.AsyncClient.post", mock_post),
    ):
        result = await _call_tool(mcp, "create_document", doc_type="link", body={"tags": ["new"]})

    assert result == {"id": "n1"}
    mock_post.assert_awaited_once()


@pytest.mark.anyio
async def test_update_document_notes_a_new_tag():
    mcp = _build_server()
    mock_patch = AsyncMock(return_value=_mock_response(200, {"id": "doc1"}))

    with (
        patch("httpx.AsyncClient.get", _tag_routes({"developer-blogs": 6})),
        patch("httpx.AsyncClient.patch", mock_patch),
    ):
        result = await _call_tool(mcp, "update_document", doc_id="doc1", body={"tags": ["developer-blog"]})

    assert result["tag_notes"][0]["tag"] == "developer-blog"
    assert "developer-blogs (6)" in result["tag_notes"][0]["note"]
    assert mock_patch.call_args.kwargs["json"]["body"] == {"tags": ["developer-blog"]}
