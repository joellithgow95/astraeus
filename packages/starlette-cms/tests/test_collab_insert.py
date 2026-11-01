"""
Adding to a rich-text field through its authority (ADR 027).

The use case: a person is typing in the editor and asks Claude, over MCP, to
scaffold a new section of the same post. The section must appear in their
editor as they watch, and nothing they type before, during or after must be
lost. A whole-body PATCH cannot do that (it is built against what the writer
last read), so the insert goes through the authority as one step.

These run against a real in-process CMS with verification on, as prod runs it;
the MCP tools reach it through a transport that forwards to the same app.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

pytest.importorskip("prosemirror", reason="needs the collab-verify extra")
pytest.importorskip("mistune", reason="needs the collab-verify extra")

from starlette_cms import CMS, RichTextField, TextField  # noqa: E402
from starlette_cms.mcp.server import build_mcp_server  # noqa: E402


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _heading(text: str, level: int = 2) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


START = {
    "type": "doc",
    "content": [_para("Intro."), _heading("Setup"), _para("Install it."), _heading("Usage")],
}


def _insert(pos: int, text: str) -> dict:
    return {
        "stepType": "replace",
        "from": pos,
        "to": pos,
        "slice": {"content": [{"type": "text", "text": text}]},
    }


def _blocks(doc: dict) -> list[str]:
    return [
        "".join(n.get("text", "") for n in block.get("content", []))
        for block in doc.get("content", [])
    ]


@pytest.fixture()
def server():
    """(client, cms, doc_id) for a post whose body holds START."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cms = CMS(
        database_url=f"sqlite:///{tmp.name}",
        auth="none",
        read_auth=False,
        verify_collab=True,
        default_changeset="Staging",
    )

    @cms.block("post")
    class Post:
        title: str = TextField(required=True)
        body: dict = RichTextField()

    @cms.block("two_bodies")
    class TwoBodies:
        title: str = TextField(required=True)
        intro: dict = RichTextField()
        body: dict = RichTextField()

    app = Starlette(routes=[Mount("/", app=cms.app)], lifespan=cms.lifespan)
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(
                "/api/documents",
                json={"doc_type": "post", "slug": "p", "body": {"title": "T", "body": START}},
            )
            assert resp.status_code == 201, resp.text
            yield client, cms, resp.json()["id"]
    finally:
        os.unlink(tmp.name)


def _draft(client: TestClient, doc_id: str) -> dict:
    resp = client.get(f"/api/documents/{doc_id}", params={"draft": "true"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _url(doc_id: str) -> str:
    return f"/api/documents/{doc_id}/collab?field=body"


class _ViaTestClient(httpx.AsyncBaseTransport):
    """Send the MCP server's requests to the app the TestClient is running.

    Lets one test hold an editor's WebSocket open and call an MCP tool against
    the same CMS process.
    """

    def __init__(self, client: TestClient) -> None:
        self._client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        resp = await asyncio.to_thread(
            self._client.request,
            request.method,
            request.url.raw_path.decode(),
            content=body,
            headers=dict(request.headers),
        )
        return httpx.Response(resp.status_code, headers=resp.headers, content=resp.content)


def _tool(client: TestClient, name: str, **kwargs: Any) -> Any:
    mcp = build_mcp_server(base_url="http://testserver", transport=_ViaTestClient(client))
    return asyncio.run(mcp._tool_manager._tools[name].fn(**kwargs))


# ---------------------------------------------------------------------------
# The use case
# ---------------------------------------------------------------------------


def test_claude_scaffolds_a_section_while_the_person_keeps_typing(server):
    client, _, doc_id = server

    with client.websocket_connect(_url(doc_id)) as ws:
        assert ws.receive_json()["type"] == "init"
        # The person types at the end of "Intro." (position 7).
        ws.send_json(
            {"type": "steps", "steps": [_insert(7, " More")], "clientID": "joel", "version": 0}
        )
        assert ws.receive_json()["version"] == 1

        result = _tool(
            client,
            "insert_into_document",
            doc_id=doc_id,
            markdown="### Troubleshooting\n\nIf it **fails**, see [the docs](https://x.test).",
            heading="Setup",
        )
        assert result["draft_version"] == 2
        assert result["open_editors"] == 1

        # The editor is sent the insert as an ordinary step and shows it.
        msg = ws.receive_json()
        assert msg["type"] == "steps"
        assert msg["origin"] == "mcp"
        assert msg["clientIDs"] == ["server:mcp"]
        assert msg["version"] == 2

        # A keystroke made before the editor saw the insert is refused, not
        # applied blindly; rebased onto version 2 it lands.
        stale = {"type": "steps", "steps": [_insert(12, "!")], "clientID": "joel", "version": 1}
        ws.send_json(stale)
        assert ws.receive_json() == {"type": "reject", "version": 2, "reason": "version"}
        ws.send_json({**stale, "version": 2})
        assert ws.receive_json()["version"] == 3

    body = _draft(client, doc_id)["body"]["body"]
    assert _blocks(body) == [
        "Intro. More!",
        "Setup",
        "Install it.",
        "Troubleshooting",
        "If it fails, see the docs.",
        "Usage",
    ]
    link = body["content"][4]["content"][-2]
    assert link["marks"] == [{"type": "link", "attrs": {"href": "https://x.test", "title": None}}]


def test_get_document_reads_the_draft_and_its_version(server):
    client, _, doc_id = server
    client.post(f"/api/documents/{doc_id}/publish")
    client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "Draft only."})

    doc = _tool(client, "get_document", doc_id=doc_id)
    assert _blocks(doc["body"]["body"])[-1] == "Draft only."
    assert doc["draft_version"] == 1

    live = _tool(client, "get_document", doc_id=doc_id, published=True)
    assert "Draft only." not in _blocks(live["body"]["body"])


