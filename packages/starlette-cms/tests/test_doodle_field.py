"""
Integration tests for DoodleField and the site_doodles singleton pattern.

Covers:
- a document type using DoodleField stores/round-trips an array of doodles
  through the normal CREATE/PATCH path (no new API needed)
- a singleton block using DoodleField bootstraps, is retrievable, and a
  second publish archives the first version rather than resurrecting it
- the generic list endpoint gotcha this repo's docs warn about: it never
  filters on singleton_status, so archived versions stay `published=True`
  forever — demonstrated here so a future change to that behavior is caught
"""

from __future__ import annotations

import os
import tempfile

import httpx
import pytest_asyncio
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS, DoodleField, TextField

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(cms: CMS) -> httpx.AsyncClient:
    app = Starlette(routes=[Mount("/", app=cms.app)])
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


SAMPLE_DOODLE = {
    "id": "dd_x7k2p9",
    "path_data": "M 12 34 C 18 40, 25 38, 30 30",
    "viewbox": "0 0 100 100",
    "stroke": "#1a1a1a",
    "stroke_width": 2,
    "placement": {"base": {"mode": "absolute", "top": {"value": 12, "unit": "%"}}},
}


@pytest_asyncio.fixture
async def cms_with_doodles():
    """CMS with a 'post' document that has a doodles field, and a
    'site_doodles' singleton block."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        instance = CMS(database_url=f"sqlite:///{db_path}", auth="none")

        @instance.document("post")
        class PostDocument:
            title: str = TextField(required=True)
            doodles: list = DoodleField(label="Doodles")

        @instance.block("site_doodles", singleton=True)
        class SiteDoodles:
            homepage: list = DoodleField(label="Homepage doodles")
            about: list = DoodleField(label="About page doodles")
            blog_index: list = DoodleField(label="Blog index doodles")

        async with instance.lifespan_context(None):
            yield instance
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Per-document doodles — the normal CREATE/PATCH path, no new API
# ---------------------------------------------------------------------------


async def test_create_document_with_no_doodles(cms_with_doodles):
    """doodles is optional — a document with none is valid."""
    async with _make_client(cms_with_doodles) as client:
        resp = await client.post(
            "/api/documents",
            json={"doc_type": "post", "slug": "no-doodles", "body": {"title": "Plain post"}},
        )
    assert resp.status_code == 201
    # None fields are excluded from the stored body (model_dump(exclude_none=True)),
    # same as every other optional field — absent, not a null key.
    assert resp.json()["body"].get("doodles") is None


async def test_create_document_with_doodles(cms_with_doodles):
    async with _make_client(cms_with_doodles) as client:
        resp = await client.post(
            "/api/documents",
            json={
                "doc_type": "post",
                "slug": "with-doodles",
                "body": {"title": "Doodled post", "doodles": [SAMPLE_DOODLE]},
            },
        )
    assert resp.status_code == 201
    assert resp.json()["body"]["doodles"] == [SAMPLE_DOODLE]


async def test_patch_adds_a_doodle(cms_with_doodles):
    async with _make_client(cms_with_doodles) as client:
        create_resp = await client.post(
            "/api/documents",
            json={"doc_type": "post", "slug": "grows-a-doodle", "body": {"title": "Post"}},
        )
        doc_id = create_resp.json()["id"]

        patch_resp = await client.patch(
            f"/api/documents/{doc_id}",
            json={"body": {"doodles": [SAMPLE_DOODLE]}},
        )

    assert patch_resp.status_code == 200
    assert patch_resp.json()["body"]["doodles"] == [SAMPLE_DOODLE]


# ---------------------------------------------------------------------------
# site_doodles singleton
# ---------------------------------------------------------------------------


async def test_bootstrap_singleton_and_read_it_back(cms_with_doodles):
    async with _make_client(cms_with_doodles) as client:
        publish_resp = await client.post(
            "/api/documents/singleton/site_doodles",
            json={"body": {"homepage": [SAMPLE_DOODLE]}},
        )
        assert publish_resp.status_code == 201
        first_id = publish_resp.json()["id"]

        get_resp = await client.get("/api/documents/singleton/site_doodles")

    assert get_resp.status_code == 200
    doc = get_resp.json()
    assert doc["id"] == first_id
    assert doc["body"]["homepage"] == [SAMPLE_DOODLE]
    assert doc["singleton_status"] == "active"


async def test_no_singleton_published_yet_is_404(cms_with_doodles):
    async with _make_client(cms_with_doodles) as client:
        resp = await client.get("/api/documents/singleton/site_doodles")
    assert resp.status_code == 404


async def test_second_publish_archives_the_first_and_becomes_active(cms_with_doodles):
    async with _make_client(cms_with_doodles) as client:
        first = await client.post(
            "/api/documents/singleton/site_doodles",
            json={"body": {"homepage": [SAMPLE_DOODLE]}},
        )
        first_id = first.json()["id"]

        second = await client.post(
            "/api/documents/singleton/site_doodles",
            json={"body": {"homepage": []}},
        )
        second_id = second.json()["id"]

        get_resp = await client.get("/api/documents/singleton/site_doodles")

    assert second_id != first_id
    # get_singleton returns only the new active version, not the archived one.
    assert get_resp.json()["id"] == second_id
    assert get_resp.json()["body"]["homepage"] == []


async def test_generic_list_endpoint_does_not_filter_singleton_status(cms_with_doodles):
    """Documented gotcha: list_documents only filters on `published`, never on
    `singleton_status`, so an archived singleton version stays visible there
    forever. Callers must use /singleton/{block_type}, never this endpoint,
    to get "the current one" for a singleton block type."""
    async with _make_client(cms_with_doodles) as client:
        await client.post(
            "/api/documents/singleton/site_doodles",
            json={"body": {"homepage": [SAMPLE_DOODLE]}},
        )
        await client.post(
            "/api/documents/singleton/site_doodles",
            json={"body": {"homepage": []}},
        )

        list_resp = await client.get("/api/documents", params={"type": "site_doodles"})

    assert list_resp.status_code == 200
    docs = list_resp.json()["documents"]
    # Both the archived first version and the active second version are
    # `published=True`, so both show up — this is the gotcha, not a bug fix.
    assert len(docs) == 2
