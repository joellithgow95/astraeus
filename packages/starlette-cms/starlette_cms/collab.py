"""
Collaborative editing authority for starlette-cms.

Implements the server side of the prosemirror-collab protocol.
One CollabAuthority instance per (document, rich-text field) manages connected
clients.

Usage::

    manager = CollabManager()
    authority = await manager.get_or_create_authority(document_id, "body_markdown")
    async with authority._lock:
        result = authority.apply_steps(steps, client_id, client_version, updated_doc)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from prosemirror.model import Schema
    from starlette.websockets import WebSocket

log = structlog.get_logger(__name__)

#: Most steps one authority keeps for catch-up. A client further behind than
#: this has to take the server's copy instead of rebasing.
MAX_STEP_LOG = 5000

#: ``stepType`` of the marker row an authority writes to ``cms_step`` before its
#: first step: the field and the document its steps start from. Without it the
#: log cannot be replayed, because nothing records what the first step applies to.
BASELINE_STEP_TYPE = "baseline"

#: Client-id prefix for steps the server makes itself, such as a REST/MCP write
#: folded into an open editing session.
SERVER_CLIENT_PREFIX = "server:"


def collab_room(document_id: str, field: str) -> str:
    """Key for one rich-text field's collab session: authority, connections, peers.

    Scoping by field keeps two editors on the same document from receiving each
    other's steps (which would apply one field's edits to another's doc).
    """
    return f"{document_id}:{field}"


def _effective_body(row: dict) -> dict:
    """Return a document row's draft body if it has one, else its published body."""
    raw = row.get("draft_body")
    if raw is None:
        raw = row.get("body", "{}")
    return (json.loads(raw) if isinstance(raw, str) else raw) or {}


@dataclass
class CollabResult:
    """Result of applying a batch of ProseMirror steps to the authority."""

    accepted: bool
    version: int
    steps: list[dict] = field(default_factory=list)
    #: One entry per step, parallel to ``steps``. ``prosemirror-collab`` matches
    #: IDs to steps by position to tell which of a broadcast's steps are the
    #: receiver's own; a shorter list makes it re-apply its own steps.
    client_ids: list[str] = field(default_factory=list)
    #: Why a batch was refused: ``"version"``, or a description of the step
    #: that did not apply (verifying authority only).
    reason: str | None = None
    #: Verifying authority only: the client's claimed document differed from
    #: the one the steps produce. The server's document was kept.
    mismatch: bool = False


