"""DateField and TagField: a checked calendar date, and one tag suggested from another type."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from starlette_cms import CMS, DateField, ListField, TagField, TagsField, TextField
from starlette_cms.model_builder import build_block_model


class _Range:
    from_date: str = DateField(required=True)
    to_date: str = DateField(required=True)


RangeBlock = build_block_model("range", _Range)


class _ByTag:
    tag: str = TagField(required=True, suggest_from="link")


ByTagBlock = build_block_model("by_tag", _ByTag)


@pytest_asyncio.fixture
async def dt_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cms = CMS(database_url=f"sqlite:///{db_path}", auth="none", read_auth=False)

        @cms.document("link")
        class LinkDocument:
            title: str = TextField(required=True)
            date_added: str | None = DateField()
            tags: list | None = TagsField()

        @cms.document("post")
        class PostDocument:
            title: str = TextField(required=True)
            data: list | None = ListField(blocks=[RangeBlock, ByTagBlock])

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


async def _post(client: httpx.AsyncClient, doc_type: str, body: dict) -> httpx.Response:
    return await client.post("/api/documents", json={"doc_type": doc_type, "body": body})


# ---------------------------------------------------------------------------
# field_meta
# ---------------------------------------------------------------------------


def test_date_field_meta():
    assert DateField(label="Added").field_meta() == {"label": "Added", "field_type": "date"}


def test_tag_field_meta_names_the_suggestion_source():
    assert TagField(suggest_from="link").field_meta() == {
        "field_type": "tag",
        "suggest_from": "link",
        "suggest_field": "tags",
    }
    assert TagField().field_meta() == {"field_type": "tag"}


async def test_schema_carries_meta_inside_blocks(dt_client):
    schema = (await dt_client.get("/api/schema")).json()["post"]["schema"]
    rng = schema["$defs"]["_Range"]["properties"]["from_date"]["cms:field_meta"]
    assert rng["field_type"] == "date"
    tag = schema["$defs"]["_ByTag"]["properties"]["tag"]["cms:field_meta"]
    assert tag["suggest_from"] == "link"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_date_round_trips(dt_client):
    resp = await _post(dt_client, "link", {"title": "A", "date_added": "2026-10-28"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"]["date_added"] == "2026-10-28"


async def test_optional_date_may_be_absent(dt_client):
    resp = await _post(dt_client, "link", {"title": "A"})
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("bad", ["2026-02-30", "Oct 1", "2026-10-1", "2026-10-28T10:00:00Z", ""])
async def test_date_rejects_what_is_not_a_calendar_date(dt_client, bad):
    resp = await _post(dt_client, "link", {"title": "A", "date_added": bad})
    assert resp.status_code == 422, resp.text
    assert "YYYY-MM-DD" in resp.text


async def test_date_inside_a_block_is_checked(dt_client):
    good = {"block_type": "range", "from_date": "2026-10-01", "to_date": "2026-10-31"}
    resp = await _post(dt_client, "post", {"title": "P", "data": [good]})
    assert resp.status_code == 201, resp.text
    bad = {**good, "to_date": "2026-10-32"}
    resp = await _post(dt_client, "post", {"title": "P", "data": [bad]})
    assert resp.status_code == 422, resp.text


async def test_tag_is_stored_as_written(dt_client):
    block = {"block_type": "by_tag", "tag": "developer-blog"}
    resp = await _post(dt_client, "post", {"title": "P", "data": [block]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"]["data"] == [block]
