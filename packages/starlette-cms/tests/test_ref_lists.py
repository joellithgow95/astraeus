"""Lists of DocumentRef (ADR 010, 026): write checks, on_delete, nested blocks, resolve_refs."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from httpx import ASGITransport
from starlette_cms import CMS, DocumentRef, ListField, TextField
from starlette_cms.model_builder import build_block_model


class RangeSlice:
    source: str = TextField(required=True)
    start: str = TextField()
    end: str = TextField()


class PickedSlice:
    source: str = TextField(required=True)
    refs: list = ListField(DocumentRef(block_type="observation"))


RangeModel = build_block_model("range_slice", RangeSlice)
PickedModel = build_block_model("picked_slice", PickedSlice)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cms = CMS(database_url=f"sqlite:///{db_path}", auth="none", read_auth=False)

        @cms.document("song")
        class Song:
            track: str = TextField(required=True)

        @cms.document("definition")
        class Definition:
            term: str = TextField(required=True)

        @cms.document("observation")
        class Observation:
            species: str = TextField(required=True)

        @cms.document("playlist")
        class Playlist:
            title: str = TextField(required=True)
            songs: list = ListField(DocumentRef(block_type="song"))

        @cms.document("post")
        class Post:
            title: str = TextField(required=True)
            see_also: list | None = ListField(
                DocumentRef(block_type="definition", on_delete="nullify")
            )
            data: list | None = ListField(blocks=[RangeModel, PickedModel])
            picks: list | None = ListField(DocumentRef(block_type="song", on_delete="nullify"))

        @cms.document("gloss")
        class Gloss:
            note: str = TextField(required=True)
            about: list = ListField(DocumentRef(block_type="definition", on_delete="cascade"))

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


async def _create(client: httpx.AsyncClient, doc_type: str, body: dict) -> httpx.Response:
    return await client.post("/api/documents", json={"doc_type": doc_type, "body": body})


async def _id(client: httpx.AsyncClient, doc_type: str, body: dict) -> str:
    resp = await _create(client, doc_type, body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _songs(client: httpx.AsyncClient, *tracks: str) -> list[str]:
    return [await _id(client, "song", {"track": t}) for t in tracks]


# ---------------------------------------------------------------------------
# field_meta and schema
# ---------------------------------------------------------------------------


def test_ref_list_field_meta():
    meta = ListField(
        DocumentRef(block_type="song", on_delete="nullify", label_field="track"), label="Songs"
    ).field_meta()
    assert meta == {
        "label": "Songs",
        "field_type": "document_ref_list",
        "ref_block_type": "song",
        "on_delete": "nullify",
        "ref_label_field": "track",
    }


def test_block_list_meta_unchanged():
    assert ListField(blocks=[RangeModel]).field_meta() == {"field_type": "block_list"}


def test_document_ref_label_field_meta():
    meta = DocumentRef(block_type="song", label_field="track").field_meta()
    assert meta["ref_label_field"] == "track"
    assert meta["field_type"] == "document_ref"


async def test_schema_lists_ids_as_strings(client):
    resp = await client.get("/api/schema")
    props = resp.json()["playlist"]["schema"]["properties"]
    songs = props["songs"]
    assert {"type": "array", "items": {"type": "string"}} in songs["anyOf"]
    assert songs["cms:field_meta"]["field_type"] == "document_ref_list"
    assert songs["cms:field_meta"]["ref_block_type"] == "song"


# ---------------------------------------------------------------------------
# Validation on write
# ---------------------------------------------------------------------------


async def test_create_keeps_order(client):
    a, b, c = await _songs(client, "A", "B", "C")
    resp = await _create(client, "playlist", {"title": "Mix", "songs": [c, a, b]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"]["songs"] == [c, a, b]


async def test_missing_id_rejected(client):
    (a,) = await _songs(client, "A")
    resp = await _create(client, "playlist", {"title": "Mix", "songs": [a, "nope"]})
    assert resp.status_code == 422
    assert resp.json()["error"] == "songs: referenced document 'nope' not found"


async def test_wrong_type_rejected(client):
    d = await _id(client, "definition", {"term": "Comp Stomp"})
    resp = await _create(client, "playlist", {"title": "Mix", "songs": [d]})
    assert resp.status_code == 422
    assert "expected block_type 'song', got 'definition'" in resp.json()["error"]


async def test_duplicate_rejected(client):
    (a,) = await _songs(client, "A")
    resp = await _create(client, "playlist", {"title": "Mix", "songs": [a, a]})
    assert resp.status_code == 422
    assert "listed more than once" in resp.json()["error"]


async def test_non_string_rejected(client):
    resp = await _create(client, "playlist", {"title": "Mix", "songs": [1]})
    assert resp.status_code == 422


async def test_empty_list_ok(client):
    resp = await _create(client, "playlist", {"title": "Mix", "songs": []})
    assert resp.status_code == 201


async def test_patch_validates(client):
    pid = await _id(client, "playlist", {"title": "Mix", "songs": []})
    resp = await client.patch(f"/api/documents/{pid}", json={"body": {"songs": ["nope"]}})
    assert resp.status_code == 422
    assert "not found" in resp.json()["error"]


async def test_nested_block_refs_validated(client):
    obs = await _id(client, "observation", {"species": "Anna's hummingbird"})
    good = {
        "title": "Birds",
        "data": [
            {"block_type": "range_slice", "source": "spotify", "start": "2026-10-01"},
            {"block_type": "picked_slice", "source": "inat", "refs": [obs]},
        ],
    }
    assert (await _create(client, "post", good)).status_code == 201

    bad = {**good, "data": [good["data"][0], {**good["data"][1], "refs": [obs, "nope"]}]}
    resp = await _create(client, "post", bad)
    assert resp.status_code == 422
    assert resp.json()["error"] == "data[1].refs: referenced document 'nope' not found"


# ---------------------------------------------------------------------------
# on_delete
# ---------------------------------------------------------------------------


async def test_delete_blocked_by_list(client):
    a, b = await _songs(client, "A", "B")
    await _id(client, "playlist", {"title": "Mix", "songs": [a, b]})
    resp = await client.delete(f"/api/documents/{b}")
    assert resp.status_code == 409
    assert resp.json()["error"] == "Cannot delete: referenced by playlist.songs"


async def test_delete_blocked_by_nested_ref(client):
    obs = await _id(client, "observation", {"species": "Heron"})
    await _id(
        client,
        "post",
        {
            "title": "Birds",
            "data": [{"block_type": "picked_slice", "source": "inat", "refs": [obs]}],
        },
    )
    resp = await client.delete(f"/api/documents/{obs}")
    assert resp.status_code == 409
    assert "post.data[0].refs" in resp.json()["error"]


async def test_nullify_drops_id_from_list(client):
    d1 = await _id(client, "definition", {"term": "Comp Stomp"})
    d2 = await _id(client, "definition", {"term": "Bot Bash"})
    d3 = await _id(client, "definition", {"term": "Melee"})
    pid = await _id(client, "post", {"title": "Robots", "see_also": [d1, d2, d3]})

    assert (await client.delete(f"/api/documents/{d2}")).status_code == 204
    body = (await client.get(f"/api/documents/{pid}")).json()["body"]
    assert body["see_also"] == [d1, d3]


async def test_nullify_reaches_drafts(client):
    d1 = await _id(client, "definition", {"term": "Comp Stomp"})
    d2 = await _id(client, "definition", {"term": "Bot Bash"})
    pid = await _id(client, "post", {"title": "Robots"})
    resp = await client.patch(f"/api/documents/{pid}", json={"body": {"see_also": [d1, d2]}})
    assert resp.status_code == 200, resp.text

    assert (await client.delete(f"/api/documents/{d1}")).status_code == 204
    draft = (await client.get(f"/api/documents/{pid}?draft=true")).json()["body"]
    assert draft["see_also"] == [d2]


async def test_cascade_deletes_referrer(client):
    d = await _id(client, "definition", {"term": "Comp Stomp"})
    gid = await _id(client, "gloss", {"note": "same thing", "about": [d]})
    assert (await client.delete(f"/api/documents/{d}")).status_code == 204
    assert (await client.get(f"/api/documents/{gid}")).status_code == 404


async def test_block_wins_before_anything_changes(client):
    """A blocked delete leaves the nullify references to the same document untouched."""

    (a,) = await _songs(client, "A")
    await _id(client, "playlist", {"title": "Mix", "songs": [a]})
    pid = await _id(client, "post", {"title": "Picks", "picks": [a]})

    assert (await client.delete(f"/api/documents/{a}")).status_code == 409
    assert (await client.get(f"/api/documents/{pid}")).json()["body"]["picks"] == [a]


# ---------------------------------------------------------------------------
# Reading: resolve_refs, ids, q
# ---------------------------------------------------------------------------


async def test_resolve_list_in_order(client):
    a, b, c = await _songs(client, "A", "B", "C")
    await _id(client, "playlist", {"title": "Mix", "songs": [c, a, b]})
    resp = await client.get("/api/documents?type=playlist&resolve_refs=songs")
    resolved = resp.json()["documents"][0]["body"]["songs__resolved"]
    assert [r["body"]["track"] for r in resolved] == ["C", "A", "B"]


async def test_ids_param(client):
    a, b, _c = await _songs(client, "A", "B", "C")
    resp = await client.get(f"/api/documents?type=song&ids={a},{b}")
    data = resp.json()
    assert data["total"] == 2
    assert {d["id"] for d in data["documents"]} == {a, b}


async def test_q_matches_body_strings(client):
    await _songs(client, "Harvest Moon", "Heart of Gold", "Old Man")
    resp = await client.get("/api/documents?type=song&q=gold")
    data = resp.json()
    assert data["total"] == 1
    assert data["documents"][0]["body"]["track"] == "Heart of Gold"

    resp = await client.get("/api/documents?type=song&q=OLD&limit=1")
    assert resp.json()["total"] == 2
    assert len(resp.json()["documents"]) == 1
