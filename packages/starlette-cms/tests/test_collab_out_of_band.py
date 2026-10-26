"""
Writes that bypass an open editor, and replaying the step log afterwards.

Reproduces the 2026-10-26 data loss: four draft posts were edited in the
browser editor, the server logged every step, and the stored drafts ended up
with none of them. The step log replays cleanly onto the stored draft, so the
draft was overwritten *after* the session with the copy the editor had loaded
when it opened: the shell's Save sent the whole form, rich-text field included,
as a PATCH. The PATCH went around the open collab session, which never heard of
it, and nothing told the editor.

These run against a real in-process CMS with server-side verification on, as
prod runs it (``CMS_VERIFY_COLLAB=1``).
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

pytest.importorskip("prosemirror", reason="needs the collab-verify extra")

from starlette_cms import CMS, RichTextField, TextField  # noqa: E402
from starlette_cms.collab_replay import replay_document, replay_rows  # noqa: E402


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _doc(*texts: str) -> dict:
    return {"type": "doc", "content": [_para(t) for t in texts]}


def _insert(pos: int, text: str) -> dict:
    return {
        "stepType": "replace",
        "from": pos,
        "to": pos,
        "slice": {"content": [{"type": "text", "text": text}]},
    }


LOADED = _doc("I found this. [JOEL: how you ran into it.]")


@pytest.fixture()
def server():
    """(client, cms, doc_id): a blog-like post whose body holds LOADED."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cms = CMS(
        database_url=f"sqlite:///{tmp.name}", auth="none", read_auth=False, verify_collab=True
    )

    @cms.block("post")
    class Post:
        title: str = TextField(required=True)
        body: dict = RichTextField()

    app = Starlette(routes=[Mount("/", app=cms.app)], lifespan=cms.lifespan)
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(
                "/api/documents",
                json={"doc_type": "post", "slug": "p", "body": {"title": "T", "body": LOADED}},
            )
            assert resp.status_code == 201, resp.text
            yield client, cms, resp.json()["id"]
    finally:
        os.unlink(tmp.name)


def _url(doc_id: str) -> str:
    return f"/api/documents/{doc_id}/collab?field=body"


