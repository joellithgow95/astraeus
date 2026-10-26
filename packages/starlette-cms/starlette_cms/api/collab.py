"""
WebSocket collaborative editing endpoint and history HTTP endpoints.

Routes added by ``make_collab_routes(cms)``:
  - WS  /api/documents/{document_id}/collab
  - GET  /api/documents/{document_id}/history
  - GET  /api/documents/{document_id}/history/{version}
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from starlette_cms.api.changesets import link_document_to_changeset
from starlette_cms.auth import check_session_auth
from starlette_cms.collab import BASELINE_STEP_TYPE, SERVER_CLIENT_PREFIX, collab_room
from starlette_cms.tables import CMSDocument

if TYPE_CHECKING:
    from starlette_cms.app import CMS

log = structlog.get_logger(__name__)


#: ``client_id`` of the marker row a session writes before its first step.
_BASELINE_CLIENT_ID = f"{SERVER_CLIENT_PREFIX}{BASELINE_STEP_TYPE}"


def _is_editable_rich_text(cms: CMS, doc_type: str, field: str) -> bool:
    """True if *field* is a rich-text field of *doc_type* that is not immutable."""
    try:
        model = cms.registry.get(doc_type)
    except Exception:  # noqa: BLE001 - unknown type
        return False
    info = model.model_fields.get(field)
    if info is None:
        return False
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    meta = extra.get("cms:field_meta") or {}
    if meta.get("field_type") != "rich_text":
        return False
    return field not in getattr(model, "__immutable_fields__", [])


def _check_ws_auth(websocket: WebSocket, cms: CMS) -> bool:
    """Return True if the WebSocket connection is authorised.

    Checks (in order):
    1. ``cms_session`` cookie (session-based auth) — used by the inline editor
       embed on the public site, which carries no API key.
    2. ``?api_key=<key>`` query param (or ``Authorization: Bearer``) when
       ``cms.auth == "apikey"`` — used by the shell editor.
    3. ``cms.auth == "none"``

    The session cookie takes precedence, mirroring ``check_auth`` on the HTTP
    write path. Without it, an ``apikey`` or callable auth mode rejected every
    cookie-authenticated embed connection, so inline body edits silently failed.
    """
    if check_session_auth(websocket, cms):
        return True

    if cms.auth == "none":
        return True

    if cms.auth == "apikey":
        api_key = websocket.query_params.get("api_key")
        if api_key and api_key == cms.api_key:
            return True
        # Also allow Bearer token via headers if present
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer ") and auth_header[7:] == cms.api_key:
            return True
        return False

    if callable(cms.auth):
        # For callable auth we cannot await here (no async), so reject for now.
        # WebSocket clients should authenticate with the session cookie (checked
        # above) or apikey.
        return False

    return False


def make_collab_routes(cms: CMS) -> list:
    """Return the list of Routes for collaborative editing.

    :param cms: The CMS instance (captured in closures).
    """

    # ------------------------------------------------------------------
    # WebSocket endpoint
    # ------------------------------------------------------------------

    async def collab_ws(websocket: WebSocket) -> None:
        """Handle a ProseMirror collab WebSocket connection."""
        document_id: str = websocket.path_params["document_id"]
        # The rich-text field being edited. Required: the editor only ever edits
        # one field, and without knowing which, an accepted step would overwrite
        # the whole document body with that field's doc. Rejecting field-less
        # connections also stops a stale cached client from doing exactly that.
        field = websocket.query_params.get("field")

        # Auth check before accepting
        if not _check_ws_auth(websocket, cms):
            await websocket.close(code=4401)
            return

        if not field:
            await websocket.close(code=4400)
            return

        await websocket.accept()

        # Server-assigned UUID for this connection (used for peer presence).
        # Distinct from the client-generated clientID in step messages.
        client_id = str(uuid.uuid4())
        manager = cms.collab_manager
        # Connections, peers and broadcasts are scoped to this field's session.
        room = collab_room(document_id, field)

        # Load or create the authority for this document's field
        try:
            authority = await manager.get_or_create_authority(document_id, field)
        except KeyError:
            await websocket.send_json({"type": "error", "message": "Document not found"})
            await websocket.close(code=4404)
            return

        # Doc type is needed to group body edits into a changeset (below).
        doc_type_rows = (
            await CMSDocument.select(CMSDocument.doc_type)
            .where(CMSDocument.id == document_id)
            .run()
        )
        doc_type: str = doc_type_rows[0]["doc_type"] if doc_type_rows else ""

        # Verifying mode: the socket may only edit a rich-text field the block
        # type actually defines, and never an immutable one. Without this any
        # authenticated client could name any key and have it written into the body.
        if cms.verify_collab and not _is_editable_rich_text(cms, doc_type, field):
            await websocket.send_json(
                {"type": "error", "message": f"{field!r} is not an editable rich-text field"}
            )
            await websocket.close(code=4403)
            return
        # Changeset this connection's persisted edits are already grouped into,
        # so we link once per changeset rather than on every step batch.
        linked_cs: str | None = None

        # Register connection and bind the server-assigned client_id
        await manager.add_connection(room, websocket)
        manager._bind_client(websocket, client_id)

        # Send initial state including current peer list
        peers = manager.get_peers_for_doc(room)
        await websocket.send_json(
            {
                "type": "init",
                "doc": authority._doc,
                "version": authority._version,
                "peers": peers,
            }
        )

        # Single try/except/finally covers both the presence phase and the
        # message loop so that WebSocketDisconnect at any point is handled
        # cleanly and the finally block always runs.
        try:
            # ── Presence phase ─────────────────────────────────────────────────
            # Wait up to 2 s for the client to send a 'presence' message
            # identifying itself.  Non-presence messages are kept so the main
            # loop can process them; a timeout results in a silent registration.
            display: str = client_id
            peer_type: str = "human"
            first_msg: dict | None = None
            send_peer_joined = False

            try:
                raw = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
                if raw.get("type") == "presence":
                    display = raw.get("display", client_id)
                    peer_type = raw.get("client_type", "human")
                    send_peer_joined = True
                else:
                    # Non-presence message arrived — keep it for the main loop
                    first_msg = raw
            except asyncio.TimeoutError:
                pass  # No message within 2 s — register silently as human

            manager.register_peer(client_id, display, peer_type)
            if send_peer_joined:
                await manager.broadcast_peer_event(
                    room,
                    {
                        "type": "peer_joined",
                        "peer": {
                            "client_id": client_id,
                            "type": peer_type,
                            "display": display,
                        },
                    },
                    exclude=websocket,
                )

            # ── Message loop ───────────────────────────────────────────────────
            while True:
                # Drain any buffered message from the presence phase first
                if first_msg is not None:
                    data = first_msg
                    first_msg = None
                else:
                    data = await websocket.receive_json()

                msg_type = data.get("type")

                # ── Peer presence / activity ───────────────────────────────────
                if msg_type == "presence":
                    p_display = data.get("display", client_id)
                    p_type = data.get("client_type", "human")
                    manager.register_peer(client_id, p_display, p_type)
                    await manager.broadcast_peer_event(
                        room,
                        {
                            "type": "peer_joined",
                            "peer": {
                                "client_id": client_id,
                                "type": p_type,
                                "display": p_display,
                            },
                        },
                        exclude=websocket,
                    )

                elif msg_type == "editing":
                    await manager.broadcast_peer_event(
                        room,
                        {"type": "editing", "client_id": client_id, "doc_id": document_id},
                        exclude=websocket,
                    )

                elif msg_type == "editing_done":
                    await manager.broadcast_peer_event(
                        room,
                        {"type": "editing_done", "client_id": client_id},
                        exclude=websocket,
                    )

                # ── Keep-alive ─────────────────────────────────────────────────
                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                # ── ProseMirror steps ──────────────────────────────────────────
                elif msg_type == "steps":
                    steps = data.get("steps", [])
                    step_client_id = data.get("clientID", "unknown")
                    client_version = data.get("version", -1)
                    # Client sends its current doc state after applying steps locally
                    updated_doc = data.get("doc")

                    persist_failed = False
                    async with authority._lock:
                        base_version = authority._version
                        result = authority.apply_steps(
                            steps,
                            step_client_id,
                            client_version,
                            updated_doc
                            if updated_doc is not None or authority._schema is None
                            else None,
                        )
                        if result.accepted:
                            # Persist under the lock: two batches persisted out of
                            # order would store the older document last.
                            try:
                                await authority._persist_steps(steps, step_client_id, base_version)
                            except Exception:
                                persist_failed = True
                                # Don't drop the connection, but don't hide it either:
                                # the in-memory version is now ahead of what is stored.
                                log.exception(
                                    "collab_persist_failed",
                                    document_id=document_id,
                                    field=field,
                                    version=result.version,
                                )

                    if result.accepted:
                        if persist_failed:
                            # The editor shows this; it used to ignore it.
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "code": "persist_failed",
                                    "message": "Edits were accepted but could not be stored",
                                    "version": result.version,
                                }
                            )

                        # Group this body edit into a changeset so a batch publish
                        # includes it — the collab path bypasses the HTTP PATCH that
                        # normally does this. The client sends its active changeset
                        # id; when we create a fresh one, tell the client to adopt
                        # it so title/description edits join the same changeset.
                        active_cs_id = data.get("activeChangesetId")
                        if active_cs_id != linked_cs:
                            try:
                                created = await link_document_to_changeset(
                                    document_id, doc_type, active_cs_id, cms.default_changeset
                                )
                                linked_cs = created[0] if created else active_cs_id
                                if created is not None:
                                    await websocket.send_json(
                                        {"type": "changeset", "id": created[0], "title": created[1]}
                                    )
                            except Exception:
                                pass  # never drop the connection over changeset bookkeeping

                        # Broadcast to ALL connections (including sender — sender
                        # uses this as its confirmation receipt)
                        await manager.broadcast(
                            room,
                            {
                                "type": "steps",
                                "steps": result.steps,
                                "clientIDs": result.client_ids,
                                "version": result.version,
                            },
                        )
                    else:
                        await websocket.send_json(
                            {
                                "type": "reject",
                                "version": result.version,
                                "reason": result.reason,
                            }
                        )

                # ── Catch-up after a reconnect ─────────────────────────────────
                elif msg_type == "catch_up" and authority._schema is not None:
                    since = data.get("version")
                    missed = (
                        authority.steps_since(since) if isinstance(since, int) else None
                    )
                    if missed is None:
                        # Too far behind (or ahead): pending steps cannot be rebased.
                        await websocket.send_json(
                            {
                                "type": "resync_required",
                                "doc": authority._doc,
                                "version": authority._version,
                            }
                        )
                    else:
                        missed_steps, missed_ids = missed
                        await websocket.send_json(
                            {
                                "type": "steps",
                                "steps": missed_steps,
                                "clientIDs": missed_ids,
                                "version": authority._version,
                            }
                        )

        except WebSocketDisconnect:
            pass  # the client went away
        except Exception:
            # Anything else ends the connection too; record it rather than hide a
            # server bug behind what looks like a client disconnect.
            log.exception("collab_socket_failed", document_id=document_id, field=field)
        finally:
            manager.unregister_peer(client_id)
            await manager.remove_connection(room, websocket)
            # Broadcast peer_left to remaining connections after removal
            await manager.broadcast_peer_event(
                room,
                {"type": "peer_left", "client_id": client_id},
            )
            await manager.gc_if_idle(room)

    # ------------------------------------------------------------------
    # Document-events WebSocket — WS /api/events
    # ------------------------------------------------------------------

    async def events_ws(websocket: WebSocket) -> None:
        """Broadcast document-list events (create/update/delete/publish) to subscribers.

        Reuses the same auth as the collab socket. The shell opens one of these
        on boot to keep its document list live regardless of who wrote (human or AI).
        """
        if not _check_ws_auth(websocket, cms):
            await websocket.close(code=4401)
            return

        await websocket.accept()
        cms.event_bus.add(websocket)

        try:
            # Hold the connection open. We only need to read to detect
            # disconnects and answer keep-alive pings; all data flows outbound
            # via event_bus.broadcast().
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except Exception:
            pass  # disconnect / receive error
        finally:
            cms.event_bus.remove(websocket)

    # ------------------------------------------------------------------
    # History endpoint — GET /api/documents/{document_id}/history
    # ------------------------------------------------------------------

    async def history_list(request: Request) -> JSONResponse:
        """Return step history for a document as time-bucketed checkpoints.

        Advisory only. Steps are recorded as the client asserts them — the
        authority never applies them (see ``CollabAuthority.apply_steps``) — so
        this listing is not a verified history. Rows are also never deleted while
        ``draft_version`` resets to 0 on publish, so versions repeat across
        publish cycles. See ADR 021 §5.
        """
        from starlette_cms.auth import require_auth
        from starlette_cms.tables import CMSDocument, CMSStep

        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        document_id: str = request.path_params["document_id"]

        # Verify document exists
        doc_rows = (
            await CMSDocument.select(CMSDocument.id, CMSDocument.draft_version)
            .where(CMSDocument.id == document_id)
            .limit(1)
            .run()
        )
        if not doc_rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        current_version: int = doc_rows[0].get("draft_version") or 0

        # Parse query params
        try:
            limit = min(int(request.query_params.get("limit", "50")), 200)
        except (ValueError, TypeError):
            limit = 50

        before_str = request.query_params.get("before")

        # Fetch step rows
        query = (
            CMSStep.select(
                CMSStep.document_id,
                CMSStep.client_id,
                CMSStep.version,
                CMSStep.created_at,
            )
            .where(
                CMSStep.document_id == document_id,
                CMSStep.client_id != _BASELINE_CLIENT_ID,
            )
            .order_by(CMSStep.version, ascending=False)
            .limit(limit * 20)  # fetch extra for bucketing
        )
        rows = await query.run()

        # Group by 1-minute time buckets
        buckets: list[dict] = []
        current_bucket: dict | None = None

        for row in rows:
            created_at = row["created_at"]
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at)
                except ValueError:
                    created_at = datetime.now(UTC)

            # Bucket key: minute-precision ISO string
            if hasattr(created_at, "replace"):
                bucket_key = created_at.replace(second=0, microsecond=0).isoformat()
            else:
                bucket_key = str(created_at)

            if before_str and bucket_key >= before_str:
                continue

            if current_bucket is None or current_bucket["_key"] != bucket_key:
                if current_bucket is not None:
                    buckets.append(_format_bucket(current_bucket))
                    if len(buckets) >= limit:
                        break
                current_bucket = {
                    "_key": bucket_key,
                    "version_from": row["version"],
                    "version_to": row["version"],
                    "created_at": bucket_key,
                    "step_count": 1,
                    "client_id": row["client_id"],
                }
            else:
                current_bucket["version_from"] = min(
                    current_bucket["version_from"], row["version"]
                )
                current_bucket["version_to"] = max(
                    current_bucket["version_to"], row["version"]
                )
                current_bucket["step_count"] += 1

        if current_bucket is not None and len(buckets) < limit:
            buckets.append(_format_bucket(current_bucket))

        return JSONResponse(
            {
                "document_id": document_id,
                "current_version": current_version,
                "checkpoints": buckets,
            }
        )

    def _format_bucket(b: dict) -> dict:
        return {
            "version_from": b["version_from"],
            "version_to": b["version_to"],
            "created_at": b["created_at"],
            "step_count": b["step_count"],
            "client_id": b["client_id"],
        }

    # ------------------------------------------------------------------
    # History at version — GET /api/documents/{document_id}/history/{version}
    # ------------------------------------------------------------------

    async def history_at_version(request: Request) -> JSONResponse:
        """Return baseline body + all steps up to *version* for client-side replay.

        Do not treat the replay result as authoritative. Three reasons, all
        tracked in ADR 021 §5: the steps are unverified client assertions; the
        baseline here is the *published* body while steps apply to ``draft_body``;
        and because ``draft_version`` resets on publish without ``cms_steps`` being
        cleared, a version can match rows from several publish cycles at once.

        Sessions now write a baseline row before their first step (returned in
        ``baselines``), and rows come back in the order they were written, so
        :mod:`starlette_cms.collab_replay` can rebuild a session exactly.
        """
        from starlette_cms.auth import require_auth
        from starlette_cms.tables import CMSDocument, CMSStep

        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        document_id: str = request.path_params["document_id"]
        try:
            target_version = int(request.path_params["version"])
        except (ValueError, KeyError):
            return JSONResponse({"error": "Invalid version"}, status_code=400)

        # Fetch document (need the published body as baseline)
        doc_rows = (
            await CMSDocument.select(CMSDocument.id, CMSDocument.body)
            .where(CMSDocument.id == document_id)
            .limit(1)
            .run()
        )
        if not doc_rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        raw_body = doc_rows[0].get("body", "{}")
        baseline_body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body

        # Fetch steps up to (and including) the requested version
        # In the order they were written: versions repeat after a publish or a
        # discard resets draft_version, so ordering by version interleaves cycles.
        step_rows = (
            await CMSStep.select(CMSStep.version, CMSStep.step_data, CMSStep.client_id)
            .where(
                CMSStep.document_id == document_id,
                CMSStep.version <= target_version,
            )
            .order_by(CMSStep._meta.primary_key, ascending=True)
            .run()
        )

        steps = []
        versions: list[int] = []
        client_ids: list[str] = []
        baselines: list[dict] = []
        for row in step_rows:
            raw = row.get("step_data", "{}")
            try:
                step_dict = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                step_dict = {}
            if isinstance(step_dict, dict) and step_dict.get("stepType") == BASELINE_STEP_TYPE:
                # Where a session's steps start; not a step. See collab_replay.
                baselines.append(
                    {
                        "index": len(steps),
                        "version": row["version"],
                        "field": step_dict.get("field"),
                        "doc": step_dict.get("doc"),
                    }
                )
                continue
            steps.append(step_dict)
            versions.append(row["version"])
            client_ids.append(row["client_id"])

        return JSONResponse(
            {
                "document_id": document_id,
                "version": target_version,
                "step_count": len(steps),
                "baseline_body": baseline_body,
                "steps": steps,
                "versions": versions,
                "client_ids": client_ids,
                "baselines": baselines,
                "note": (
                    "Steps are in the order they were written. Each entry of "
                    "`baselines` is the document a session's steps start from "
                    "(`index` into `steps`); apply the steps after it in order. "
                    "baseline_body is the published body, which is only right for "
                    "steps with no baseline before them and no write in between. "
                    "starlette_cms.collab_replay does this and reports gaps."
                ),
            }
        )

    return [
        WebSocketRoute("/api/events", endpoint=events_ws),
        WebSocketRoute("/api/documents/{document_id}/collab", endpoint=collab_ws),
        Route(
            "/api/documents/{document_id}/history",
            endpoint=history_list,
            methods=["GET"],
            name="collab_history_list",
        ),
        Route(
            "/api/documents/{document_id}/history/{version}",
            endpoint=history_at_version,
            methods=["GET"],
            name="collab_history_at_version",
        ),
    ]
