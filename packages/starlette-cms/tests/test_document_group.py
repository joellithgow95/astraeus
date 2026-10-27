"""Tests for ``cms.document(group=...)``: a document type filed under a sidebar group."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from starlette_cms import CMS, TextField


@pytest_asyncio.fixture
async def cms_and_client() -> AsyncGenerator[tuple[CMS, httpx.AsyncClient], None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cms = CMS(database_url=f"sqlite:///{db_path}", auth="none", read_auth=False)

        @cms.document("post")
        class Post:
            title: str = TextField(required=True)

        @cms.document("song_like", group="Data")
        class SongLike:
            track: str = TextField(required=True)

        async with cms.lifespan_context(None):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=cms.app), base_url="http://test"
            ) as c:
                yield cms, c
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_document_group_is_registered(cms_and_client):
    cms, _ = cms_and_client
    assert cms.registry.get_registration("song_like").group == "Data"
    assert cms.registry.get_registration("post").group is None


@pytest.mark.asyncio
async def test_schema_lists_document_group(cms_and_client):
    _, client = cms_and_client
    schema = (await client.get("/api/schema")).json()
    assert schema["song_like"]["group"] == "Data"
    assert "group" not in schema["post"]


@pytest.mark.asyncio
async def test_single_schema_reports_group(cms_and_client):
    _, client = cms_and_client
    assert (await client.get("/api/schema/song_like")).json()["group"] == "Data"
    assert "group" not in (await client.get("/api/schema/post")).json()


@pytest.mark.asyncio
async def test_grouped_document_accepts_writes(cms_and_client):
    _, client = cms_and_client
    resp = await client.post(
        "/api/documents",
        json={"doc_type": "song_like", "slug": "one", "body": {"track": "Song"}},
    )
    assert resp.status_code == 201, resp.text
