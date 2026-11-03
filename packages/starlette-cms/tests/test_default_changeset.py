"""The default changeset: where new drafts collect when nothing names a changeset.

Against a real in-process CMS, since the point is what ends up in the database.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS, TextField

from starlette_cms.tables import CMSChangeset, CMSChangesetDocument


async def _make_cms(default_changeset: str | None) -> AsyncGenerator[CMS, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        instance = CMS(
            database_url=f"sqlite:///{db_path}",
            auth="none",
            read_auth=False,
            default_changeset=default_changeset,
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
async def staged() -> AsyncGenerator[httpx.AsyncClient, None]:
    """A CMS whose default changeset is "Staging"."""
    async for cms in _make_cms("Staging"):
        app = Starlette(routes=[Mount("/", app=cms.app)])
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            yield c


@pytest_asyncio.fixture
async def plain() -> AsyncGenerator[httpx.AsyncClient, None]:
    """A CMS with no default changeset: the behaviour before this existed."""
    async for cms in _make_cms(None):
        app = Starlette(routes=[Mount("/", app=cms.app)])
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            yield c


async def _create(client: httpx.AsyncClient, title: str = "A post", **headers: str) -> str:
    resp = await client.post(
        "/api/documents",
        json={"doc_type": "article", "body": {"title": title}},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _changesets(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get("/api/changesets?include_documents=true")
    return resp.json()["changesets"]


def _doc_ids(changeset: dict) -> set[str]:
    return {d["id"] for d in changeset["documents"]}


class TestNewDraftsJoinTheDefault:
    async def test_a_new_draft_lands_in_the_default_changeset(self, staged):
        doc = await _create(staged)

        changesets = await _changesets(staged)
        assert [c["title"] for c in changesets] == ["Staging"]
        assert _doc_ids(changesets[0]) == {doc}

    async def test_later_drafts_join_the_same_changeset(self, staged):
        a = await _create(staged, "A")
        b = await _create(staged, "B")

        changesets = await _changesets(staged)
        assert len(changesets) == 1
        assert _doc_ids(changesets[0]) == {a, b}

    async def test_a_changeset_named_in_the_request_wins(self, staged):
        target = (await staged.post("/api/changesets", json={"title": "Gateway run"})).json()["id"]
        doc = await _create(staged, **{"X-Active-Changeset-Id": target})

        changesets = {c["title"]: c for c in await _changesets(staged)}
        assert _doc_ids(changesets["Gateway run"]) == {doc}
        assert "Staging" not in changesets  # the default was never even made

    async def test_without_a_default_a_draft_stays_in_no_changeset(self, plain):
        await _create(plain)
        assert await _changesets(plain) == []

    async def test_two_creates_at_once_make_one_default_changeset(self, staged):
        ids = await asyncio.gather(*[_create(staged, f"Post {i}") for i in range(6)])

        changesets = await _changesets(staged)
        assert [c["title"] for c in changesets] == ["Staging"]
        assert _doc_ids(changesets[0]) == set(ids)

    async def test_a_published_default_is_replaced_by_a_fresh_one(self, staged):
        first = await _create(staged, "First")
        staging = (await _changesets(staged))[0]
        assert (await staged.post(f"/api/changesets/{staging['id']}/publish")).status_code == 200

        second = await _create(staged, "Second")

        open_ones = (await staged.get("/api/changesets?status=open&include_documents=true")).json()[
            "changesets"
        ]
        assert [c["title"] for c in open_ones] == ["Staging"]
        assert open_ones[0]["id"] != staging["id"]
        assert _doc_ids(open_ones[0]) == {second}
        assert first not in _doc_ids(open_ones[0])


class TestEditsWithNoChangesetJoinTheDefault:
    async def test_an_edit_collects_in_the_default_not_in_a_date_titled_changeset(self, staged):
        # A document made before the default existed, in no changeset.
        doc = await _create(staged)
        await staged.delete(f"/api/changesets/{(await _changesets(staged))[0]['id']}")

        resp = await staged.patch(f"/api/documents/{doc}", json={"body": {"title": "Edited"}})

        assert resp.status_code == 200
        changesets = await _changesets(staged)
        assert [c["title"] for c in changesets] == ["Staging"]
        assert _doc_ids(changesets[0]) == {doc}

    async def test_the_client_is_told_only_when_the_default_was_just_made(self, staged):
        a = await _create(staged, "A")
        b = await _create(staged, "B")
        staging_id = (await _changesets(staged))[0]["id"]
        await staged.delete(f"/api/changesets/{staging_id}")

        first = await staged.patch(f"/api/documents/{a}", json={"body": {"title": "A2"}})
        second = await staged.patch(f"/api/documents/{b}", json={"body": {"title": "B2"}})

        assert first.headers.get("x-changeset-title") == "Staging"  # new: adopt it
        assert "x-changeset-id" not in second.headers  # already there: nothing to adopt

    async def test_a_document_already_in_an_open_changeset_stays_there(self, staged):
        other = (await staged.post("/api/changesets", json={"title": "Big rewrite"})).json()["id"]
        doc = await _create(staged, **{"X-Active-Changeset-Id": other})

        await staged.patch(f"/api/documents/{doc}", json={"body": {"title": "Edited"}})

        changesets = {c["title"]: c for c in await _changesets(staged)}
        assert _doc_ids(changesets["Big rewrite"]) == {doc}
        assert "Staging" not in changesets

    async def test_a_stale_active_changeset_falls_back_to_the_default(self, staged):
        doc = await _create(staged)
        staging_id = (await _changesets(staged))[0]["id"]
        await staged.delete(f"/api/changesets/{staging_id}")

        resp = await staged.patch(
            f"/api/documents/{doc}",
            json={"body": {"title": "Edited"}},
            headers={"X-Active-Changeset-Id": "no-such-changeset"},
        )

        assert resp.status_code == 200
        assert [c["title"] for c in await _changesets(staged)] == ["Staging"]

    async def test_staging_a_publish_state_change_also_joins_the_default(self, staged):
        doc = await _create(staged)
        await staged.delete(f"/api/changesets/{(await _changesets(staged))[0]['id']}")

        resp = await staged.post(f"/api/documents/{doc}/draft-publish-state", json={"published": True})

        assert resp.status_code == 200
        changesets = await _changesets(staged)
        assert [c["title"] for c in changesets] == ["Staging"]
        assert _doc_ids(changesets[0]) == {doc}

    async def test_without_a_default_an_edit_still_gets_a_date_titled_changeset(self, plain):
        doc = await _create(plain)

        await plain.patch(f"/api/documents/{doc}", json={"body": {"title": "Edited"}})

        changesets = await _changesets(plain)
        assert len(changesets) == 1
        assert changesets[0]["title"] != "Staging"
        assert _doc_ids(changesets[0]) == {doc}


class TestPublishingOneDocument:
    async def test_it_leaves_the_open_changesets(self, staged):
        keep = await _create(staged, "Keep")
        ship = await _create(staged, "Ship")

        assert (await staged.post(f"/api/documents/{ship}/publish")).status_code == 200

        staging = (await _changesets(staged))[0]
        assert _doc_ids(staging) == {keep}

    async def test_publishing_the_default_afterwards_ships_only_what_is_left(self, staged):
        keep = await _create(staged, "Keep")
        ship = await _create(staged, "Ship")
        await staged.post(f"/api/documents/{ship}/publish")
        staging_id = (await _changesets(staged))[0]["id"]

        assert (await staged.post(f"/api/changesets/{staging_id}/publish")).status_code == 200

        published = {
            d["id"]: d["published"]
            for d in (await staged.get("/api/documents?type=article")).json()["documents"]
        }
        assert published == {keep: True, ship: True}

    async def test_a_published_changeset_keeps_its_history(self, staged):
        doc = await _create(staged)
        staging_id = (await _changesets(staged))[0]["id"]
        await staged.post(f"/api/changesets/{staging_id}/publish")
        # Publishing the same document on its own later must not rewrite that history.
        await staged.post(f"/api/documents/{doc}/publish")

        rows = await CMSChangesetDocument.select().where(
            CMSChangesetDocument.changeset_id == staging_id
        ).run()
        assert [r["document_id"] for r in rows] == [doc]
        status = (await CMSChangeset.select().where(CMSChangeset.id == staging_id).run())[0]["status"]
        assert status == "published"


class TestDefaultChangesetEndpoint:
    async def test_it_returns_the_open_default_with_its_documents(self, staged):
        doc = await _create(staged)

        resp = await staged.get("/api/changesets/default")

        assert resp.status_code == 200
        changeset = resp.json()["changeset"]
        assert changeset["title"] == "Staging"
        assert [d["id"] for d in changeset["documents"]] == [doc]

    async def test_it_is_null_when_there_is_none_and_does_not_create_one(self, staged):
        resp = await staged.get("/api/changesets/default")

        assert resp.json() == {"changeset": None}
        assert await _changesets(staged) == []

    async def test_it_is_null_once_the_default_is_published(self, staged):
        await _create(staged)
        staging_id = (await _changesets(staged))[0]["id"]
        await staged.post(f"/api/changesets/{staging_id}/publish")

        assert (await staged.get("/api/changesets/default")).json() == {"changeset": None}

    async def test_it_is_null_when_no_default_is_configured(self, plain):
        await _create(plain)
        assert (await plain.get("/api/changesets/default")).json() == {"changeset": None}

    async def test_it_does_not_shadow_a_changeset_id(self, staged):
        cs = (await staged.post("/api/changesets", json={"title": "Mine"})).json()["id"]
        assert (await staged.get(f"/api/changesets/{cs}")).json()["title"] == "Mine"


class TestBodyEditsOverTheCollabSocket:
    """A body typed in the editor streams over the collab socket, not PATCH.

    It used to skip the changeset link when the editor had no active changeset
    (it sends ``activeChangesetId: null``), so an edit to a published post was
    saved as a draft in no changeset: Staging looked empty and publishing it
    shipped nothing.
    """

    @staticmethod
    def _client():
        from starlette.testclient import TestClient

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cms = CMS(
            database_url=f"sqlite:///{tmp.name}",
            auth="none",
            read_auth=False,
            default_changeset="Staging",
        )

        @cms.block("article")
        class ArticleBlock:
            title: str = TextField(required=True)

        app = Starlette(routes=[Mount("/", app=cms.app)], lifespan=cms.lifespan)
        return TestClient(app), tmp.name

    @staticmethod
    def _type(ws, version: int, active: str | None = None) -> list[dict]:
        """Send one step batch with *active* as the editor's changeset; return the replies."""
        ws.receive_json() if version == 0 else None  # init
        ws.send_json(
            {
                "type": "steps",
                "steps": [{"stepType": "replace", "from": 0, "to": 0, "slice": {"content": []}}],
                "clientID": "editor",
                "version": version,
                "doc": {"type": "doc", "content": []},
                "activeChangesetId": active,
            }
        )
        replies = [ws.receive_json()]
        ws.send_json({"type": "ping"})
        while (msg := ws.receive_json())["type"] != "pong":
            replies.append(msg)
        return replies

    def _open_ids(self, client) -> dict[str, set[str]]:
        resp = client.get("/api/changesets?status=open&include_documents=true")
        return {c["title"]: {d["id"] for d in c["documents"]} for c in resp.json()["changesets"]}

    def test_an_edit_to_a_published_post_joins_staging(self):
        client, db_path = self._client()
        try:
            with client:
                doc = client.post(
                    "/api/documents", json={"doc_type": "article", "body": {"title": "Post"}}
                ).json()["id"]
                assert client.post(f"/api/documents/{doc}/publish").status_code == 200
                assert self._open_ids(client).get("Staging", set()) == set()

                with client.websocket_connect(f"/api/documents/{doc}/collab?field=body") as ws:
                    replies = self._type(ws, 0)

                assert replies[0]["type"] == "steps"  # the confirmation still comes first
                assert self._open_ids(client)["Staging"] == {doc}
        finally:
            os.unlink(db_path)

    def test_an_edit_after_publishing_from_an_open_editor_joins_again(self):
        client, db_path = self._client()
        try:
            with client:
                doc = client.post(
                    "/api/documents", json={"doc_type": "article", "body": {"title": "Post"}}
                ).json()["id"]
                with client.websocket_connect(f"/api/documents/{doc}/collab?field=body") as ws:
                    self._type(ws, 0)
                    # Published on its own while the editor stays open: it leaves Staging.
                    assert client.post(f"/api/documents/{doc}/publish").status_code == 200
                    assert self._open_ids(client)["Staging"] == set()

                    self._type(ws, 1)

                assert self._open_ids(client)["Staging"] == {doc}
        finally:
            os.unlink(db_path)
