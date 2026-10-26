"""
Server-side step verification for the collab WebSocket (ADR 024 spike, Path A1).

These run against a real in-process CMS (``CMS(verify_collab=True)``) through
Starlette's TestClient, as AGENTS/CLAUDE.md require for anything touching draft
or sync semantics.  Test names carry the acceptance-suite ids from the ADR.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

pytest.importorskip("prosemirror", reason="needs the collab-verify extra")

from starlette_cms import CMS, RichTextField, TextField  # noqa: E402
from starlette_cms.collab import CollabAuthority  # noqa: E402
from starlette_cms.collab_verify import editor_schema  # noqa: E402


def _para(text: str) -> dict:
    if not text:
        return {"type": "paragraph"}
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _doc(*blocks: dict) -> dict:
    return {"type": "doc", "content": list(blocks)}


QUOTE = _doc(
    {"type": "blockquote", "content": [_para("Roses are red"), _para("Violets are blue")]}
)
HELLO = _doc(_para("Hello world"))


def _insert(pos: int, text: str) -> dict:
    return {
        "stepType": "replace",
        "from": pos,
        "to": pos,
        "slice": {"content": [{"type": "text", "text": text}]},
    }


def _delete(frm: int, to: int) -> dict:
    return {"stepType": "replace", "from": frm, "to": to}


def _make_cms(**kwargs) -> tuple[CMS, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cms = CMS(database_url=f"sqlite:///{tmp.name}", auth="none", read_auth=False, **kwargs)

    @cms.block("article")
    class Article:
        title: str = TextField(required=True)
        body: dict = RichTextField()
        locked: dict = RichTextField(immutable=True)

    return cms, tmp.name


@pytest.fixture()
def server():
    """(client, cms, doc_id) for a document whose ``body`` holds HELLO."""
    cms, db_path = _make_cms(verify_collab=True)
    app = Starlette(routes=[Mount("/", app=cms.app)], lifespan=cms.lifespan)
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(
                "/api/documents",
                json={
                    "doc_type": "article",
                    "slug": "a",
                    "body": {"title": "T", "body": HELLO, "locked": HELLO},
                },
            )
            assert resp.status_code == 201, resp.text
            yield client, cms, resp.json()["id"]
    finally:
        os.unlink(db_path)


def _url(doc_id: str, field: str = "body") -> str:
    return f"/api/documents/{doc_id}/collab?field={field}"


def _draft_body(client: TestClient, doc_id: str) -> dict:
    resp = client.get(f"/api/documents/{doc_id}", params={"draft": "true"})
    assert resp.status_code == 200, resp.text
    body = resp.json()["body"]
    return json.loads(body) if isinstance(body, str) else body


def _send(ws, steps, version, client_id="client-a", doc=None):
    msg = {"type": "steps", "steps": steps, "clientID": client_id, "version": version}
    if doc is not None:
        msg["doc"] = doc
    ws.send_json(msg)
    return ws.receive_json()


# ---------------------------------------------------------------------------
# T1 — batches of steps, as autocorrect and paste produce them
# ---------------------------------------------------------------------------


def test_T1_batch_is_applied_by_the_server_and_every_step_has_its_sender(server):
    client, _, doc_id = server
    batch = [_delete(1, 6), _insert(1, "Howdy")]  # "Hello world" -> "Howdy world"

    with client.websocket_connect(_url(doc_id)) as ws:
        assert ws.receive_json()["type"] == "init"
        msg = _send(ws, batch, 0)

    assert msg["type"] == "steps"
    assert msg["version"] == 2
    assert msg["clientIDs"] == ["client-a", "client-a"]  # one per step (#34)
    stored = _draft_body(client, doc_id)["body"]
    assert stored["content"][0]["content"][0]["text"] == "Howdy world"
    editor_schema().node_from_json(stored).check()  # T9: the stored JSON is a valid document


def test_T1_server_keeps_the_document_the_steps_produce_not_the_one_the_client_claims(server):
    """The trust gap: a client whose copy diverged cannot write its copy to the server."""
    client, _, doc_id = server
    lie = _doc(_para("something the steps never produce"))

    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        _send(ws, [_insert(6, ",")], 0, doc=lie)

    stored = _draft_body(client, doc_id)["body"]
    assert stored["content"][0]["content"][0]["text"] == "Hello, world"


def test_authority_flags_a_client_document_that_differs_from_its_steps():
    authority = CollabAuthority("d", "body", HELLO, 0, schema=editor_schema())
    result = authority.apply_steps([_insert(6, ",")], "c", 0, _doc(_para("Hello world")))
    assert result.accepted and result.mismatch

    honest = CollabAuthority("d", "body", HELLO, 0, schema=editor_schema())
    result = honest.apply_steps([_insert(6, ",")], "c", 0, _doc(_para("Hello, world")))
    assert result.accepted and not result.mismatch


def test_T1_the_stub_steps_the_old_tests_used_are_now_refused(server):
    """``{from: 0, to: 1, slice: []}`` against a paragraph is not a real edit."""
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        msg = _send(ws, [{"stepType": "replace", "from": 0, "to": 1, "slice": {"content": []}}], 0)
    assert msg["type"] == "reject"
    assert "step 0" in msg["reason"]


@pytest.mark.parametrize(
    "bad",
    [
        {"stepType": "nonsense"},
        {"stepType": "replace", "from": 500, "to": 600},
        {"stepType": "addMark", "from": 1, "to": 3, "mark": {"type": "no_such_mark"}},
    ],
)
def test_unparseable_or_out_of_range_steps_are_refused_not_fatal(server, bad):
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        msg = _send(ws, [bad], 0)
        assert msg["type"] == "reject" and msg["version"] == 0
        # the connection survives and a good batch is still accepted
        assert _send(ws, [_insert(6, "!")], 0)["type"] == "steps"


def test_a_batch_is_all_or_nothing(server):
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        msg = _send(ws, [_insert(6, "!"), {"stepType": "replace", "from": 900, "to": 901}], 0)
    assert msg["type"] == "reject" and msg["version"] == 0
    assert _draft_body(client, doc_id)["body"] == HELLO


# ---------------------------------------------------------------------------
# T11 — two edits that are each valid and together break the schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("erase", [_delete(1, 16), _delete(16, 34)], ids=["line 1", "line 2"])
def test_T11_each_erase_is_valid_on_its_own(erase):
    authority = CollabAuthority("d", "body", QUOTE, 0, schema=editor_schema())
    result = authority.apply_steps([erase], "c", 0, None)
    assert result.accepted
    assert len(authority._doc["content"][0]["content"]) == 1


def test_T11_the_edit_that_would_empty_a_blockquote_is_refused():
    cms, db_path = _make_cms(verify_collab=True)
    app = Starlette(routes=[Mount("/", app=cms.app)], lifespan=cms.lifespan)
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            doc_id = client.post(
                "/api/documents",
                json={"doc_type": "article", "slug": "q", "body": {"title": "Q", "body": QUOTE}},
            ).json()["id"]
            ann_deletes_line_1 = _delete(1, 16)  # the blockquote's first paragraph
            with client.websocket_connect(_url(doc_id)) as ann, client.websocket_connect(
                _url(doc_id)
            ) as ben:
                ann.receive_json()
                ben.receive_json()
                accepted = _send(ann, [ann_deletes_line_1], 0, client_id="ann")
                assert accepted["type"] == "steps" and accepted["version"] == 1
                assert ben.receive_json()["type"] == "steps"  # Ben sees Ann's edit

                # Ben (now at version 1) erases the only line left in the quote box.
                ben_deletes_line_2 = _delete(1, 19)
                refused = _send(ben, [ben_deletes_line_2], 1, client_id="ben")

            assert refused["type"] == "reject"
            assert refused["version"] == 1  # nothing advanced
            # refused because the quote box would be empty, not because of bad positions
            assert "Invalid content for node blockquote" in refused["reason"]
            remaining = _draft_body(client, doc_id)["body"]
            assert remaining["content"][0]["type"] == "blockquote"
            assert len(remaining["content"][0]["content"]) == 1
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# T3 — a client that was away while the server moved on
# ---------------------------------------------------------------------------


def test_T3_catch_up_returns_the_missed_steps_with_one_sender_per_step(server):
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id)) as a:
        a.receive_json()
        _send(a, [_insert(6, "!")], 0, client_id="a")
        _send(a, [_insert(1, ">"), _insert(2, ">")], 1, client_id="a")

        with client.websocket_connect(_url(doc_id)) as b:
            init = b.receive_json()
            assert init["version"] == 3
            b.send_json({"type": "catch_up", "version": 0})
            msg = b.receive_json()

    assert msg["type"] == "steps"
    assert msg["version"] == 3
    assert len(msg["steps"]) == 3 and msg["clientIDs"] == ["a", "a", "a"]
    assert msg["steps"][0] == _insert(6, "!")


def test_T3_catch_up_when_already_current_is_empty(server):
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        ws.send_json({"type": "catch_up", "version": 0})
        msg = ws.receive_json()
    assert msg == {"type": "steps", "steps": [], "clientIDs": [], "version": 0}


@pytest.mark.parametrize("since", [-1, 99, "x"])
def test_T3_catch_up_from_a_version_the_server_cannot_serve_asks_for_a_resync(server, since):
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        ws.send_json({"type": "catch_up", "version": since})
        msg = ws.receive_json()
    assert msg["type"] == "resync_required"
    assert msg["doc"] == HELLO and msg["version"] == 0


def test_T3_the_step_log_is_bounded(monkeypatch):
    import starlette_cms.collab as collab

    monkeypatch.setattr(collab, "MAX_STEP_LOG", 3)
    authority = CollabAuthority("d", "body", HELLO, 0, schema=editor_schema())
    for i in range(5):
        assert authority.apply_steps([_insert(1, "x")], "c", i, None).accepted
    assert authority.steps_since(0) is None  # older than the log reaches
    assert authority.steps_since(2) is not None
    assert len(authority.steps_since(2)[0]) == 3


# ---------------------------------------------------------------------------
# T5 — what survives losing the in-memory authority
# ---------------------------------------------------------------------------


def test_T5_a_restarted_authority_reloads_the_servers_document_and_version(server):
    client, cms, doc_id = server
    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        _send(ws, [_insert(6, ",")], 0)

    cms.collab_manager._authorities.clear()  # what a process restart does to memory

    with client.websocket_connect(_url(doc_id)) as ws:
        init = ws.receive_json()
    assert init["version"] == 1
    assert init["doc"]["content"][0]["content"][0]["text"] == "Hello, world"


def test_T5_a_failed_database_write_is_reported_to_the_client(server, monkeypatch):
    client, _, doc_id = server

    async def boom(self, steps, client_id, base_version):
        raise RuntimeError("disk full")

    monkeypatch.setattr(CollabAuthority, "_persist_steps", boom)
    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        ws.send_json({"type": "steps", "steps": [_insert(6, "!")], "clientID": "c", "version": 0})
        first = ws.receive_json()
    assert first == {
        "type": "error",
        "code": "persist_failed",
        "message": "Edits were accepted but could not be stored",
        "version": 1,
    }


# ---------------------------------------------------------------------------
# T13 — which fields the socket may edit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["title", "locked", "no_such_field"])
def test_T13_a_field_that_is_not_an_editable_rich_text_field_is_refused(server, field):
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id, field)) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4403


def test_T13_an_editable_rich_text_field_is_accepted(server):
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id, "body")) as ws:
        assert ws.receive_json()["type"] == "init"


def test_T13_without_verification_the_socket_still_accepts_any_field_name():
    """Documents today's behaviour so the change is visible: opt-in, default unchanged."""
    cms, db_path = _make_cms()  # verify_collab defaults to False
    app = Starlette(routes=[Mount("/", app=cms.app)], lifespan=cms.lifespan)
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            doc_id = client.post(
                "/api/documents",
                json={"doc_type": "article", "slug": "z", "body": {"title": "Z", "body": HELLO}},
            ).json()["id"]
            with client.websocket_connect(_url(doc_id, "title")) as ws:
                assert ws.receive_json()["type"] == "init"
    finally:
        os.unlink(db_path)
