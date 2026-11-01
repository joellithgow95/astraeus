"""
Plain fields are live (ADR 027): a PATCH tells every open editor the values it set.

The shell listens on ``/api/events``. A title typed in one tab, or written by
Claude over MCP, has to reach the other tabs without a reload; rich text is not
sent here (it travels as collab steps), and field values never go to webhooks.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from starlette_cms import CMS, RichTextField, TextField


@pytest.fixture()
def server():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cms = CMS(database_url=f"sqlite:///{tmp.name}", auth="none", read_auth=False)

    @cms.block("post")
    class Post:
        title: str = TextField(required=True)
        tagline: str = TextField()
        body: dict = RichTextField()

    app = Starlette(routes=[Mount("/", app=cms.app)], lifespan=cms.lifespan)
    try:
        with TestClient(app) as client:
            doc = client.post(
                "/api/documents",
                json={"doc_type": "post", "slug": "p", "body": {"title": "T"}},
            ).json()
            yield client, doc["id"]
    finally:
        os.unlink(tmp.name)


def _next_event(ws, name: str) -> dict:
    while True:
        msg = ws.receive_json()
        if msg.get("event") == name:
            return msg


def test_a_patch_sends_its_plain_fields_and_slug_to_open_editors(server):
    client, doc_id = server
    with client.websocket_connect("/api/events") as ws:
        resp = client.patch(
            f"/api/documents/{doc_id}",
            json={
                "body": {"title": "Live title", "body": {"type": "doc", "content": []}},
                "slug": "live",
            },
            headers={"X-Client-Id": "tab-1"},
        )
        assert resp.status_code == 200, resp.text

        msg = _next_event(ws, "document.fields")
        assert msg["document_id"] == doc_id
        assert msg["fields"] == {"title": "Live title"}  # rich text travels as steps
        assert msg["slug"] == "live"
        assert msg["client_id"] == "tab-1"
        assert msg["draft_version"] == resp.json()["draft_version"]

        # The list event that follows carries no field values: it is also a
        # webhook payload, and those go to outside URLs.
        updated = _next_event(ws, "document.updated")
        assert "fields" not in updated


def test_a_write_with_no_client_id_is_still_sent(server):
    """MCP and scripts send no client id; every tab must show their change."""
    client, doc_id = server
    with client.websocket_connect("/api/events") as ws:
        client.patch(f"/api/documents/{doc_id}", json={"body": {"tagline": "From MCP"}})
        msg = _next_event(ws, "document.fields")
        assert msg["fields"] == {"tagline": "From MCP"}
        assert msg["client_id"] is None
