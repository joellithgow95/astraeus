"""TagsField, LinksField and the distinct-tags endpoint behind the editor's tag chips."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from httpx import ASGITransport
from starlette_cms import CMS, LinksField, TagsField, TextField


@pytest_asyncio.fixture
async def tl_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cms = CMS(database_url=f"sqlite:///{db_path}", auth="none", read_auth=False)

        @cms.document("post")
        class PostDocument:
            title: str = TextField(required=True)
            tags: list | None = TagsField()
            links: list | None = LinksField()

        @cms.document("note")
        class NoteDocument:
            title: str = TextField(required=True)
            tags: list | None = TagsField()

        async with cms.lifespan_context(None):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=cms.app), base_url="http://test"
            ) as c:
                yield c
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


async def _create(client: httpx.AsyncClient, doc_type: str, slug: str, body: dict) -> dict:
    resp = await client.post(
        "/api/documents", json={"doc_type": doc_type, "slug": slug, "body": body}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# field_meta
# ---------------------------------------------------------------------------


def test_tags_field_meta():
    meta = TagsField(label="Tags", help_text="Topics").field_meta()
    assert meta == {"label": "Tags", "help_text": "Topics", "field_type": "tags"}


def test_links_field_meta():
    assert LinksField().field_meta() == {"field_type": "links"}


# ---------------------------------------------------------------------------
# Validation and storage
# ---------------------------------------------------------------------------


async def test_tags_round_trip_as_list_of_strings(tl_client):
    doc = await _create(tl_client, "post", "a", {"title": "A", "tags": ["ai", "claude-code"]})
    assert doc["body"]["tags"] == ["ai", "claude-code"]


async def test_tags_are_stored_as_written(tl_client):
    # Normalising is the editor's job: a gateway hashes the tags it owns, so a
    # rewrite here would make every re-sync look like a change.
    doc = await _create(tl_client, "post", "a", {"title": "A", "tags": ["Cloud Run"]})
    assert doc["body"]["tags"] == ["Cloud Run"]


async def test_tags_reject_non_strings(tl_client):
    resp = await tl_client.post(
        "/api/documents",
        json={"doc_type": "post", "slug": "a", "body": {"title": "A", "tags": [{"x": 1}]}},
    )
    assert resp.status_code == 422


async def test_links_keep_unknown_keys(tl_client):
    link = {
        "url": "https://simonwillison.net/",
        "title": "Simon Willison's Weblog",
        "description": "Developer blog",
        "date_added": "2026-01-17",
        "tags": ["developer-blogs"],
        "collections": ["random-developer-blogs"],
    }
    doc = await _create(tl_client, "post", "a", {"title": "A", "links": [link]})
    assert doc["body"]["links"] == [link]


async def test_links_need_a_url(tl_client):
    resp = await tl_client.post(
        "/api/documents",
        json={
            "doc_type": "post",
            "slug": "a",
            "body": {"title": "A", "links": [{"title": "No URL"}]},
        },
    )
    assert resp.status_code == 422


async def test_links_only_url_is_required(tl_client):
    doc = await _create(
        tl_client, "post", "a", {"title": "A", "links": [{"url": "https://example.com"}]}
    )
    assert doc["body"]["links"] == [{"url": "https://example.com"}]


async def test_schema_publishes_field_types(tl_client):
    resp = await tl_client.get("/api/schema")
    assert resp.status_code == 200
    text = resp.text
    assert '"field_type":"tags"' in text.replace(" ", "")
    assert '"field_type":"links"' in text.replace(" ", "")


# ---------------------------------------------------------------------------
# GET /api/tags
# ---------------------------------------------------------------------------


async def test_list_tags_counts_once_per_document_most_used_first(tl_client):
    await _create(tl_client, "post", "a", {"title": "A", "tags": ["ai", "tools"]})
    await _create(tl_client, "post", "b", {"title": "B", "tags": ["ai", "ai"]})
    await _create(tl_client, "note", "c", {"title": "C", "tags": ["other-type"]})

    resp = await tl_client.get("/api/tags", params={"type": "post"})
    assert resp.status_code == 200
    assert resp.json() == {"tags": [{"tag": "ai", "count": 2}, {"tag": "tools", "count": 1}]}


async def test_list_tags_includes_draft_tags(tl_client):
    doc = await _create(tl_client, "post", "a", {"title": "A", "tags": ["ai"]})
    assert (await tl_client.post(f"/api/documents/{doc['id']}/publish")).status_code == 200
    patch = await tl_client.patch(
        f"/api/documents/{doc['id']}", json={"body": {"title": "A", "tags": ["ai", "drafted"]}}
    )
    assert patch.status_code == 200

    tags = (await tl_client.get("/api/tags", params={"type": "post"})).json()["tags"]
    assert {"tag": "drafted", "count": 1} in tags
    assert {"tag": "ai", "count": 1} in tags


async def test_list_tags_reads_another_field(tl_client):
    await _create(
        tl_client,
        "post",
        "a",
        {"title": "A", "links": [{"url": "https://x.test"}], "tags": ["ai"]},
    )
    resp = await tl_client.get("/api/tags", params={"type": "post", "field": "title"})
    assert resp.json() == {"tags": []}


async def test_list_tags_requires_type(tl_client):
    resp = await tl_client.get("/api/tags")
    assert resp.status_code == 400


async def test_list_tags_unknown_type_is_empty(tl_client):
    resp = await tl_client.get("/api/tags", params={"type": "nope"})
    assert resp.json() == {"tags": []}


async def test_list_tags_dotted_path_reads_link_tags(tl_client):
    await _create(
        tl_client,
        "post",
        "a",
        {
            "title": "A",
            "tags": ["ai"],
            "links": [
                {"url": "https://a.test", "tags": ["portfolio", "webgl"]},
                {"url": "https://b.test", "tags": ["portfolio"]},
            ],
        },
    )
    resp = await tl_client.get("/api/tags", params={"type": "post", "field": "links.tags"})
    assert resp.json() == {"tags": [{"tag": "portfolio", "count": 1}, {"tag": "webgl", "count": 1}]}


# ---------------------------------------------------------------------------
# LinksField(required_keys=...)
# ---------------------------------------------------------------------------


def test_links_required_keys_in_meta():
    meta = LinksField(required_keys=["title", "date_added"]).field_meta()
    assert meta["required_keys"] == ["title", "date_added"]


def test_links_required_keys_rejects_unknown_key():
    import pytest

    with pytest.raises(ValueError, match="url"):
        LinksField(required_keys=["url"])


async def test_links_required_keys_enforced(tmp_path):
    cms = CMS(database_url=f"sqlite:///{tmp_path / 'r.db'}", auth="none", read_auth=False)

    @cms.document("post")
    class PostDocument:
        title: str = TextField(required=True)
        links: list | None = LinksField(required_keys=["title", "date_added"])

    async with cms.lifespan_context(None):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=cms.app), base_url="http://test"
        ) as c:
            missing = await c.post(
                "/api/documents",
                json={
                    "doc_type": "post",
                    "slug": "a",
                    "body": {"title": "A", "links": [{"url": "https://x.test"}]},
                },
            )
            assert missing.status_code == 422
            ok = await c.post(
                "/api/documents",
                json={
                    "doc_type": "post",
                    "slug": "b",
                    "body": {
                        "title": "B",
                        "links": [
                            {"url": "https://x.test", "title": "X", "date_added": "2026-10-27"}
                        ],
                    },
                },
            )
            assert ok.status_code == 201, ok.text
