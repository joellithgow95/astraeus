"""
Editing a rich-text field through its authority (ADR 027).

The use case: a person is typing in the editor and asks Claude, over MCP, to
scaffold a new section of the same post, or fix a sentence in it. The change
must appear in their editor as they watch, and nothing they type before,
during or after must be lost. A whole-body PATCH cannot do that (it is built
against what the writer last read), so anchored edits go through the authority
as steps built against the live document.

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
from starlette_cms.mcp.server import BodyEdit, build_mcp_server  # noqa: E402


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


def _type(ws, pos: int, text: str, version: int) -> int:
    ws.send_json(
        {"type": "steps", "steps": [_insert(pos, text)], "clientID": "joel", "version": version}
    )
    msg = ws.receive_json()
    assert msg["type"] == "steps", msg
    return msg["version"]


def test_claude_edits_the_post_while_the_person_keeps_typing(server):
    client, _, doc_id = server

    with client.websocket_connect(_url(doc_id)) as ws:
        assert ws.receive_json()["type"] == "init"
        # The person types at the end of "Intro." (position 7).
        assert _type(ws, 7, " More", 0) == 1

        result = _tool(
            client,
            "edit_document_body",
            doc_id=doc_id,
            edits=[
                BodyEdit(
                    insert="### Troubleshooting\n\nIf it **fails**, see [the docs](https://x.test).",
                    after_heading="Setup",
                ),
                BodyEdit(find="Install it.", replace="Install it with `uv add`."),
            ],
        )
        assert result["draft_version"] == 3
        assert result["open_editors"] == 1

        # The editor is sent both edits as ordinary steps and shows them.
        msg = ws.receive_json()
        assert msg["type"] == "steps"
        assert msg["origin"] == "mcp"
        assert msg["clientIDs"] == ["server:mcp", "server:mcp"]
        assert msg["version"] == 3

        # A keystroke made before the editor saw the edits is refused, not
        # applied blindly; rebased onto version 3 it lands.
        stale = {"type": "steps", "steps": [_insert(12, "!")], "clientID": "joel", "version": 1}
        ws.send_json(stale)
        assert ws.receive_json() == {"type": "reject", "version": 3, "reason": "version"}
        assert _type(ws, 12, "!", 3) == 4

    body = _draft(client, doc_id)["body"]["body"]
    assert _blocks(body) == [
        "Intro. More!",
        "Setup",
        "Install it with uv add.",
        "Troubleshooting",
        "If it fails, see the docs.",
        "Usage",
    ]
    link = body["content"][4]["content"][-2]
    assert link["marks"] == [{"type": "link", "attrs": {"href": "https://x.test", "title": None}}]


def test_an_edit_whose_text_the_person_just_changed_is_refused_whole(server):
    """The anchor is looked up in the live document, never a stale copy."""
    client, _, doc_id = server

    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        _type(ws, 26, " now", 0)  # "Install it." -> "Install it now." in the editor

        result = _tool(
            client,
            "edit_document_body",
            doc_id=doc_id,
            edits=[
                BodyEdit(insert="Added.", at="end"),
                BodyEdit(find="Install it.", replace="Run the installer."),
            ],
        )
        assert "error" in result
        assert result["detail"]["error"].startswith("edit 1:")

    # All or nothing: the insert in the same batch did not land either.
    assert _blocks(_draft(client, doc_id)["body"]["body"])[-1] == "Usage"


def test_get_document_reads_the_draft_and_its_version(server):
    client, _, doc_id = server
    client.post(f"/api/documents/{doc_id}/publish")
    client.post(f"/api/documents/{doc_id}/edit", json={"edits": [{"insert": "Draft only."}]})

    doc = _tool(client, "get_document", doc_id=doc_id)
    assert _blocks(doc["body"]["body"])[-1] == "Draft only."
    assert doc["draft_version"] == 1

    live = _tool(client, "get_document", doc_id=doc_id, published=True)
    assert "Draft only." not in _blocks(live["body"]["body"])


def test_update_document_refuses_a_stale_body_and_points_at_edit(server):
    client, _, doc_id = server
    client.post(f"/api/documents/{doc_id}/edit", json={"edits": [{"insert": "Newer."}]})

    result = _tool(client, "update_document", doc_id=doc_id, body={"body": START}, base_version=0)
    assert "error" in result
    assert "edit_document_body" in result["hint"]
    assert _blocks(_draft(client, doc_id)["body"]["body"])[-1] == "Newer."

    # Plain fields need no version.
    ok = _tool(client, "update_document", doc_id=doc_id, body={"title": "New"})
    assert ok["body"]["title"] == "New"


# ---------------------------------------------------------------------------
# The edit endpoint
# ---------------------------------------------------------------------------


def _edit(client: TestClient, doc_id: str, *edits: dict, **extra: Any):
    return client.post(f"/api/documents/{doc_id}/edit", json={"edits": list(edits), **extra})


def test_editing_with_no_editor_open_writes_the_draft_and_lets_the_session_go(server):
    client, cms, doc_id = server

    resp = _edit(client, doc_id, {"insert": "Last."})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "document_id": doc_id,
        "field": "body",
        "edits": 1,
        "draft_version": 1,
        "open_editors": 0,
    }
    assert cms.collab_manager.authorities_for(doc_id) == []

    stored = _draft(client, doc_id)
    assert stored["draft_version"] == 1
    assert _blocks(stored["body"]["body"])[-1] == "Last."

    # The next session starts where the edit left the document.
    with client.websocket_connect(_url(doc_id)) as ws:
        init = ws.receive_json()
        assert init["version"] == 1
        assert _blocks(init["doc"])[-1] == "Last."


def test_a_refused_edit_names_the_problem_and_changes_nothing(server):
    client, _, doc_id = server
    resp = _edit(client, doc_id, {"insert": "x", "after_heading": "Nope"})
    assert resp.status_code == 422
    assert "['Setup', 'Usage']" in resp.json()["error"]
    assert _draft(client, doc_id)["draft_version"] == 0


def test_editing_needs_a_field_when_the_type_has_two(server):
    client, _, _ = server
    empty = {"type": "doc", "content": [{"type": "paragraph"}]}
    doc_id = client.post(
        "/api/documents",
        json={"doc_type": "two_bodies", "body": {"title": "T", "intro": empty, "body": empty}},
    ).json()["id"]

    resp = _edit(client, doc_id, {"insert": "x"})
    assert resp.status_code == 422
    assert resp.json()["fields"] == ["intro", "body"]

    resp = _edit(client, doc_id, {"insert": "x"}, field="intro")
    assert resp.status_code == 200, resp.text
    assert _blocks(_draft(client, doc_id)["body"]["intro"]) == ["x"]


def test_editing_needs_write_auth():
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
            assert _edit(client, doc_id, {"insert": "x"}).status_code == 401
    finally:
        os.unlink(tmp.name)
