"""Changeset endpoints — /api/changesets

Changesets group multiple documents for atomic publish.  A single webhook
event (``changeset.published``) fires after all documents in the changeset
are published, rather than one event per document. That includes staged
deletions: they are listed in the event's ``deleted_document_ids`` and fire no
``document.deleted`` of their own.
"""

from __future__ import annotations

import asyncio
import difflib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from nanoid import generate
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from starlette_cms.auth import require_auth
from starlette_cms.tables import CMSChangeset, CMSChangesetDocument, CMSDocument, CMSDocumentVersion, CMSWebhook

if TYPE_CHECKING:
    from starlette_cms.app import CMS

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _changeset_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw CMSChangeset row to a JSON-serialisable dict."""
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


async def _get_changeset_documents(changeset_id: str) -> list[dict[str, Any]]:
    """Return minimal document info for all docs in a changeset, with conflict data."""
    join_rows = (
        await CMSChangesetDocument.select()
        .where(CMSChangesetDocument.changeset_id == changeset_id)
        .run()
    )
    if not join_rows:
        return []

    doc_ids = [r["document_id"] for r in join_rows]
    doc_rows = await CMSDocument.select(
        CMSDocument.id,
        CMSDocument.doc_type,
        CMSDocument.slug,
        CMSDocument.draft_body,
        CMSDocument.draft_deleted,
        CMSDocument.draft_published,
        CMSDocument.updated_at,
    ).where(CMSDocument.id.is_in(doc_ids)).run()

    result = []
    for r in doc_rows:
        doc_entry: dict[str, Any] = {
            "id": r["id"],
            "doc_type": r["doc_type"],
            "slug": r.get("slug", ""),
            "has_draft": (
                r.get("draft_body") is not None
                or r.get("draft_published") is not None
                or r.get("draft_deleted") is True
            ),
            "draft_deleted": r.get("draft_deleted"),
            "draft_published": r.get("draft_published"),
        }

        # Conflict detection: find other open changesets containing this doc
        other_cs_rows = (
            await CMSChangesetDocument.select(CMSChangesetDocument.changeset_id)
            .where(
                CMSChangesetDocument.document_id == r["id"],
                CMSChangesetDocument.changeset_id != changeset_id,
            )
            .run()
        )
        if other_cs_rows:
            other_ids = [row["changeset_id"] for row in other_cs_rows]
            other_changesets = (
                await CMSChangeset.select(CMSChangeset.id, CMSChangeset.title)
                .where(
                    CMSChangeset.id.is_in(other_ids),
                    CMSChangeset.status.is_in(["open", "review", "scheduled"]),
                )
                .run()
            )
            if other_changesets:
                doc_entry["also_in"] = [
                    {"changeset_id": cs["id"], "title": cs["title"]}
                    for cs in other_changesets
                ]

        result.append(doc_entry)
    return result


async def _snapshot_document_version(
    doc_id: str,
    body: Any,
    action: str,
    changeset_id: str | None,
    now: datetime,
) -> int:
    """Insert a version snapshot and return the new version number."""
    max_rows = (
        await CMSDocumentVersion.select(CMSDocumentVersion.version)
        .where(CMSDocumentVersion.document_id == doc_id)
        .order_by(CMSDocumentVersion.version, ascending=False)
        .limit(1)
        .run()
    )
    version_num = (max_rows[0]["version"] + 1) if max_rows else 1

    body_str = json.dumps(body) if not isinstance(body, str) else body

    await CMSDocumentVersion.insert(
        CMSDocumentVersion(
            document_id=doc_id,
            version=version_num,
            body=body_str,
            action=action,
            changeset_id=changeset_id,
            created_at=now,
        )
    ).run()
    return version_num


async def _build_full_changeset(row: dict[str, Any]) -> dict[str, Any]:
    """Return a full changeset dict including document list."""
    d = _changeset_to_dict(row)
    d["documents"] = await _get_changeset_documents(row["id"])
    return d


async def _fire_changeset_webhook(
    cms: "CMS",
    event: str,
    changeset_id: str,
    title: str,
    doc_ids: list[str],
    now: datetime,
    deleted_ids: list[str] | None = None,
) -> None:
    """
    Deliver *event* to all matching active webhooks.

    *doc_ids* is every document the changeset held; *deleted_ids* are the ones
    whose staged deletion it carried out (they no longer exist).

    Piccolo doesn't support JSON-contains queries on SQLite, so we filter in Python.
    """
    import starlette_cms.api.webhooks as _webhooks_module

    rows = await CMSWebhook.select().where(CMSWebhook.active == True).run()  # noqa: E712

    payload: dict[str, Any] = {
        "event": event,
        "changeset_id": changeset_id,
        "title": title,
        "document_ids": doc_ids,
        "document_count": len(doc_ids),
        "deleted_document_ids": deleted_ids or [],
        "timestamp": now.isoformat(),
    }

    loop = asyncio.get_running_loop()
    for row in rows:
        raw_events = row.get("events", "[]")
        if isinstance(raw_events, str):
            try:
                events_list = json.loads(raw_events)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "starlette_cms.changesets.webhook_events_parse_failed",
                    webhook_id=row.get("id"),
                )
                events_list = []
        else:
            events_list = raw_events if isinstance(raw_events, list) else []

        if event in events_list:
            task = loop.create_task(_webhooks_module._deliver(row["url"], payload))
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )


async def _publish_changeset_logic(changeset_id: str, cms: "CMS") -> dict[str, Any] | None:
    """
    Core publish logic — shared between the HTTP endpoint and the scheduler.

    Returns the updated changeset dict on success, or None if not found.
    Raises ValueError with a message if the changeset is in the wrong state.
    """
    rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
    if not rows:
        return None

    changeset = rows[0]
    if changeset["status"] not in ("open", "review", "scheduled"):
        raise ValueError(f"Changeset status is {changeset['status']!r}; must be 'open', 'review', or 'scheduled'")

    now = datetime.now(UTC)

    # Collect document IDs
    join_rows = (
        await CMSChangesetDocument.select()
        .where(CMSChangesetDocument.changeset_id == changeset_id)
        .run()
    )
    doc_ids = [r["document_id"] for r in join_rows]

    # Validate every draft before publishing anything: a malformed body must
    # never go live, and one bad document must not leave the changeset
    # half-published. The HTTP endpoint turns this ValueError into a 400.
    for doc_id in doc_ids:
        doc_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not doc_rows or doc_rows[0].get("draft_deleted") is True:
            continue
        draft = doc_rows[0].get("draft_body")
        if draft is None:
            continue
        draft = json.loads(draft) if isinstance(draft, str) else draft
        error = body_validation_error(cms, doc_rows[0]["doc_type"], draft)
        if error is not None:
            name = doc_rows[0].get("slug") or doc_id
            raise ValueError(f"{name!r} can't be published — {error}")

    # Publish each document, promoting draft_body if present
    deleted_ids: list[str] = []
    for doc_id in doc_ids:
        doc_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not doc_rows:
            logger.warning(
                "starlette_cms.changesets.missing_document",
                changeset_id=changeset_id,
                document_id=doc_id,
            )
            continue

        doc_row = doc_rows[0]

        # Snapshot current body BEFORE overwriting
        current_body = doc_row.get("body")
        if isinstance(current_body, str):
            try:
                current_body = json.loads(current_body)
            except Exception:
                current_body = {}
        await _snapshot_document_version(
            doc_id=doc_id,
            body=current_body or {},
            action="publish",
            changeset_id=changeset_id,
            now=now,
        )

        # Handle staged deletion — delete the row instead of publishing
        if doc_row.get("draft_deleted") is True:
            from starlette_cms.api.documents import _check_ref_integrity

            ref_err = await _check_ref_integrity(cms, doc_id, doc_row["doc_type"])
            if ref_err is not None:
                err_data = json.loads(ref_err.body.decode())
                raise ValueError(err_data.get("error", "Ref integrity check failed"))

            await CMSDocument.delete().where(CMSDocument.id == doc_id).run()
            await (
                CMSChangesetDocument.delete()
                .where(CMSChangesetDocument.document_id == doc_id)
                .run()
            )

            # No document.deleted here: like the publishes, the deletion is
            # reported once, in the changeset.published event below.
            deleted_ids.append(doc_id)
            continue

        # Build update fields — default to publishing (backward compat)
        draft_published = doc_row.get("draft_published")
        if draft_published is not None:
            update_fields: dict[Any, Any] = {
                CMSDocument.published: draft_published,
                CMSDocument.draft_published: None,
                CMSDocument.updated_at: now,
            }
            if draft_published:
                update_fields[CMSDocument.published_at] = now
        else:
            update_fields = {
                CMSDocument.published: True,
                CMSDocument.published_at: now,
                CMSDocument.updated_at: now,
            }

        # Promote draft_body → body if present
        draft_body_raw = doc_row.get("draft_body")
        if draft_body_raw is not None:
            if isinstance(draft_body_raw, str):
                try:
                    draft_body_parsed = json.loads(draft_body_raw)
                except Exception:
                    logger.warning(
                        "starlette_cms.changesets.draft_body_parse_failed",
                        doc_id=doc_id,
                    )
                    draft_body_parsed = None
            else:
                draft_body_parsed = draft_body_raw

            if draft_body_parsed is not None:
                update_fields[CMSDocument.body] = json.dumps(draft_body_parsed)
                update_fields[CMSDocument.draft_body] = None
                update_fields[CMSDocument.draft_version] = 0

        await CMSDocument.update(update_fields).where(CMSDocument.id == doc_id).run()

        # Auto-remove this doc from other open changesets (it's now live)
        await (
            CMSChangesetDocument.delete()
            .where(
                CMSChangesetDocument.document_id == doc_id,
                CMSChangesetDocument.changeset_id != changeset_id,
            )
            .run()
        )

    # Mark changeset published
    await (
        CMSChangeset.update(
            {
                CMSChangeset.status: "published",
                CMSChangeset.published_at: now,
            }
        )
        .where(CMSChangeset.id == changeset_id)
        .run()
    )

    # Fire webhook (fire-and-forget)
    loop = asyncio.get_running_loop()
    loop.create_task(
        _fire_changeset_webhook(
            cms,
            "changeset.published",
            changeset_id,
            changeset["title"],
            doc_ids,
            now,
            deleted_ids=deleted_ids,
        )
    )

    # Return updated changeset
    updated_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
    return await _build_full_changeset(updated_rows[0])


def _extract_text(pm_doc: dict[str, Any]) -> str:
    """Walk a ProseMirror doc tree and extract plain text for diffing."""
    parts: list[str] = []

    def _walk(node: dict[str, Any]) -> None:
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
            return
        for child in node.get("content", []):
            _walk(child)
        if node.get("type") in ("paragraph", "heading", "blockquote", "list_item", "bullet_list", "ordered_list"):
            parts.append("\n")

    _walk(pm_doc)
    return "".join(parts).strip()


def body_validation_error(cms: CMS, doc_type: str, body: dict) -> str | None:
    """Return a short message if *body* fails its document model, else ``None``.

    Publishing promotes a draft verbatim, so without this a draft that no write
    path validated (e.g. one the collab socket produced) could go live broken.
    Uses the same model lookup as the document PATCH handler. Doc types with no
    registered model are not validated.
    """
    doc_model = cms._document_types.get(doc_type)
    if doc_model is None and doc_type in cms.registry:
        doc_model = cms.registry.get(doc_type)
    if doc_model is None:
        return None

    try:
        doc_model.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ())) or "body"
        return f"{location}: {first.get('msg')}"
    return None


# Doc types that never participate in changesets (chat is ephemeral).
CHANGESET_EXCLUDED_TYPES = {"chat_session", "chat_message"}

# Serialises get-or-create of the default changeset, so two writes arriving
# together cannot each find none and make two.
_default_changeset_lock = asyncio.Lock()


async def find_default_changeset(title: str) -> dict[str, Any] | None:
    """The open changeset called *title*, oldest first, or ``None``."""
    rows = (
        await CMSChangeset.select()
        .where(CMSChangeset.title == title, CMSChangeset.status == "open")
        .order_by(CMSChangeset.created_at, ascending=True)
        .limit(1)
        .run()
    )
    return rows[0] if rows else None


async def get_or_create_default_changeset(title: str) -> tuple[str, bool]:
    """Return ``(changeset_id, created)`` for the open changeset called *title*.

    Creates it when none is open, which is what happens after the last one was
    published: the next draft starts a fresh one.
    """
    async with _default_changeset_lock:
        existing = await find_default_changeset(title)
        if existing is not None:
            return existing["id"], False

        new_id = generate(size=21)
        await CMSChangeset.insert(
            CMSChangeset(
                id=new_id,
                title=title,
                status="open",
                created_at=datetime.now(UTC),
                publish_at=None,
                published_at=None,
            )
        ).run()
        return new_id, True


async def _link(changeset_id: str, doc_id: str) -> None:
    """Put *doc_id* in *changeset_id* unless it is already there."""
    existing = (
        await CMSChangesetDocument.select()
        .where(
            CMSChangesetDocument.changeset_id == changeset_id,
            CMSChangesetDocument.document_id == doc_id,
        )
        .run()
    )
    if not existing:
        await CMSChangesetDocument.insert(
            CMSChangesetDocument(
                changeset_id=changeset_id,
                document_id=doc_id,
                added_at=datetime.now(UTC),
            )
        ).run()


async def unlink_from_open_changesets(doc_id: str) -> None:
    """Take *doc_id* out of every changeset that has not shipped.

    A document published on its own is live, so it has no change left to carry.
    Left in its changeset, it would show there as pending and be published a
    second time with the rest. (Changeset publish does the same for the document's
    other changesets.) Published and reverted changesets keep their history.
    """
    open_ids = [
        r["id"]
        for r in await CMSChangeset.select(CMSChangeset.id)
        .where(CMSChangeset.status.is_in(["open", "review", "scheduled"]))
        .run()
    ]
    if open_ids:
        await (
            CMSChangesetDocument.delete()
            .where(
                CMSChangesetDocument.document_id == doc_id,
                CMSChangesetDocument.changeset_id.is_in(open_ids),
            )
            .run()
        )


async def link_new_draft_to_default_changeset(
    doc_id: str, doc_type: str, title: str | None
) -> None:
    """Put a freshly created draft in the default changeset, if one is configured.

    Called by document create, which otherwise leaves a draft in no changeset
    until somebody edits it. A caller that names a changeset (a gateway run) does
    not come here.
    """
    if not title or doc_type in CHANGESET_EXCLUDED_TYPES:
        return
    changeset_id, _ = await get_or_create_default_changeset(title)
    await _link(changeset_id, doc_id)


async def link_document_to_changeset(
    doc_id: str,
    doc_type: str,
    active_cs_id: str | None,
    default_title: str | None = None,
) -> tuple[str, str] | None:
    """Ensure *doc_id* is grouped into an open changeset for publishing.

    Shared by the document PATCH handler and the collab (rich-text) WebSocket, so
    both write paths group edits into the same changeset. Without this a
    body-only edit (which streams over the collab socket) would never be linked
    to a changeset and so would be dropped by a batch publish.

    - If *active_cs_id* names an open/review changeset, link the doc to it.
    - Otherwise, if the doc is already in an open changeset, leave it there.
    - Otherwise link it to the default changeset called *default_title* (made if
      none is open), or, with no default configured, to a new date-titled one.
      This also heals a stale *active_cs_id* (one already published or deleted),
      which used to leave the edit orphaned from every changeset.

    :param default_title: the CMS's ``default_changeset``. With it, edits that name
        no changeset collect in one place instead of one date-titled changeset
        per document.
    :returns: ``(changeset_id, title)`` when a NEW changeset was created, so the
        caller can tell the client to adopt it, else ``None``.
    """
    if doc_type in CHANGESET_EXCLUDED_TYPES:
        return None

    if active_cs_id:
        cs_rows = await CMSChangeset.select().where(CMSChangeset.id == active_cs_id).run()
        if cs_rows and cs_rows[0]["status"] in ("open", "review"):
            existing = (
                await CMSChangesetDocument.select()
                .where(
                    CMSChangesetDocument.changeset_id == active_cs_id,
                    CMSChangesetDocument.document_id == doc_id,
                )
                .run()
            )
            if not existing:
                await CMSChangesetDocument.insert(
                    CMSChangesetDocument(
                        changeset_id=active_cs_id,
                        document_id=doc_id,
                        added_at=datetime.now(UTC),
                    )
                ).run()
            return None

    # No usable active changeset — reuse an open one the doc already belongs to.
    in_cs = (
        await CMSChangesetDocument.select(CMSChangesetDocument.changeset_id)
        .where(CMSChangesetDocument.document_id == doc_id)
        .run()
    )
    if in_cs:
        cs_ids = [r["changeset_id"] for r in in_cs]
        open_cs = (
            await CMSChangeset.select(CMSChangeset.id)
            .where(
                CMSChangeset.id.is_in(cs_ids),
                CMSChangeset.status.is_in(["open", "review"]),
            )
            .run()
        )
        if open_cs:
            return None

    if default_title:
        default_id, created = await get_or_create_default_changeset(default_title)
        await _link(default_id, doc_id)
        return (default_id, default_title) if created else None

    # Create a fresh date-titled changeset ("Sep 22", "Sep 22 (2)", …) and link.
    today = datetime.now(UTC)
    auto_title = today.strftime("%b %-d")
    existing_today = (
        await CMSChangeset.select(CMSChangeset.title)
        .where(CMSChangeset.title.like(f"{auto_title}%"))
        .run()
    )
    if existing_today:
        existing_titles = {r["title"] for r in existing_today}
        if auto_title in existing_titles:
            suffix = 2
            while f"{auto_title} ({suffix})" in existing_titles:
                suffix += 1
            auto_title = f"{auto_title} ({suffix})"

    new_cs_id = generate(size=21)
    await CMSChangeset.insert(
        CMSChangeset(
            id=new_cs_id,
            title=auto_title,
            status="open",
            created_at=today,
            publish_at=None,
            published_at=None,
        )
    ).run()
    await CMSChangesetDocument.insert(
        CMSChangesetDocument(
            changeset_id=new_cs_id,
            document_id=doc_id,
            added_at=today,
        )
    ).run()
    return (new_cs_id, auto_title)


# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------


def make_changeset_routes(cms: "CMS") -> list[Route]:
    """Build and return all changeset routes, closed over ``cms``."""

    async def create_changeset(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        try:
            data = await request.json()
        except Exception:
            data = {}

        title = data.get("title", "") if isinstance(data, dict) else ""
        changeset_id = generate(size=21)
        now = datetime.now(UTC)

        await CMSChangeset.insert(
            CMSChangeset(
                id=changeset_id,
                title=title,
                status="open",
                created_at=now,
                publish_at=None,
                published_at=None,
            )
        ).run()

        rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        result = await _build_full_changeset(rows[0])
        return JSONResponse(result, status_code=201)

    async def list_changesets(request: Request) -> JSONResponse:
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        status_filter = request.query_params.get("status")
        include_docs = request.query_params.get("include_documents", "").lower() in ("true", "1")

        query = CMSChangeset.select().order_by(CMSChangeset.created_at, ascending=False)
        if status_filter:
            query = query.where(CMSChangeset.status == status_filter)

        rows = await query.run()

        result = []
        for row in rows:
            d = _changeset_to_dict(row)
            if include_docs:
                d["documents"] = await _get_changeset_documents(row["id"])
                d["document_count"] = len(d["documents"])
            else:
                count = await (
                    CMSChangesetDocument.count()
                    .where(CMSChangesetDocument.changeset_id == row["id"])
                    .run()
                )
                d["document_count"] = count
            result.append(d)

        return JSONResponse({"changesets": result})

    async def get_default_changeset(request: Request) -> JSONResponse:
        """The open default changeset, or ``{"changeset": null}`` when there is none.

        Read-only: it never creates one. The editor asks so Publish can target it
        on a device that has not picked a changeset (a phone, a fresh browser).
        """
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        title = cms.default_changeset
        row = await find_default_changeset(title) if title else None
        if row is None:
            return JSONResponse({"changeset": None})
        return JSONResponse({"changeset": await _build_full_changeset(row)})

    async def get_changeset(request: Request) -> JSONResponse:
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        changeset_id = request.path_params["id"]
        rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        result = await _build_full_changeset(rows[0])
        return JSONResponse(result)

    async def add_document(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        changeset_id = request.path_params["id"]
        doc_id = request.path_params["doc_id"]

        cs_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not cs_rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        changeset = cs_rows[0]
        if changeset["status"] not in ("open", "review"):
            return JSONResponse(
                {"error": f"Cannot add documents to a changeset with status {changeset['status']!r}"},
                status_code=400,
            )

        doc_rows = await CMSDocument.select(CMSDocument.id).where(CMSDocument.id == doc_id).run()
        if not doc_rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        # 409 if already in changeset
        existing = (
            await CMSChangesetDocument.select()
            .where(
                CMSChangesetDocument.changeset_id == changeset_id,
                CMSChangesetDocument.document_id == doc_id,
            )
            .run()
        )
        if existing:
            return JSONResponse(
                {"error": "Document is already in this changeset"},
                status_code=409,
            )

        now = datetime.now(UTC)
        await CMSChangesetDocument.insert(
            CMSChangesetDocument(
                changeset_id=changeset_id,
                document_id=doc_id,
                added_at=now,
            )
        ).run()

        updated_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        result = await _build_full_changeset(updated_rows[0])
        return JSONResponse(result)

    async def remove_document(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        changeset_id = request.path_params["id"]
        doc_id = request.path_params["doc_id"]

        cs_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not cs_rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        doc_rows = await CMSDocument.select(CMSDocument.id).where(CMSDocument.id == doc_id).run()
        if not doc_rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        join_rows = (
            await CMSChangesetDocument.select()
            .where(
                CMSChangesetDocument.changeset_id == changeset_id,
                CMSChangesetDocument.document_id == doc_id,
            )
            .run()
        )
        if not join_rows:
            return JSONResponse(
                {"error": "Document is not in this changeset"},
                status_code=404,
            )

        await (
            CMSChangesetDocument.delete()
            .where(
                CMSChangesetDocument.changeset_id == changeset_id,
                CMSChangesetDocument.document_id == doc_id,
            )
            .run()
        )

        updated_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        result = await _build_full_changeset(updated_rows[0])
        return JSONResponse(result)

    async def delete_changeset(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        changeset_id = request.path_params["id"]
        rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        changeset = rows[0]
        if changeset["status"] == "published":
            return JSONResponse(
                {"error": "Cannot delete a published changeset"},
                status_code=400,
            )

        # Delete join rows first, then changeset
        await (
            CMSChangesetDocument.delete()
            .where(CMSChangesetDocument.changeset_id == changeset_id)
            .run()
        )
        await CMSChangeset.delete().where(CMSChangeset.id == changeset_id).run()

        return JSONResponse({"deleted": True})

    async def publish_changeset(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        changeset_id = request.path_params["id"]

        rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        changeset = rows[0]
        if changeset["status"] not in ("open", "review", "scheduled"):
            return JSONResponse(
                {
                    "error": (
                        f"Changeset status is {changeset['status']!r}; "
                        "must be 'open', 'review', or 'scheduled' to publish"
                    )
                },
                status_code=400,
            )

        try:
            result = await _publish_changeset_logic(changeset_id, cms)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        if result is None:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        return JSONResponse(result)

    async def schedule_changeset(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        changeset_id = request.path_params["id"]
        rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        publish_at_str = data.get("publish_at")
        if not publish_at_str:
            return JSONResponse({"error": "publish_at is required"}, status_code=422)

        try:
            publish_at = datetime.fromisoformat(publish_at_str)
        except (ValueError, TypeError):
            return JSONResponse(
                {"error": "publish_at must be a valid ISO 8601 datetime string"},
                status_code=400,
            )

        # Ensure timezone-aware
        if publish_at.tzinfo is None:
            publish_at = publish_at.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        if publish_at <= now:
            return JSONResponse(
                {"error": "publish_at must be in the future"},
                status_code=400,
            )

        await (
            CMSChangeset.update(
                {
                    CMSChangeset.status: "scheduled",
                    CMSChangeset.publish_at: publish_at,
                }
            )
            .where(CMSChangeset.id == changeset_id)
            .run()
        )

        updated_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        result = await _build_full_changeset(updated_rows[0])
        return JSONResponse(result)

    async def unschedule_changeset(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        changeset_id = request.path_params["id"]
        rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        changeset = rows[0]
        if changeset["status"] != "scheduled":
            return JSONResponse(
                {"error": f"Changeset is not scheduled (status: {changeset['status']!r})"},
                status_code=400,
            )

        await (
            CMSChangeset.update(
                {
                    CMSChangeset.status: "open",
                    CMSChangeset.publish_at: None,
                }
            )
            .where(CMSChangeset.id == changeset_id)
            .run()
        )

        updated_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        result = await _build_full_changeset(updated_rows[0])
        return JSONResponse(result)

    async def patch_changeset(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        changeset_id = request.path_params["id"]
        rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        changeset = rows[0]
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        update_fields: dict[Any, Any] = {}

        if "title" in data:
            update_fields[CMSChangeset.title] = data["title"]

        if "status" in data:
            new_status = data["status"]
            valid_transitions = {
                "open": ["review"],
                "review": ["open"],
            }
            allowed = valid_transitions.get(changeset["status"], [])
            if new_status not in allowed:
                return JSONResponse(
                    {"error": f"Cannot transition from {changeset['status']!r} to {new_status!r}"},
                    status_code=400,
                )
            update_fields[CMSChangeset.status] = new_status
            if new_status == "review":
                update_fields[CMSChangeset.reviewed_at] = datetime.now(UTC)

        if not update_fields:
            return JSONResponse({"error": "No fields to update"}, status_code=400)

        await CMSChangeset.update(update_fields).where(CMSChangeset.id == changeset_id).run()

        updated_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        result = await _build_full_changeset(updated_rows[0])
        return JSONResponse(result)

    async def revert_changeset(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        changeset_id = request.path_params["id"]
        cs_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not cs_rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        if cs_rows[0]["status"] != "published":
            return JSONResponse(
                {"error": "Can only revert published changesets"},
                status_code=400,
            )

        # Optional: revert a single doc from the changeset
        doc_id_param = request.path_params.get("doc_id")

        version_rows = (
            await CMSDocumentVersion.select()
            .where(
                CMSDocumentVersion.changeset_id == changeset_id,
                CMSDocumentVersion.action == "publish",
            )
            .run()
        )
        if not version_rows:
            return JSONResponse(
                {"error": "No version snapshots found for this changeset"},
                status_code=404,
            )

        if doc_id_param:
            version_rows = [v for v in version_rows if v["document_id"] == doc_id_param]
            if not version_rows:
                return JSONResponse(
                    {"error": f"No snapshot for document {doc_id_param!r} in this changeset"},
                    status_code=404,
                )

        now = datetime.now(UTC)
        reverted_docs = []

        for ver in version_rows:
            did = ver["document_id"]

            # Check if a LATER changeset published this doc
            later_versions = (
                await CMSDocumentVersion.select()
                .where(
                    CMSDocumentVersion.document_id == did,
                    CMSDocumentVersion.action == "publish",
                    CMSDocumentVersion.changeset_id != changeset_id,
                )
                .order_by(CMSDocumentVersion.version, ascending=False)
                .limit(1)
                .run()
            )
            if later_versions and later_versions[0]["version"] > ver["version"]:
                return JSONResponse(
                    {
                        "error": f"Document {did!r} was published by a later changeset — revert that one first",
                        "conflict_changeset_id": later_versions[0].get("changeset_id"),
                    },
                    status_code=409,
                )

            # Restore the body from before this changeset published
            restore_body = ver["body"]
            if isinstance(restore_body, str):
                try:
                    restore_body = json.loads(restore_body)
                except Exception:
                    restore_body = {}

            await CMSDocument.update(
                {
                    CMSDocument.body: json.dumps(restore_body),
                    CMSDocument.updated_at: now,
                }
            ).where(CMSDocument.id == did).run()

            # Record the revert as a new version
            await _snapshot_document_version(
                doc_id=did,
                body=restore_body,
                action="revert",
                changeset_id=changeset_id,
                now=now,
            )
            reverted_docs.append(did)

        # Mark changeset as reverted (only if all docs reverted)
        if not doc_id_param:
            await CMSChangeset.update(
                {CMSChangeset.status: "reverted"}
            ).where(CMSChangeset.id == changeset_id).run()

        return JSONResponse({
            "reverted": True,
            "document_ids": reverted_docs,
        })

    async def changeset_diff(request: Request) -> JSONResponse:
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        changeset_id = request.path_params["id"]
        cs_rows = await CMSChangeset.select().where(CMSChangeset.id == changeset_id).run()
        if not cs_rows:
            return JSONResponse({"error": "Changeset not found"}, status_code=404)

        join_rows = (
            await CMSChangesetDocument.select()
            .where(CMSChangesetDocument.changeset_id == changeset_id)
            .run()
        )
        doc_ids = [r["document_id"] for r in join_rows]

        diffs = []
        for doc_id in doc_ids:
            doc_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
            if not doc_rows:
                continue
            doc = doc_rows[0]

            published_body = doc.get("body")
            if isinstance(published_body, str):
                try:
                    published_body = json.loads(published_body)
                except Exception:
                    published_body = {}

            draft_body = doc.get("draft_body")
            if isinstance(draft_body, str):
                try:
                    draft_body = json.loads(draft_body)
                except Exception:
                    draft_body = None

            is_new = not doc.get("published", False)
            published_text = _extract_text(published_body or {})
            draft_text = _extract_text(draft_body or published_body or {})

            diff_lines = list(difflib.unified_diff(
                published_text.splitlines(keepends=True),
                draft_text.splitlines(keepends=True),
                fromfile="published",
                tofile="draft",
                lineterm="",
            ))

            # Conflict detection
            also_in = []
            other_cs_rows = (
                await CMSChangesetDocument.select(CMSChangesetDocument.changeset_id)
                .where(
                    CMSChangesetDocument.document_id == doc_id,
                    CMSChangesetDocument.changeset_id != changeset_id,
                )
                .run()
            )
            if other_cs_rows:
                other_ids = [row["changeset_id"] for row in other_cs_rows]
                other_open = (
                    await CMSChangeset.select(CMSChangeset.id, CMSChangeset.title)
                    .where(
                        CMSChangeset.id.is_in(other_ids),
                        CMSChangeset.status.is_in(["open", "review", "scheduled"]),
                    )
                    .run()
                )
                also_in = [{"changeset_id": cs["id"], "title": cs["title"]} for cs in other_open]

            draft_deleted = doc.get("draft_deleted")
            draft_published = doc.get("draft_published")
            has_changes = len(diff_lines) > 0 or draft_published is not None or draft_deleted is True

            diffs.append({
                "doc_id": doc_id,
                "doc_type": doc["doc_type"],
                "slug": doc.get("slug", ""),
                "is_new": is_new,
                "has_changes": has_changes,
                "diff": "".join(diff_lines),
                "additions": sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++")),
                "deletions": sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---")),
                "also_in": also_in,
                "draft_deleted": draft_deleted,
                "draft_published": draft_published,
            })

        return JSONResponse({"changeset_id": changeset_id, "diffs": diffs})

    return [
        Route("/api/changesets", endpoint=create_changeset, methods=["POST"]),
        Route("/api/changesets", endpoint=list_changesets, methods=["GET"]),
        # Before /{id}, which would read "default" as an id.
        Route("/api/changesets/default", endpoint=get_default_changeset, methods=["GET"]),
        Route("/api/changesets/{id}", endpoint=get_changeset, methods=["GET"]),
        Route("/api/changesets/{id}", endpoint=patch_changeset, methods=["PATCH"]),
        Route("/api/changesets/{id}", endpoint=delete_changeset, methods=["DELETE"]),
        Route(
            "/api/changesets/{id}/documents/{doc_id}",
            endpoint=add_document,
            methods=["POST"],
        ),
        Route(
            "/api/changesets/{id}/documents/{doc_id}",
            endpoint=remove_document,
            methods=["DELETE"],
        ),
        Route("/api/changesets/{id}/publish", endpoint=publish_changeset, methods=["POST"]),
        Route("/api/changesets/{id}/revert", endpoint=revert_changeset, methods=["POST"]),
        Route("/api/changesets/{id}/revert/{doc_id}", endpoint=revert_changeset, methods=["POST"]),
        Route("/api/changesets/{id}/diff", endpoint=changeset_diff, methods=["GET"]),
        Route("/api/changesets/{id}/schedule", endpoint=schedule_changeset, methods=["POST"]),
        Route(
            "/api/changesets/{id}/unschedule",
            endpoint=unschedule_changeset,
            methods=["POST"],
        ),
    ]
