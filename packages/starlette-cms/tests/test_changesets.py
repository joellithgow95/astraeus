"""Tests for the changeset endpoints and scheduler."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS, TextField

from starlette_cms.scheduler import check_scheduled_changesets
from starlette_cms.tables import CMSChangeset, CMSDocument


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cs_cms() -> AsyncGenerator[CMS, None]:
    """CMS with a simple block for changeset testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        instance = CMS(
            database_url=f"sqlite:///{db_path}",
            auth="none",
            read_auth=False,
        )

        @instance.block("article")
        class ArticleBlock:
            title: str = TextField(required=True)

        async with instance.lifespan_context(None):
            yield instance
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest_asyncio.fixture
async def cs_client(cs_cms: CMS) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = Starlette(routes=[Mount("/", app=cs_cms.app)])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


async def _create_doc(client: httpx.AsyncClient) -> str:
    """Helper: create and return a document ID."""
    resp = await client.post(
        "/api/documents",
        json={"doc_type": "article", "body": {"title": "Test Article"}},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Test 1: create changeset
# ---------------------------------------------------------------------------


async def test_create_changeset(cs_client: httpx.AsyncClient) -> None:
    resp = await cs_client.post("/api/changesets", json={"title": "Sprint 1"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "open"
    assert data["title"] == "Sprint 1"
    assert "id" in data
    assert data["documents"] == []
    assert data["publish_at"] is None
    assert data["published_at"] is None


# ---------------------------------------------------------------------------
# Test 2: list changesets with document_count
# ---------------------------------------------------------------------------


async def test_list_changesets(cs_client: httpx.AsyncClient) -> None:
    await cs_client.post("/api/changesets", json={"title": "CS-A"})
    await cs_client.post("/api/changesets", json={"title": "CS-B"})

    resp = await cs_client.get("/api/changesets")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["changesets"]) == 2
    for cs in data["changesets"]:
        assert "document_count" in cs


# ---------------------------------------------------------------------------
# Test 3: list changesets with status filter
# ---------------------------------------------------------------------------


async def test_list_changesets_status_filter(cs_client: httpx.AsyncClient, cs_cms: CMS) -> None:
    await cs_client.post("/api/changesets", json={"title": "Open CS"})

    # Directly insert a published changeset
    now = datetime.now(UTC)
    await CMSChangeset.insert(
        CMSChangeset(
            id="test-published-cs",
            title="Published CS",
            status="published",
            created_at=now,
            publish_at=None,
            published_at=now,
        )
    ).run()

    resp = await cs_client.get("/api/changesets?status=open")
    assert resp.status_code == 200
    changesets = resp.json()["changesets"]
    assert all(cs["status"] == "open" for cs in changesets)
    assert len(changesets) == 1

    resp2 = await cs_client.get("/api/changesets?status=published")
    assert resp2.status_code == 200
    changesets2 = resp2.json()["changesets"]
    assert all(cs["status"] == "published" for cs in changesets2)
    assert len(changesets2) == 1


# ---------------------------------------------------------------------------
# Test 4: get changeset by id — includes documents list
# ---------------------------------------------------------------------------


async def test_get_changeset_includes_documents(cs_client: httpx.AsyncClient) -> None:
    create_resp = await cs_client.post("/api/changesets", json={"title": "CS-Get"})
    cs_id = create_resp.json()["id"]

    doc_id = await _create_doc(cs_client)
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")

    resp = await cs_client.get(f"/api/changesets/{cs_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["documents"]) == 1
    doc = data["documents"][0]
    assert doc["id"] == doc_id
    assert "doc_type" in doc
    assert "slug" in doc


# ---------------------------------------------------------------------------
# Test 5: get changeset — 404 if not found
# ---------------------------------------------------------------------------


async def test_get_changeset_not_found(cs_client: httpx.AsyncClient) -> None:
    resp = await cs_client.get("/api/changesets/nonexistent-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 6: add document to changeset — document appears in GET
# ---------------------------------------------------------------------------


async def test_add_document_to_changeset(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Add"})
    cs_id = cs_resp.json()["id"]
    doc_id = await _create_doc(cs_client)

    resp = await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert any(d["id"] == doc_id for d in data["documents"])


# ---------------------------------------------------------------------------
# Test 7: add document — 409 if duplicate
# ---------------------------------------------------------------------------


async def test_add_document_duplicate_409(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Dup"})
    cs_id = cs_resp.json()["id"]
    doc_id = await _create_doc(cs_client)

    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")
    resp = await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Test 8: add document — 400 if changeset is published
# ---------------------------------------------------------------------------


async def test_add_document_to_published_changeset_400(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Pub"})
    cs_id = cs_resp.json()["id"]

    # Publish the (empty) changeset
    await cs_client.post(f"/api/changesets/{cs_id}/publish")

    doc_id = await _create_doc(cs_client)
    resp = await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 9: remove document from changeset
# ---------------------------------------------------------------------------


async def test_remove_document_from_changeset(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Remove"})
    cs_id = cs_resp.json()["id"]
    doc_id = await _create_doc(cs_client)

    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")
    resp = await cs_client.delete(f"/api/changesets/{cs_id}/documents/{doc_id}")
    assert resp.status_code == 200

    get_resp = await cs_client.get(f"/api/changesets/{cs_id}")
    assert get_resp.json()["documents"] == []


# ---------------------------------------------------------------------------
# Test 10: delete changeset — changeset gone, documents unaffected
# ---------------------------------------------------------------------------


async def test_delete_changeset(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Del"})
    cs_id = cs_resp.json()["id"]
    doc_id = await _create_doc(cs_client)
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")

    del_resp = await cs_client.delete(f"/api/changesets/{cs_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # Changeset is gone
    assert (await cs_client.get(f"/api/changesets/{cs_id}")).status_code == 404

    # Document still exists
    doc_resp = await cs_client.get(f"/api/documents/{doc_id}")
    assert doc_resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 11: delete changeset — 400 if published
# ---------------------------------------------------------------------------


async def test_delete_published_changeset_400(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-DelPub"})
    cs_id = cs_resp.json()["id"]
    await cs_client.post(f"/api/changesets/{cs_id}/publish")

    resp = await cs_client.delete(f"/api/changesets/{cs_id}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 12: publish changeset — all documents set published=True
# ---------------------------------------------------------------------------


async def test_publish_changeset_publishes_all_docs(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Publish"})
    cs_id = cs_resp.json()["id"]

    doc_id1 = await _create_doc(cs_client)
    doc_id2 = await _create_doc(cs_client)
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id1}")
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id2}")

    pub_resp = await cs_client.post(f"/api/changesets/{cs_id}/publish")
    assert pub_resp.status_code == 200
    assert pub_resp.json()["status"] == "published"

    for doc_id in (doc_id1, doc_id2):
        doc_resp = await cs_client.get(f"/api/documents/{doc_id}")
        assert doc_resp.json()["published"] is True


# ---------------------------------------------------------------------------
# Test 13: publish — draft_body promoted to body before publish
# ---------------------------------------------------------------------------


async def test_publish_changeset_promotes_draft_body(cs_cms: CMS, cs_client: httpx.AsyncClient) -> None:
    """If draft_body column exists and is non-null, it is promoted to body on publish."""
    doc_id = await _create_doc(cs_client)

    # Manually set draft_body on the document if the column exists
    doc_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
    has_draft_col = "draft_body" in doc_rows[0]

    if has_draft_col:
        draft_content = json.dumps({"title": "DRAFT title"})
        await (
            CMSDocument.update({"draft_body": draft_content})
            .where(CMSDocument.id == doc_id)
            .run()
        )

    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Draft"})
    cs_id = cs_resp.json()["id"]
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")
    await cs_client.post(f"/api/changesets/{cs_id}/publish")

    if has_draft_col:
        final_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        final_row = final_rows[0]
        body = final_row["body"]
        if isinstance(body, str):
            body = json.loads(body)
        assert body.get("title") == "DRAFT title"
        assert final_row["draft_body"] is None


# ---------------------------------------------------------------------------
# Test 13b/13c: publish refuses a draft that fails the document model.
#
# Publishing promotes a draft verbatim. A draft no write path validated — the
# collab socket used to write a bare rich-text doc over the whole body — must
# not go live: the publish is refused and nothing changes.
# ---------------------------------------------------------------------------

# The shape the old collab bug left behind: a bare rich-text doc, no title.
_CORRUPT_DRAFT = {"type": "doc", "content": [{"type": "paragraph"}]}


async def test_publish_changeset_refuses_invalid_draft(cs_client: httpx.AsyncClient) -> None:
    doc_id = await _create_doc(cs_client)
    before = (await CMSDocument.select().where(CMSDocument.id == doc_id).run())[0]["body"]
    await (
        CMSDocument.update({"draft_body": json.dumps(_CORRUPT_DRAFT)})
        .where(CMSDocument.id == doc_id)
        .run()
    )

    cs_id = (await cs_client.post("/api/changesets", json={"title": "CS-Invalid"})).json()["id"]
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")
    resp = await cs_client.post(f"/api/changesets/{cs_id}/publish")

    assert resp.status_code == 400
    assert "title" in resp.json()["error"]
    after = (await CMSDocument.select().where(CMSDocument.id == doc_id).run())[0]
    assert after["body"] == before
    changeset = (await CMSChangeset.select().where(CMSChangeset.id == cs_id).run())[0]
    assert changeset["status"] == "open"


async def test_publish_document_refuses_invalid_draft(cs_client: httpx.AsyncClient) -> None:
    doc_id = await _create_doc(cs_client)
    before = (await CMSDocument.select().where(CMSDocument.id == doc_id).run())[0]["body"]
    await (
        CMSDocument.update({"draft_body": json.dumps(_CORRUPT_DRAFT)})
        .where(CMSDocument.id == doc_id)
        .run()
    )

    resp = await cs_client.post(f"/api/documents/{doc_id}/publish")

    assert resp.status_code == 422
    after = (await CMSDocument.select().where(CMSDocument.id == doc_id).run())[0]
    assert after["body"] == before


# ---------------------------------------------------------------------------
# Test 14: publish fires exactly ONE "changeset.published" webhook
# ---------------------------------------------------------------------------


async def test_publish_changeset_fires_one_webhook(cs_cms: CMS, cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Webhook"})
    cs_id = cs_resp.json()["id"]
    doc_id1 = await _create_doc(cs_client)
    doc_id2 = await _create_doc(cs_client)
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id1}")
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id2}")

    # Patch _fire_changeset_webhook at the module level to capture calls synchronously
    fired_calls: list[tuple] = []

    async def fake_fire(cms_arg, event, changeset_id, title, doc_ids, now, **kwargs) -> None:
        fired_calls.append((event, changeset_id, list(doc_ids)))

    import asyncio

    with patch("starlette_cms.api.changesets._fire_changeset_webhook", new=fake_fire):
        pub_resp = await cs_client.post(f"/api/changesets/{cs_id}/publish")
        assert pub_resp.status_code == 200
        # Drain scheduled tasks while mock is still active
        await asyncio.sleep(0)

    assert len(fired_calls) == 1, f"Expected 1 webhook fire, got {len(fired_calls)}"
    event, called_cs_id, called_doc_ids = fired_calls[0]
    assert event == "changeset.published"
    assert called_cs_id == cs_id
    assert set(called_doc_ids) == {doc_id1, doc_id2}


async def test_publish_changeset_reports_staged_deletions_in_its_one_webhook(
    cs_cms: CMS, cs_client: httpx.AsyncClient
) -> None:
    """A staged deletion fires no document.deleted: the changeset's one event lists it."""
    cs_id = (await cs_client.post("/api/changesets", json={"title": "CS-Swap"})).json()["id"]
    new_id = await _create_doc(cs_client)
    old_id = await _create_doc(cs_client)
    await cs_client.post(f"/api/documents/{old_id}/publish")
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{new_id}")
    staged = await cs_client.post(
        f"/api/documents/{old_id}/draft-delete",
        json={"deleted": True},
        headers={"x-active-changeset-id": cs_id},
    )
    assert staged.status_code == 200

    changeset_calls: list[dict] = []
    document_events: list[str] = []

    async def fake_changeset_fire(cms_arg, event, changeset_id, title, doc_ids, now, **kwargs) -> None:
        changeset_calls.append({"event": event, "doc_ids": set(doc_ids), **kwargs})

    async def fake_document_fire(cms_arg, event, *args, **kwargs) -> None:
        document_events.append(event)

    import asyncio

    with (
        patch("starlette_cms.api.changesets._fire_changeset_webhook", new=fake_changeset_fire),
        patch("starlette_cms.api.webhooks.fire_event", new=fake_document_fire),
    ):
        pub_resp = await cs_client.post(f"/api/changesets/{cs_id}/publish")
        assert pub_resp.status_code == 200
        await asyncio.sleep(0)

    assert document_events == []
    assert len(changeset_calls) == 1
    assert changeset_calls[0]["event"] == "changeset.published"
    assert changeset_calls[0]["doc_ids"] == {new_id, old_id}
    assert changeset_calls[0]["deleted_ids"] == [old_id]
    assert (await cs_client.get(f"/api/documents/{old_id}")).status_code == 404
    assert (await cs_client.get(f"/api/documents/{new_id}")).json()["published"] is True


# ---------------------------------------------------------------------------
# Test 15: publish changeset — status becomes "published", cannot publish again
# ---------------------------------------------------------------------------


async def test_publish_changeset_cannot_republish(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Republish"})
    cs_id = cs_resp.json()["id"]
    await cs_client.post(f"/api/changesets/{cs_id}/publish")

    resp = await cs_client.post(f"/api/changesets/{cs_id}/publish")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 16: schedule changeset
# ---------------------------------------------------------------------------


async def test_schedule_changeset(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Schedule"})
    cs_id = cs_resp.json()["id"]

    future_time = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    resp = await cs_client.post(
        f"/api/changesets/{cs_id}/schedule",
        json={"publish_at": future_time},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "scheduled"
    assert data["publish_at"] is not None


# ---------------------------------------------------------------------------
# Test 17: schedule changeset — 400 if publish_at is in the past
# ---------------------------------------------------------------------------


async def test_schedule_changeset_past_time_400(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-PastSchedule"})
    cs_id = cs_resp.json()["id"]

    past_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    resp = await cs_client.post(
        f"/api/changesets/{cs_id}/schedule",
        json={"publish_at": past_time},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 18: unschedule changeset
# ---------------------------------------------------------------------------


async def test_unschedule_changeset(cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Unschedule"})
    cs_id = cs_resp.json()["id"]

    future_time = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    await cs_client.post(
        f"/api/changesets/{cs_id}/schedule",
        json={"publish_at": future_time},
    )

    resp = await cs_client.post(f"/api/changesets/{cs_id}/unschedule")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "open"
    assert data["publish_at"] is None


# ---------------------------------------------------------------------------
# Test 19: check_scheduled_changesets publishes due changesets
# ---------------------------------------------------------------------------


async def test_scheduler_publishes_due_changesets(cs_cms: CMS, cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Due"})
    cs_id = cs_resp.json()["id"]
    doc_id = await _create_doc(cs_client)
    await cs_client.post(f"/api/changesets/{cs_id}/documents/{doc_id}")

    # Set publish_at to the past directly
    past_time = datetime.now(UTC) - timedelta(minutes=5)
    await (
        CMSChangeset.update(
            {
                CMSChangeset.status: "scheduled",
                CMSChangeset.publish_at: past_time,
            }
        )
        .where(CMSChangeset.id == cs_id)
        .run()
    )

    published_ids = await check_scheduled_changesets(cs_cms)
    assert cs_id in published_ids

    # Document should be published
    doc_resp = await cs_client.get(f"/api/documents/{doc_id}")
    assert doc_resp.json()["published"] is True

    # Changeset status should be "published"
    cs_resp2 = await cs_client.get(f"/api/changesets/{cs_id}")
    assert cs_resp2.json()["status"] == "published"


# ---------------------------------------------------------------------------
# Test 20: check_scheduled_changesets ignores non-due changesets
# ---------------------------------------------------------------------------


async def test_scheduler_ignores_non_due_changesets(cs_cms: CMS, cs_client: httpx.AsyncClient) -> None:
    cs_resp = await cs_client.post("/api/changesets", json={"title": "CS-Future"})
    cs_id = cs_resp.json()["id"]

    # Set publish_at to the future
    future_time = datetime.now(UTC) + timedelta(hours=5)
    await (
        CMSChangeset.update(
            {
                CMSChangeset.status: "scheduled",
                CMSChangeset.publish_at: future_time,
            }
        )
        .where(CMSChangeset.id == cs_id)
        .run()
    )

    published_ids = await check_scheduled_changesets(cs_cms)
    assert cs_id not in published_ids

    # Changeset remains scheduled
    cs_resp2 = await cs_client.get(f"/api/changesets/{cs_id}")
    assert cs_resp2.json()["status"] == "scheduled"