def test_update_document_refuses_a_stale_body_and_points_at_insert(server):
    client, _, doc_id = server
    client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "Newer."})

    result = _tool(
        client,
        "update_document",
        doc_id=doc_id,
        body={"body": START},
        base_version=0,
    )
    assert "error" in result
    assert "insert_into_document" in result["hint"]
    assert _blocks(_draft(client, doc_id)["body"]["body"])[-1] == "Newer."

    # Plain fields need no version.
    ok = _tool(client, "update_document", doc_id=doc_id, body={"title": "New"})
    assert ok["body"]["title"] == "New"


# ---------------------------------------------------------------------------
# The insert endpoint
# ---------------------------------------------------------------------------


def test_insert_with_no_editor_open_writes_the_draft_and_lets_the_session_go(server):
    client, cms, doc_id = server

    resp = client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "Last."})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "document_id": doc_id,
        "field": "body",
        "draft_version": 1,
        "open_editors": 0,
    }
    assert cms.collab_manager.authorities_for(doc_id) == []

    stored = _draft(client, doc_id)
    assert stored["draft_version"] == 1
    assert _blocks(stored["body"]["body"])[-1] == "Last."

    # The next session starts where the insert left the document.
    with client.websocket_connect(_url(doc_id)) as ws:
        init = ws.receive_json()
        assert init["version"] == 1
        assert _blocks(init["doc"])[-1] == "Last."


def test_insert_at_start_and_at_the_last_section(server):
    client, _, doc_id = server
    client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "First.", "at": "start"})
    client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "Run it.", "heading": "usage"})

    assert _blocks(_draft(client, doc_id)["body"]["body"]) == [
        "First.",
        "Intro.",
        "Setup",
        "Install it.",
        "Usage",
        "Run it.",
    ]


def test_insert_into_an_empty_body_replaces_the_blank_paragraph(server):
    client, _, _ = server
    resp = client.post(
        "/api/documents",
        json={
            "doc_type": "post",
            "body": {"title": "Empty", "body": {"type": "doc", "content": [{"type": "paragraph"}]}},
        },
    )
    doc_id = resp.json()["id"]

    client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "# Title\n\nText."})
    assert _blocks(_draft(client, doc_id)["body"]["body"]) == ["Title", "Text."]


def test_insert_refuses_a_missing_heading_and_lists_the_real_ones(server):
    client, _, doc_id = server
    resp = client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "x", "heading": "Nope"})
    assert resp.status_code == 422
    assert "['Setup', 'Usage']" in resp.json()["error"]
    assert _draft(client, doc_id)["draft_version"] == 0


def test_insert_refuses_content_that_breaks_the_schema(server):
    client, _, doc_id = server
    resp = client.post(
        f"/api/documents/{doc_id}/insert",
        json={"content": [{"type": "text", "text": "a bare text node is not a block"}]},
    )
    assert resp.status_code == 422, resp.text


def test_insert_needs_a_field_when_the_type_has_two(server):
    client, _, _ = server
    empty = {"type": "doc", "content": [{"type": "paragraph"}]}
    doc_id = client.post(
        "/api/documents",
        json={"doc_type": "two_bodies", "body": {"title": "T", "intro": empty, "body": empty}},
    ).json()["id"]

    resp = client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "x"})
    assert resp.status_code == 422
    assert resp.json()["fields"] == ["intro", "body"]

    resp = client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "x", "field": "intro"})
    assert resp.status_code == 200, resp.text
    assert _blocks(_draft(client, doc_id)["body"]["intro"]) == ["x"]


def test_insert_needs_write_auth():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cms = CMS(database_url=f"sqlite:///{tmp.name}", auth="apikey", api_key="k", read_auth=False)

    @cms.block("post")
    class Post:
        body: dict = RichTextField()

    app = Starlette(routes=[Mount("/", app=cms.app)], lifespan=cms.lifespan)
    try:
        with TestClient(app) as client:
            doc_id = client.post(
                "/api/documents",
                json={"doc_type": "post", "body": {"body": START}},
                headers={"Authorization": "Bearer k"},
            ).json()["id"]
            resp = client.post(f"/api/documents/{doc_id}/insert", json={"markdown": "x"})
            assert resp.status_code == 401
    finally:
        os.unlink(tmp.name)