class CollabAuthority:
    """
    Server-side authority for one rich-text field of one document.

    Holds the field's authoritative ProseMirror doc and version counter.
    ``apply_steps`` must be called while the caller holds ``self._lock``.

    The editor only ever edits a single field (e.g. ``body_markdown``), so the
    authority is scoped to that field. It used to hold the whole document body
    and persist the client's field doc *as* the body, which wiped every sibling
    field (title, description, …) on the first body edit.

    :param document_id: The CMS document ID this authority manages.
    :param field: The rich-text body field this authority edits.
    :param field_doc: The field's current ProseMirror doc (dict), or ``None``.
    :param version: Current version number (incremented per accepted step).
    """

    def __init__(
        self,
        document_id: str,
        field: str,
        field_doc: dict | None,
        version: int,
        schema: Schema | None = None,
    ) -> None:
        self.document_id = document_id
        self.field = field
        self._doc = field_doc
        self._version = version
        self._lock = asyncio.Lock()
        self._last_activity = time.monotonic()
        #: When set, steps are applied server-side and the client's document is
        #: only compared, never stored (see :mod:`starlette_cms.collab_verify`).
        self._schema = schema
        # Accepted steps since this authority was created, for catch-up.
        # ``_log[i]`` took the document from version ``_log_base + i`` to ``+ 1``.
        self._log: list[tuple[dict, str]] = []
        self._log_base = version
        # What the first step applies to, written once to ``cms_step`` as a
        # baseline row so the persisted log can be replayed (see collab_replay).
        self._origin_doc = field_doc
        self._origin_version = version
        self._baseline_written = False

    def steps_since(self, version: int) -> tuple[list[dict], list[str]] | None:
        """Accepted steps after *version*, with one client ID per step.

        :returns: ``None`` if *version* is older than the log reaches or newer
            than the server's, in which case the client must take the server's
            copy instead.
        """
        if version > self._version or version < self._log_base:
            return None
        tail = self._log[version - self._log_base :]
        return [step for step, _ in tail], [cid for _, cid in tail]

    def _record(self, steps: list[dict], client_id: str) -> None:
        self._log.extend((step, client_id) for step in steps)
        overflow = len(self._log) - MAX_STEP_LOG
        if overflow > 0:
            del self._log[:overflow]
            self._log_base += overflow

    def apply_steps(
        self,
        steps: list[dict],
        client_id: str,
        client_version: int,
        updated_doc: dict | None,
    ) -> CollabResult:
        """
        Attempt to apply a batch of ProseMirror steps from a client.

        Must be called while holding ``self._lock``.

        :param steps: List of ProseMirror step dicts (each must have ``stepType``).
        :param client_id: Opaque client identifier string.
        :param client_version: The version the client claims to be based on.
        :param updated_doc: The client's document state after applying steps. Stored on
            trust unless the authority verifies, in which case it is only compared.
        :returns: ``CollabResult`` with ``accepted=True`` on success.
        """
        # Version must match exactly
        if client_version != self._version:
            return CollabResult(accepted=False, version=self._version, reason="version")

        # Structural validation: each step must be a dict with a non-empty stepType
        for step in steps:
            if not isinstance(step, dict):
                return CollabResult(accepted=False, version=self._version, reason="malformed step")
            step_type = step.get("stepType")
            if not isinstance(step_type, str) or not step_type:
                return CollabResult(accepted=False, version=self._version, reason="malformed step")

        mismatch = False
        new_doc = updated_doc
        if self._schema is not None:
            from starlette_cms.collab_verify import apply_steps as apply_to_doc
            from starlette_cms.collab_verify import docs_equal

            outcome = apply_to_doc(self._schema, self._doc, steps)
            if not outcome.ok:
                log.warning(
                    "collab_step_refused",
                    document_id=self.document_id,
                    field=self.field,
                    client_id=client_id,
                    reason=outcome.reason,
                )
                return CollabResult(accepted=False, version=self._version, reason=outcome.reason)
            new_doc = outcome.doc
            if updated_doc is not None and not docs_equal(self._schema, new_doc, updated_doc):
                mismatch = True
                log.warning(
                    "collab_doc_mismatch",
                    document_id=self.document_id,
                    field=self.field,
                    client_id=client_id,
                )

        # Accept: advance version and update authoritative doc state
        self._version += len(steps)
        self._doc = new_doc
        self._record(steps, client_id)
        self._last_activity = time.monotonic()

        return CollabResult(
            accepted=True,
            version=self._version,
            steps=steps,
            client_ids=[client_id] * len(steps),
            mismatch=mismatch,
        )

    def replacement_step(self, new_doc: dict | None) -> dict | None:
        """One ``replace`` step that turns this authority's document into *new_doc*.

        Used to fold a write that did not come through the editor (REST, MCP, a
        discarded draft) into the session, so open editors receive it as an
        ordinary step and rebase their pending edits over it. The step covers
        only the range that differs, so pending edits elsewhere rebase cleanly.

        :returns: ``None`` when the documents are already equal.
        :raises ImportError: without the ``collab-verify`` extra.
        """
        from prosemirror.model import Slice
        from prosemirror.transform import ReplaceStep

        from starlette_cms.collab_verify import EMPTY_DOC, editor_schema

        schema = self._schema or editor_schema()
        old = schema.node_from_json(self._doc or EMPTY_DOC)
        new = schema.node_from_json(new_doc or EMPTY_DOC)
        start = old.content.find_diff_start(new.content)
        if start is None:
            return None
        end = old.content.find_diff_end(new.content)
        if end is not None:
            end_a, end_b = end["a"], end["b"]
            overlap = start - min(end_a, end_b)
            if overlap > 0:
                end_a += overlap
                end_b += overlap
            step = ReplaceStep(start, end_a, new.slice(start, end_b))
            result = step.apply(old)
            if not result.failed and result.doc is not None and result.doc.eq(new):
                return dict(step.to_json())
        # The narrow step did not fit the structure: replace everything.
        whole = ReplaceStep(0, old.content.size, Slice(new.content, 0, 0))
        return dict(whole.to_json())

    async def _persist_steps(
        self,
        steps: list[dict],
        client_id: str,
        base_version: int,
        *,
        write_draft: bool = True,
    ) -> None:
        """Persist accepted steps to the DB and update CMSDocument draft fields.

        Call it while holding ``self._lock``, so two batches cannot store their
        documents out of order.

        :param steps: The accepted step dicts.
        :param client_id: The accepting client's ID.
        :param base_version: The version number *before* these steps were applied
            (i.e. ``self._version - len(steps)`` at the time of the call).
        :param write_draft: False to log the steps without writing the draft
            (a discarded draft must stay discarded).
        """
        from starlette_cms.tables import CMSDocument, CMSStep

        now = datetime.now(UTC)
        step_rows = []
        if not self._baseline_written:
            step_rows.append(
                CMSStep(
                    document_id=self.document_id,
                    client_id=f"{SERVER_CLIENT_PREFIX}{BASELINE_STEP_TYPE}",
                    version=self._origin_version,
                    step_data=json.dumps(
                        {
                            "stepType": BASELINE_STEP_TYPE,
                            "field": self.field,
                            "doc": self._origin_doc,
                        }
                    ),
                    created_at=now,
                )
            )
        step_rows += [
            CMSStep(
                document_id=self.document_id,
                client_id=client_id,
                version=base_version + i + 1,
                step_data=json.dumps(step),
                created_at=now,
            )
            for i, step in enumerate(steps)
        ]
        await CMSStep.insert(*step_rows).run()
        self._baseline_written = True
        if not write_draft:
            return

        # Merge this field into the current body instead of replacing the body.
        # Re-read rather than caching the body, so concurrent PATCH edits to
        # sibling fields (title, description, …) are not overwritten.
        rows = (
            await CMSDocument.select(CMSDocument.body, CMSDocument.draft_body)
            .where(CMSDocument.id == self.document_id)
            .limit(1)
            .run()
        )
        if not rows:
            return
        merged_body = {**_effective_body(rows[0]), self.field: self._doc}

        await CMSDocument.update(
            {
                CMSDocument.draft_body: json.dumps(merged_body),
                CMSDocument.draft_version: self._version,
            }
        ).where(CMSDocument.id == self.document_id).run()


