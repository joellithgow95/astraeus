"""
Integration tests for DrawingField: one drawing per document (a taste shape),
validated on save and saved per field through PATCH like any plain field.
"""

from __future__ import annotations

import os
import tempfile

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS, DrawingField, TextField

SHAPE = {
    "viewbox": "0 0 100 100",
    "paths": [
        {
            "d": "M 50 10 Q 70 40 50 90 Q 30 40 50 10 Z",
            "stroke": "#1d2126",
            "stroke_width": 1.5,
            "fill": "#7a2e4a",
        },
        {"d": "M 45 30 Q 50 32 55 30", "stroke": "#1d2126", "stroke_width": 1.5, "fill": "none"},
    ],
    "symmetry": "vertical",
}


def _client(cms: CMS) -> httpx.AsyncClient:
    app = Starlette(routes=[Mount("/", app=cms.app)])
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest_asyncio.fixture
async def cms():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        instance = CMS(database_url=f"sqlite:///{db_path}", auth="none")

        @instance.document("taste")
        class Taste:
            name: str = TextField(required=True)
            shape: dict = DrawingField(label="Shape")

        async with instance.lifespan_context(None):
            yield instance
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


async def test_schema_tags_the_field_as_a_drawing(cms):
    async with _client(cms) as client:
        resp = await client.get("/api/schema")
    props = resp.json()["taste"]["schema"]["properties"]
    assert props["shape"]["cms:field_meta"]["field_type"] == "drawing"


async def test_a_document_can_start_without_a_shape(cms):
    async with _client(cms) as client:
        resp = await client.post(
            "/api/documents",
            json={"doc_type": "taste", "slug": "nuggets", "body": {"name": "Nuggets"}},
        )
    assert resp.status_code == 201
    assert resp.json()["body"].get("shape") is None


async def test_patch_saves_the_shape(cms):
    async with _client(cms) as client:
        created = await client.post(
            "/api/documents", json={"doc_type": "taste", "slug": "zin", "body": {"name": "Zin"}}
        )
        doc_id = created.json()["id"]
        resp = await client.patch(f"/api/documents/{doc_id}", json={"body": {"shape": SHAPE}})
        read = await client.get(f"/api/documents/{doc_id}?draft=true")
    assert resp.status_code == 200
    assert read.json()["body"]["shape"] == SHAPE
    assert read.json()["body"]["name"] == "Zin"


@pytest.mark.parametrize(
    "bad",
    [
        {"viewbox": "0 0 100 100", "paths": []},
        {"viewbox": "0 0 100 100", "paths": [{"d": "<script>"}]},
        {"viewbox": "nope", "paths": [{"d": "M 1 1 L 2 2"}]},
        {"viewbox": "0 0 100 100", "paths": [{"d": "M 1 1 L 2 2", "fill": "url(javascript:x)"}]},
        [SHAPE],
    ],
)
async def test_a_malformed_shape_is_refused(cms, bad):
    async with _client(cms) as client:
        resp = await client.post(
            "/api/documents",
            json={"doc_type": "taste", "slug": "bad", "body": {"name": "Bad", "shape": bad}},
        )
    assert resp.status_code == 422