def _draft(client: TestClient, doc_id: str) -> dict:
    resp = client.get(f"/api/documents/{doc_id}", params={"draft": "true"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _text(doc: dict) -> str:
    return "\n".join(
        "".join(n.get("text", "") for n in block.get("content", []))
        for block in doc.get("content", [])
    )


def _send(ws, steps, version, client_id="editor"):
    ws.send_json({"type": "steps", "steps": steps, "clientID": client_id, "version": version})
    return ws.receive_json()


def _type(ws, text: str, at: int, version: int) -> int:
    """Type *text* one character per batch, as the editor does; return the new version."""
    for i, ch in enumerate(text):
        msg = _send(ws, [_insert(at + i, ch)], version)
        assert msg["type"] == "steps", msg
        version = msg["version"]
    return version


# ---------------------------------------------------------------------------
# The incident
# ---------------------------------------------------------------------------


def test_a_save_carrying_the_loaded_body_reaches_the_open_editor(server):
    """The shell's Save sent the body it loaded. The editor must hear of it.

    Before the fix the PATCH overwrote the stored draft while the session still
    held the typed text; the editor was told nothing and showed the typed text,
    which vanished once the session closed.
    """
    client, _, doc_id = server

    with client.websocket_connect(_url(doc_id)) as ws:
        assert ws.receive_json()["type"] == "init"
        version = _type(ws, " Typed.", 14, 0)
        assert "I found this. Typed." in _text(_draft(client, doc_id)["body"]["body"])

        resp = client.patch(
            f"/api/documents/{doc_id}", json={"body": {"title": "T2", "body": LOADED}}
        )
        assert resp.status_code == 200, resp.text

        msg = ws.receive_json()
        assert msg["type"] == "steps", "the open editor was not told its text changed"
        assert msg["clientIDs"] == ["server:patch"]
        assert msg["origin"] == "patch"
        assert msg["version"] == version + 1

    # The overwrite is now a logged step, so the typed text can be replayed back.
    assert resp.json()["draft_version"] == version + 1


def test_a_rest_write_to_the_body_is_not_reverted_by_the_next_keystroke(server):
    """An MCP/REST body write used to be silently undone by the editor's next step."""
    client, _, doc_id = server
    replacement = _doc("Written by MCP")

    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        version = _type(ws, "!", 14, 0)

        client.patch(f"/api/documents/{doc_id}", json={"body": {"body": replacement}})
        folded = ws.receive_json()
        assert folded["type"] == "steps"

        # The editor rebases onto the folded step and keeps typing.
        _type(ws, " and edited", 15, folded["version"])
        # A step still based on the old version is refused, not applied blindly.
        stale = _send(ws, [_insert(1, "x")], version)
        assert stale["type"] == "reject" and stale["reason"] == "version"

    assert _text(_draft(client, doc_id)["body"]["body"]) == "Written by MCP and edited"


def test_a_write_to_other_fields_keeps_the_typed_body_and_the_session_version(server):
    """The hero-image case: an MCP write to another field mid-session."""
    client, cms, doc_id = server

    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        version = _type(ws, " Typed.", 14, 0)

        resp = client.patch(f"/api/documents/{doc_id}", json={"body": {"title": "New title"}})
        assert resp.status_code == 200
        # The session owns draft_version: a PATCH used to bump it past the session,
        # so a session reloaded later could not catch the open editor up.
        assert resp.json()["draft_version"] == version

        # The editor's next step at its own version is still accepted.
        assert _send(ws, [_insert(21, "!")], version)["type"] == "steps"

    stored = _draft(client, doc_id)
    assert stored["body"]["title"] == "New title"
    assert _text(stored["body"]["body"]).startswith("I found this. Typed.!")


def test_discarding_a_draft_resets_the_open_editor_instead_of_being_undone(server):
    client, _, doc_id = server
    client.post(f"/api/documents/{doc_id}/publish")

    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        version = _type(ws, "?", 14, 0)
        assert _draft(client, doc_id)["has_draft"]

        assert client.post(f"/api/documents/{doc_id}/discard-draft").status_code == 200
        reset = ws.receive_json()
        assert reset["type"] == "steps" and reset["origin"] == "discard"
        assert reset["version"] == version + 1
        assert not _draft(client, doc_id)["has_draft"]

        # Typing on carries on from the published text, not the discarded draft.
        _type(ws, "!", 14, reset["version"])

    assert _text(_draft(client, doc_id)["body"]["body"]).startswith("I found this.!")


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


async def _replay(cms: CMS, doc_id: str):
    # The TestClient runs the app on its own loop; read the log from a fresh one.
    return await replay_document(doc_id, field="body")


def test_the_step_log_replays_to_the_stored_document_across_sessions_and_writes(server):
    client, cms, doc_id = server

    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        version = _type(ws, " One.", 14, 0)
        client.patch(f"/api/documents/{doc_id}", json={"body": {"body": _doc("Replaced")}})
        version = ws.receive_json()["version"]
        version = _type(ws, " Two.", 9, version)

    # The session ends and is forgotten; a REST write lands with nobody editing.
    cms.collab_manager._authorities.clear()
    client.patch(f"/api/documents/{doc_id}", json={"body": {"body": _doc("Offline")}})

    with client.websocket_connect(_url(doc_id)) as ws:
        init = ws.receive_json()
        _type(ws, " Three.", 8, init["version"])

    stored = _draft(client, doc_id)["body"]["body"]
    assert _text(stored) == "Offline Three."

    import asyncio

    segments = asyncio.run(_replay(cms, doc_id))
    assert [s.complete for s in segments] == [True, True]
    assert _text(segments[0].doc) == "Replaced Two."
    assert segments[1].doc == stored


def test_a_legacy_log_replays_from_the_document_it_was_written_over():
    """The prod logs predate baselines: the clobbered draft is the base they need."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    rows = [
        # An earlier session (another day), then a write that left no step.
        {"id": 1, "version": 1, "step_data": json.dumps(_insert(1, "X")), "created_at": now},
        # The lost session: steps 3.. on the document as it was loaded.
        {"id": 2, "version": 3, "step_data": json.dumps(_insert(14, " Typed")), "created_at": now},
        {"id": 3, "version": 4, "step_data": json.dumps(_insert(20, ".")), "created_at": now},
    ]
    [segment] = replay_rows(rows, base_doc=LOADED, from_version=2)
    assert segment.complete
    assert _text(segment.doc).startswith("I found this. Typed.")

    # Without the right base the old behaviour: garbage or a failed step.
    [wrong] = replay_rows(rows, base_doc=_doc("short"), from_version=0)
    assert not wrong.complete


def test_history_endpoint_lists_baselines_and_write_order(server):
    client, _, doc_id = server
    with client.websocket_connect(_url(doc_id)) as ws:
        ws.receive_json()
        _type(ws, "ab", 14, 0)

    data = client.get(f"/api/documents/{doc_id}/history/100").json()
    assert data["versions"] == [1, 2]
    assert [s["stepType"] for s in data["steps"]] == ["replace", "replace"]
    assert data["baselines"] == [{"index": 0, "version": 0, "field": "body", "doc": LOADED}]

    checkpoints = client.get(f"/api/documents/{doc_id}/history").json()["checkpoints"]
    assert sum(c["step_count"] for c in checkpoints) == 2  # the baseline is not a step