class CollabManager:
    """
    Manages one :class:`CollabAuthority` per document and the set of active
    WebSocket connections.

    A single ``CollabManager`` instance lives on the :class:`~starlette_cms.app.CMS`
    object and is shared across all requests.
    """

    def __init__(self, schema: Schema | None = None) -> None:
        #: Set (by ``CMS(verify_collab=True)``) to make every authority apply
        #: steps server-side against this schema.
        self.schema = schema
        self._authorities: dict[str, CollabAuthority] = {}
        self._connections: dict[str, set[WebSocket]] = {}
        self._peer_info: dict[str, dict] = {}
        self._ws_to_client_id: dict = {}  # WebSocket → server-assigned client_id
        self._manager_lock = asyncio.Lock()

    async def get_or_create_authority(self, document_id: str, field: str) -> CollabAuthority:
        """Return the authority for *field* of *document_id*, loading it if needed.

        Authorities are keyed by :func:`collab_room` — one per (document, field),
        matching the room key the WebSocket handler uses for connections.

        :raises KeyError: if no CMSDocument with *document_id* exists.
        """
        room = collab_room(document_id, field)
        if room in self._authorities:
            return self._authorities[room]

        async with self._manager_lock:
            # Re-check inside the lock
            if room in self._authorities:
                return self._authorities[room]

            from starlette_cms.tables import CMSDocument

            rows = (
                await CMSDocument.select(
                    CMSDocument.id,
                    CMSDocument.body,
                    CMSDocument.draft_body,
                    CMSDocument.draft_version,
                )
                .where(CMSDocument.id == document_id)
                .limit(1)
                .run()
            )
            if not rows:
                raise KeyError(document_id)

            row = rows[0]
            field_doc = _effective_body(row).get(field)
            version = row.get("draft_version") or 0
            authority = CollabAuthority(document_id, field, field_doc, version, self.schema)
            self._authorities[room] = authority
            return authority

    def authorities_for(self, document_id: str) -> list[CollabAuthority]:
        """The in-memory authorities (one per edited field) of *document_id*."""
        return [a for a in self._authorities.values() if a.document_id == document_id]

    async def apply_external_update(
        self,
        document_id: str,
        fields: dict[str, Any],
        *,
        source: str,
        write_draft: bool = True,
    ) -> int | None:
        """Fold a write that bypassed the editor into the document's open sessions.

        An authority holds its field in memory and writes it back on every
        accepted step. A REST or MCP write to that field used to go straight to
        the database: the session never heard of it, so the next keystroke wrote
        the authority's copy back over it, and an editor that was told nothing
        kept showing the old text. Instead each changed field becomes one
        ``replace`` step, applied, logged and broadcast like any other, so open
        editors rebase their pending edits over it and the step log stays
        replayable.

        :param fields: New values by field name. Fields no authority holds are
            ignored; ``None`` means an empty document.
        :param source: Who wrote it (``"patch"``, ``"discard"``), recorded as the
            steps' client id ``server:<source>``.
        :param write_draft: False to log and broadcast without writing the draft.
        :returns: The highest version among the document's authorities, or
            ``None`` if no session is open. The caller stores it as
            ``draft_version`` so a reloaded authority starts where editors are.
        """
        authorities = self.authorities_for(document_id)
        if not authorities:
            return None
        client_id = f"{SERVER_CLIENT_PREFIX}{source}"
        for authority in authorities:
            if authority.field not in fields:
                continue
            async with authority._lock:
                try:
                    step = authority.replacement_step(fields[authority.field])
                except ImportError:
                    # No prosemirror port to build a step with: forget the session
                    # so the next connection loads the stored copy.
                    log.warning(
                        "collab_external_update_dropped_session",
                        document_id=document_id,
                        field=authority.field,
                    )
                    self._authorities.pop(collab_room(document_id, authority.field), None)
                    continue
                if step is None:
                    continue
                base_version = authority._version
                result = authority.apply_steps(
                    [step], client_id, base_version, fields[authority.field]
                )
                if not result.accepted:
                    log.error(
                        "collab_external_update_refused",
                        document_id=document_id,
                        field=authority.field,
                        reason=result.reason,
                    )
                    continue
                await authority._persist_steps(
                    [step], client_id, base_version, write_draft=write_draft
                )
            log.info(
                "collab_external_update_folded",
                document_id=document_id,
                field=authority.field,
                source=source,
                version=result.version,
            )
            await self.broadcast(
                collab_room(document_id, authority.field),
                {
                    "type": "steps",
                    "steps": result.steps,
                    "clientIDs": result.client_ids,
                    "version": result.version,
                    "origin": source,
                },
            )
        remaining = self.authorities_for(document_id)
        return max((a._version for a in remaining), default=None)

    async def add_connection(self, document_id: str, ws: WebSocket) -> None:
        """Register a WebSocket connection for *document_id*."""
        async with self._manager_lock:
            if document_id not in self._connections:
                self._connections[document_id] = set()
            self._connections[document_id].add(ws)

    async def remove_connection(self, document_id: str, ws: WebSocket) -> None:
        """Unregister a WebSocket connection. Schedules GC if no connections remain."""
        async with self._manager_lock:
            conns = self._connections.get(document_id, set())
            conns.discard(ws)
            if not conns:
                self._connections.pop(document_id, None)

    async def broadcast(
        self,
        document_id: str,
        message: dict,
        exclude: WebSocket | None = None,
    ) -> None:
        """Send *message* as JSON to all connections for *document_id*.

        :param exclude: If supplied, this WebSocket is skipped.
        """
        conns = set(self._connections.get(document_id, set()))
        for ws in conns:
            if ws is exclude:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                pass  # stale connection — will be cleaned up on disconnect

    def _bind_client(self, ws: WebSocket, client_id: str) -> None:
        """Associate a WebSocket connection with its server-assigned client_id.

        Must be called after :meth:`add_connection` so that
        :meth:`get_peers_for_doc` can map live connections back to peer info.

        :param ws: The active WebSocket connection.
        :param client_id: The server-generated UUID for this connection.
        """
        self._ws_to_client_id[ws] = client_id

    def register_peer(self, client_id: str, display: str, client_type: str) -> None:
        """Store presence information for a connected peer.

        :param client_id: Server-assigned UUID for the connection.
        :param display: Human-readable display name sent by the client.
        :param client_type: ``"human"`` or ``"ai"``.
        """
        self._peer_info[client_id] = {
            "client_id": client_id,
            "display": display,
            "type": client_type,
        }

    def unregister_peer(self, client_id: str) -> None:
        """Remove a peer's presence information and clean up the reverse WS map.

        :param client_id: Server-assigned UUID for the connection.
        """
        self._peer_info.pop(client_id, None)
        stale = [ws for ws, cid in self._ws_to_client_id.items() if cid == client_id]
        for ws in stale:
            self._ws_to_client_id.pop(ws, None)

    def get_peers_for_doc(self, doc_id: str) -> list[dict]:
        """Return peer-info dicts for all registered connections on *doc_id*.

        Uses ``self._connections[doc_id]`` to find active WebSockets, then
        maps each to a ``client_id`` via ``_ws_to_client_id``, and finally
        looks up the full peer dict in ``_peer_info``.

        :param doc_id: The CMS document ID.
        :returns: List of peer dicts (``client_id``, ``display``, ``type``).
        """
        conns = self._connections.get(doc_id, set())
        peers: list[dict] = []
        for ws in conns:
            client_id = self._ws_to_client_id.get(ws)
            if client_id and client_id in self._peer_info:
                peers.append(self._peer_info[client_id])
        return peers

    async def broadcast_peer_event(
        self,
        doc_id: str,
        message: dict,
        exclude: Any | None = None,
    ) -> None:
        """Broadcast a peer-presence event to all connections on *doc_id*.

        Mirrors :meth:`broadcast` but is dedicated to peer events so callers
        can pass a WebSocket (or any sentinel) to ``exclude``.

        :param doc_id: The CMS document ID.
        :param message: JSON-serialisable dict to send.
        :param exclude: If supplied, this WebSocket is skipped.
        """
        conns = set(self._connections.get(doc_id, set()))
        for ws in conns:
            if ws is exclude:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                pass  # stale connection — will be cleaned up on disconnect

    async def gc_if_idle(self, document_id: str, delay: float = 300.0) -> None:
        """After *delay* seconds, evict the authority if no connections remain.

        Fire-and-forget: called from the finally block of the WebSocket handler.
        *document_id* is the room key (:func:`collab_room`), which is what both
        maps are keyed by.
        """

        async def _gc() -> None:
            await asyncio.sleep(delay)
            async with self._manager_lock:
                if document_id not in self._connections:
                    self._authorities.pop(document_id, None)

        asyncio.ensure_future(_gc())


class DocumentEventBus:
    """
    In-process pub/sub for document-list events (create/update/delete/publish).

    Unlike :class:`CollabManager`, which is keyed per-document, this is a single
    list-level channel: the shell subscribes once over ``/api/events`` and
    receives every document event so it can refresh its document list live.

    A single instance lives on the :class:`~starlette_cms.app.CMS` object.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    def add(self, ws: WebSocket) -> None:
        """Register a subscriber WebSocket."""
        self._connections.add(ws)

    def remove(self, ws: WebSocket) -> None:
        """Unregister a subscriber WebSocket."""
        self._connections.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        """Send *payload* as JSON to every subscriber; drop failed connections."""
        for ws in set(self._connections):
            try:
                await ws.send_json(payload)
            except Exception:
                self._connections.discard(ws)  # stale connection
