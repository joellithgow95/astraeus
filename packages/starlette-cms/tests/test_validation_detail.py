"""A custom validator's ValueError comes back as a 422 with its message, not a 500."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from httpx import ASGITransport
from pydantic import field_validator
from starlette_cms import CMS, ListField, TextField
from starlette_cms.model_builder import build_block_model


class _Range:
    start: str = TextField(required=True)


class RangeSlice(build_block_model("range_slice", _Range)):
    @field_validator("start")
    @classmethod
    def _iso(cls, value: str) -> str:
        if len(value) != 10:
            raise ValueError("must be YYYY-MM-DD")
        return value


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        cms = CMS(database_url=f"sqlite:///{db_path}", auth="none", read_auth=False)

        @cms.document("post")
        class Post:
            title: str = TextField(required=True)
            data: list | None = ListField(blocks=[RangeSlice])

        async with cms.lifespan_context(None):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=cms.app), base_url="http://test"
            ) as c:
                yield c
    finally:
        os.unlink(db_path)


BAD = [{"block_type": "range_slice", "start": "2026-10"}]


async def test_create_reports_a_value_error_as_422(client):
    resp = await client.post(
        "/api/documents", json={"doc_type": "post", "body": {"title": "t", "data": BAD}}
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[0]["msg"] == "Value error, must be YYYY-MM-DD"
    assert detail[0]["loc"] == ["data", 0, "range_slice", "start"]


async def test_patch_reports_a_value_error_as_422(client):
    resp = await client.post("/api/documents", json={"doc_type": "post", "body": {"title": "t"}})
    doc_id = resp.json()["id"]
    resp = await client.patch(f"/api/documents/{doc_id}", json={"body": {"data": BAD}})
    assert resp.status_code == 422
    assert "must be YYYY-MM-DD" in resp.json()["detail"][0]["msg"]
