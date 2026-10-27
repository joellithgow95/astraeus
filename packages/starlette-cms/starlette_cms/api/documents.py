"""Document CRUD endpoints — /api/documents"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from piccolo.columns.base import Column
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from starlette_cms.api.changesets import (
    _snapshot_document_version,
    body_validation_error,
    link_document_to_changeset,
    link_new_draft_to_default_changeset,
    unlink_from_open_changesets,
)
from starlette_cms.api.webhooks import fire_event
from starlette_cms.auth import require_auth
from starlette_cms.refs import RefSite, iter_ref_sites, refs_to
from starlette_cms.tables import CMSChangeset, CMSChangesetDocument, CMSDocument, CMSDocumentVersion

if TYPE_CHECKING:
    from starlette_cms.app import CMS

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


def _utcnow() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def _coerce_filter_value(v: str) -> Any:
    """Coerce URL string to bool, int, float, or leave as str."""
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _strings_at(value: Any, path: list[str]) -> list[str]:
    """Non-empty strings in the list(s) at a dotted ``path``, stepping into lists on the way."""
    if not path:
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str) and v]
        return []
    if isinstance(value, list):
        return [s for item in value for s in _strings_at(item, path)]
    if isinstance(value, dict):
        return _strings_at(value.get(path[0]), path[1:])
    return []


def _matches_filters(doc: dict, filters: dict[str, Any]) -> bool:
    """Return True if all filter key/value pairs match the document body."""
    body = doc.get("body", {})
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            logger.warning(
                "starlette_cms.documents.body_parse_failed",
                doc_id=doc.get("id"),
            )
            body = {}
    for key, expected in filters.items():
        if body.get(key) != expected:
            return False
    return True


def _row_to_dict(row: dict[str, Any], *, use_draft: bool = False) -> dict[str, Any]:
    """Convert a raw Piccolo row dict to a JSON-serialisable document dict.

    When ``use_draft=True`` and a draft exists, the ``body`` field in the
    returned dict reflects ``draft_body`` instead of the published ``body``.
    ``has_draft`` and ``draft_version`` are always included.
    """
    raw_body = row.get("body")
    if isinstance(raw_body, str):
        try:
            raw_body = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "starlette_cms.documents.body_parse_failed_in_row",
                body_type=type(raw_body).__name__,
            )
    # Nulls are NOT stripped on read. ADR 016 makes absence the canonical
    # representation of "not set" at *write* time (model_dump(exclude_none=True)),
    # and reserves null for an explicit write — a PATCH that deliberately clears a
    # field (§2), or on_delete="nullify" blanking a DocumentRef (ADR 010).
    #
    # Stripping here destroyed exactly those signals: a consumer could not tell
    # "the referenced document was deleted" from "this ref was never set", which
    # is the ambiguity ADR 016 exists to prevent.

    raw_draft_body = row.get("draft_body")
    if isinstance(raw_draft_body, str):
        try:
            raw_draft_body = json.loads(raw_draft_body)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "starlette_cms.documents.draft_body_parse_failed_in_row",
                body_type=type(raw_draft_body).__name__,
            )
            raw_draft_body = None

    # Treat None and {} as "no draft" — {} can appear on legacy rows that
    # predate the explicit default=None fix, or from buggy clients.
    has_draft = bool(raw_draft_body) or (row.get("draft_published") is not None) or (row.get("draft_deleted") is True)
    effective_body = raw_draft_body if (use_draft and bool(raw_draft_body)) else raw_body

    meta = row.get("meta", "{}")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            logger.warning("starlette_cms.documents.meta_parse_failed_in_row")
            meta = {}

    # Convert datetime objects to ISO strings; emit has_draft + draft_version;
    # skip raw draft_body from the output (it's consumed above).
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key == "body":
            result[key] = effective_body
        elif key == "draft_body":
            # Omit from output — callers see has_draft instead.
            continue
        elif key == "meta":
            result[key] = meta
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value

    result["has_draft"] = has_draft
    # draft_version may not exist in older rows (before migration); default to 0.
    if "draft_version" not in result:
        result["draft_version"] = 0
    return result


def _matches_search(doc: dict[str, Any], search: str) -> bool:
    """True when ``search`` (casefolded) is in the slug or any top-level string of the body.

    Backs ``?q=``, which the editor's reference pickers use to find a document
    by title (or track, term, ...) among more than fit on one page.
    """
    if not search:
        return True
    body = doc.get("body") if isinstance(doc.get("body"), dict) else {}
    texts = [doc.get("slug"), *body.values()]
    return any(isinstance(t, str) and search in t.casefold() for t in texts)


async def _validate_refs(
    cms: CMS, doc_model: Any, body_data: dict[str, Any]
) -> JSONResponse | None:
    """
    Check every reference in ``body_data`` — single refs, lists of refs, and
    either inside nested blocks — against the database: each ID must exist
    and have the declared block_type, and a list may not repeat an ID.
    One query covers the whole body. Returns a 422 JSONResponse on failure,
    None on success.
    """
    sites = list(iter_ref_sites(doc_model, body_data))
    for site in sites:
        if (msg := site.check_shape()) is not None:
            return JSONResponse({"error": msg}, status_code=422)

    wanted = {ref_id for site in sites for ref_id in site.ids()}
    if not wanted:
        return None
    rows = (
        await CMSDocument.select(CMSDocument.id, CMSDocument.doc_type)
        .where(CMSDocument.id.is_in(list(wanted)))
        .run()
    )
    types = {r["id"]: r["doc_type"] for r in rows}

    for site in sites:
        for ref_id in site.ids():
            if ref_id not in types:
                return JSONResponse(
                    {"error": f"{site.path}: referenced document {ref_id!r} not found"},
                    status_code=422,
                )
            if site.ref.block_type and types[ref_id] != site.ref.block_type:
                return JSONResponse(
                    {
                        "error": (
                            f"{site.path}: expected block_type {site.ref.block_type!r}, "
                            f"got {types[ref_id]!r}"
                        )
                    },
                    status_code=422,
                )
    return None


def _parse_body(raw: Any, doc_id: str) -> dict[str, Any] | None:
    if raw is None or isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warning("starlette_cms.documents.ref_body_parse_failed", doc_id=doc_id)
        return None
    return parsed if isinstance(parsed, dict) else None


async def _check_ref_integrity(cms: CMS, doc_id: str, doc_type: str) -> JSONResponse | None:
    """
    Apply ``on_delete`` for every reference to ``doc_id`` (a ``doc_type``), in
    any registered type's published body or draft, single, listed or nested:

    - ``"block"`` anywhere → 409, and nothing is changed;
    - ``"nullify"`` → a single ref becomes None, a list drops the ID;
    - ``"cascade"`` → the referencing document is deleted.
    """
    all_models: list[Any] = list(cms.registry._blocks.values())
    all_models.extend(cms._document_types.values())

    # (referencing type, row id, column, parsed body, sites pointing at doc_id)
    hits: list[tuple[str, str, Any, dict[str, Any], list[RefSite]]] = []
    for model in all_models:
        if not refs_to(model, doc_type):
            continue
        model_doc_type: str = (
            getattr(model, "__document_type__", None)
            or getattr(model, "__block_type__", None)
            or ""
        )
        if not model_doc_type:
            continue

        rows = (
            await CMSDocument.select(CMSDocument.id, CMSDocument.body, CMSDocument.draft_body)
            .where(CMSDocument.doc_type == model_doc_type)
            .run()
        )
        for row in rows:
            for column, key in ((CMSDocument.body, "body"), (CMSDocument.draft_body, "draft_body")):
                body = _parse_body(row.get(key), row["id"])
                if not body:
                    continue
                sites = [
                    site
                    for site in iter_ref_sites(model, body)
                    if site.ref.block_type == doc_type and doc_id in site.ids()
                ]
                if sites:
                    hits.append((model_doc_type, row["id"], column, body, sites))

    for model_doc_type, _row_id, _column, _body, sites in hits:
        for site in sites:
            if site.ref.on_delete == "block":
                return JSONResponse(
                    {"error": f"Cannot delete: referenced by {model_doc_type}.{site.path}"},
                    status_code=409,
                )

    cascaded: set[str] = set()
    for _type, row_id, column, body, sites in hits:
        if row_id in cascaded:
            continue
        if any(site.ref.on_delete == "cascade" for site in sites):
            await CMSDocument.delete().where(CMSDocument.id == row_id).run()
            cascaded.add(row_id)
            continue
        changed = False
        for site in sites:
            if site.ref.on_delete == "nullify":
                site.remove(doc_id)
                changed = True
        if changed:
            await (
                CMSDocument.update({column: json.dumps(body), CMSDocument.updated_at: datetime.now(UTC)})
                .where(CMSDocument.id == row_id)
                .run()
            )

    return None


async def _bulk_resolve_refs(
    docs: list[dict[str, Any]], resolve_fields: list[str]
) -> list[dict[str, Any]]:
    """
    Bulk-resolve named DocumentRef fields across a list of documents.

    For each field name, issues a single IN query and attaches the resolved
    document at ``{field_name}__resolved`` in each document's body: one dict
    for a single ref, a list in stored order for a list of refs (an ID whose
    document is gone is left out).
    """
    for field_name in resolve_fields:
        ids: set[str] = set()
        for d in docs:
            value = d["body"].get(field_name) if isinstance(d.get("body"), dict) else None
            if isinstance(value, str) and value:
                ids.add(value)
            elif isinstance(value, list):
                ids.update(v for v in value if isinstance(v, str))
        if not ids:
            continue
        ref_rows = await CMSDocument.select().where(CMSDocument.id.is_in(list(ids))).run()
        ref_map = {r["id"]: _row_to_dict(r) for r in ref_rows}
        for doc in docs:
            body = doc.get("body")
            if not isinstance(body, dict):
                continue
            value = body.get(field_name)
            if isinstance(value, list):
                body[f"{field_name}__resolved"] = [ref_map[v] for v in value if v in ref_map]
            elif value and value in ref_map:
                body[f"{field_name}__resolved"] = ref_map[value]
    return docs


def make_document_routes(cms: CMS) -> list[Route]:
    """Build and return all document CRUD routes, closed over ``cms``."""

    async def list_documents(request: Request) -> JSONResponse:
        # Read auth only if cms.read_auth is True
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        params = request.query_params
        doc_type = params.get("type") or ""
        slug = params.get("slug")
        import_ref = params.get("import_ref")
        published_param = params.get("published")
        has_draft_param = params.get("has_draft")
        resolve_refs_param = params.get("resolve_refs")
        ids = [i.strip() for i in (params.get("ids") or "").split(",") if i.strip()]
        search = (params.get("q") or "").strip().casefold()
        try:
            limit = int(params.get("limit", 20))
            offset = int(params.get("offset", 0))
        except ValueError:
            return JSONResponse({"error": "limit and offset must be integers"}, status_code=400)

        # --- Filter params ---
        raw_filters_json = params.get("filters")
        key_value_filters: dict[str, Any] = {}
        if raw_filters_json:
            try:
                key_value_filters = json.loads(raw_filters_json)
            except json.JSONDecodeError:
                return JSONResponse({"error": "filters must be valid JSON"}, status_code=400)

        for param_name, param_value in params.items():
            if param_name.startswith("filter[") and param_name.endswith("]"):
                key = param_name[7:-1]
                key_value_filters[key] = _coerce_filter_value(param_value)

        # --- Order params ---
        order_by_field = params.get("order_by", "created_at")
        order_asc = params.get("order", "desc").lower() == "asc"

        valid_order_fields = {"created_at", "updated_at", "published_at", "slug"}
        if order_by_field not in valid_order_fields:
            order_by_field = "created_at"

        order_col = getattr(CMSDocument, order_by_field)

        query = CMSDocument.select()

        if doc_type:
            query = query.where(CMSDocument.doc_type == doc_type)
        if slug:
            query = query.where(CMSDocument.slug == slug)
        if import_ref is not None:
            query = query.where(CMSDocument.import_ref == import_ref)
        if ids:
            query = query.where(CMSDocument.id.is_in(ids))
        if published_param is not None:
            published = published_param.lower() in ("true", "1", "yes")
            query = query.where(CMSDocument.published == published)
        if has_draft_param is not None and has_draft_param.lower() in ("true", "1", "yes"):
            query = query.where(
                CMSDocument.draft_body.is_not_null()
                | CMSDocument.draft_published.is_not_null()
                | (CMSDocument.draft_deleted == True)  # noqa: E712
            )

        exclude_types_param = params.get("exclude_types")
        if exclude_types_param:
            excluded = [t.strip() for t in exclude_types_param.split(",") if t.strip()]
            if excluded:
                query = query.where(CMSDocument.doc_type.not_in(excluded))

        query = query.order_by(order_col, ascending=order_asc)

        with tracer.start_as_current_span("cms.documents.list") as span:
            span.set_attribute("doc_type", doc_type)
            span.set_attribute("limit", limit)
            span.set_attribute("offset", offset)
            try:
                if key_value_filters or search:
                    all_rows = await query.run()
                    all_docs = [_row_to_dict(r) for r in all_rows]
                    filtered_docs = [
                        d
                        for d in all_docs
                        if _matches_filters(d, key_value_filters) and _matches_search(d, search)
                    ]
                    total = len(filtered_docs)
                    docs = filtered_docs[offset : offset + limit]
                else:
                    # Total count (without limit/offset)
                    count_query = CMSDocument.count()
                    if doc_type:
                        count_query = count_query.where(CMSDocument.doc_type == doc_type)
                    if slug:
                        count_query = count_query.where(CMSDocument.slug == slug)
                    if import_ref is not None:
                        count_query = count_query.where(CMSDocument.import_ref == import_ref)
                    if ids:
                        count_query = count_query.where(CMSDocument.id.is_in(ids))
                    if published_param is not None:
                        count_query = count_query.where(CMSDocument.published == published)  # type: ignore[possibly-undefined]
                    if has_draft_param is not None and has_draft_param.lower() in (
                        "true",
                        "1",
                        "yes",
                    ):
                        count_query = count_query.where(
                            CMSDocument.draft_body.is_not_null()
                            | CMSDocument.draft_published.is_not_null()
                            | (CMSDocument.draft_deleted == True)  # noqa: E712
                        )

                    if exclude_types_param:
                        excluded_for_count = [t.strip() for t in exclude_types_param.split(",") if t.strip()]
                        if excluded_for_count:
                            count_query = count_query.where(CMSDocument.doc_type.not_in(excluded_for_count))

                    total = await count_query.run()
                    rows = await query.limit(limit).offset(offset).run()
                    docs = [_row_to_dict(r) for r in rows]
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

        # Bulk-resolve requested ref fields
        if resolve_refs_param:
            resolve_fields = [f.strip() for f in resolve_refs_param.split(",") if f.strip()]
            docs = await _bulk_resolve_refs(docs, resolve_fields)

        return JSONResponse(
            {
                "documents": docs,
                "total": total,
                "filters_applied": key_value_filters,
            }
        )

    async def create_document(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        doc_type = data.get("doc_type")
        if not doc_type:
            return JSONResponse({"error": "doc_type is required"}, status_code=422)

        # Look up the model — check document types first, then block registry as
        # fallback (used for @cms.block(append_only=True) and singleton types).
        doc_model = cms._document_types.get(doc_type)
        if doc_model is None:
            if doc_type in cms.registry:
                doc_model = cms.registry.get(doc_type)
            else:
                return JSONResponse(
                    {"error": f"Unknown document type: {doc_type!r}"},
                    status_code=422,
                )

        body_data = data.get("body", {})
        try:
            validated = doc_model.model_validate(body_data)
        except ValidationError as exc:
            return JSONResponse(
                {"error": "Validation failed", "detail": exc.errors()},
                status_code=422,
            )

        # Validate ImageField values against the configured media backend
        if cms.media_backend is not None:
            image_fields = getattr(doc_model, "__image_fields__", [])
            validated_dump = validated.model_dump()
            for fname in image_fields:
                val = validated_dump.get(fname)
                if val and not await cms.media_backend.confirm_exists(val):
                    return JSONResponse(
                        {"error": "Image key not found", "field": fname},
                        status_code=422,
                    )

        # Validate DocumentRef fields
        if (err := await _validate_refs(cms, doc_model, validated.model_dump())) is not None:
            return err

        from nanoid import generate

        doc_id = generate(size=21)
        slug = data.get("slug", "")
        import_ref: str | None = data.get("import_ref") or None
        doc_meta = data.get("meta", {})
        now = datetime.now(UTC)

        # 409 on duplicate (doc_type, import_ref) — NULL import_refs are never deduplicated
        if import_ref is not None:
            existing = (
                await CMSDocument.select(CMSDocument.id)
                .where(
                    CMSDocument.doc_type == doc_type,
                    CMSDocument.import_ref == import_ref,
                )
                .limit(1)
                .run()
            )
            if existing:
                return JSONResponse(
                    {
                        "error": "Duplicate import_ref",
                        "detail": (
                            f"A document of type {doc_type!r} with "
                            f"import_ref {import_ref!r} already exists."
                        ),
                        "existing_id": existing[0]["id"],
                    },
                    status_code=409,
                )

        # append_only=True: create and publish atomically (ADR 014)
        is_append_only = False
        try:
            is_append_only = cms.registry.is_append_only(doc_type)
        except Exception:
            logger.warning(
                "starlette_cms.documents.registry_lookup_failed",
                doc_type=doc_type,
                operation="is_append_only",
            )

        with tracer.start_as_current_span("cms.documents.create") as span:
            span.set_attribute("doc_type", doc_type)
            try:
                if is_append_only:
                    await CMSDocument.insert(
                        CMSDocument(
                            id=doc_id,
                            doc_type=doc_type,
                            slug=slug,
                            body=json.dumps(validated.model_dump(exclude_none=True)),
                            meta=json.dumps(doc_meta),
                            created_at=now,
                            updated_at=now,
                            published=True,
                            published_at=now,
                            import_ref=import_ref,
                        )
                    ).run()
                else:
                    await CMSDocument.insert(
                        CMSDocument(
                            id=doc_id,
                            doc_type=doc_type,
                            slug=slug,
                            body=json.dumps(validated.model_dump(exclude_none=True)),
                            meta=json.dumps(doc_meta),
                            created_at=now,
                            updated_at=now,
                            published=False,
                            published_at=None,
                            import_ref=import_ref,
                        )
                    ).run()
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()

        # Link to an explicit changeset when the caller provides one (e.g. a
        # gateway grouping a whole sync run into one changeset). Unlike PATCH,
        # create never auto-creates a changeset — it only links when told to.
        response_headers: dict[str, str] = {}
        active_cs_id = request.headers.get("x-active-changeset-id")
        if active_cs_id:
            cs_rows = await CMSChangeset.select().where(CMSChangeset.id == active_cs_id).run()
            if cs_rows and cs_rows[0]["status"] in ("open", "review"):
                await CMSChangesetDocument.insert(
                    CMSChangesetDocument(
                        changeset_id=active_cs_id,
                        document_id=doc_id,
                        added_at=datetime.now(UTC),
                    )
                ).run()
                response_headers["X-Changeset-Id"] = active_cs_id
        elif not is_append_only:
            # No changeset named: collect the new draft in the default one, so it
            # is not left in none until somebody edits it. Append-only documents
            # are born published and have nothing to stage.
            await link_new_draft_to_default_changeset(doc_id, doc_type, cms.default_changeset)

        loop = asyncio.get_running_loop()
        extra: dict[str, Any] = {}
        if is_append_only:
            extra["append_only"] = True
        loop.create_task(fire_event(cms, "document.created", doc_id, doc_type, slug, extra=extra))

        return JSONResponse(
            _row_to_dict(rows[0]),
            status_code=201,
            headers=response_headers if response_headers else None,
        )

    async def get_document(request: Request) -> JSONResponse:
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        doc_id = request.path_params["id"]
        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        use_draft = request.query_params.get("draft", "").lower() in ("true", "1", "yes")
        return JSONResponse(_row_to_dict(rows[0], use_draft=use_draft))

    async def patch_document(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        doc_id = request.path_params["id"]
        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        row = rows[0]
        doc_type = row["doc_type"]

        # append_only=True: PATCH is not allowed (ADR 014)
        try:
            if cms.registry.is_append_only(doc_type):
                return JSONResponse(
                    {"error": "append_only documents cannot be modified"},
                    status_code=405,
                )
        except Exception:
            logger.warning(
                "starlette_cms.documents.registry_lookup_failed",
                doc_type=doc_type,
                operation="is_append_only",
                doc_id=doc_id,
            )

        try:
            patch_data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        doc_model = cms._document_types.get(doc_type)
        if doc_model is None and doc_type in cms.registry:
            doc_model = cms.registry.get(doc_type)

        # Determine the base body for the draft merge.
        # If draft_body already exists, merge into it; otherwise initialise from
        # the published body (so the draft starts as a full copy of the live content).
        existing_draft_body = row.get("draft_body")
        if isinstance(existing_draft_body, str):
            try:
                existing_draft_body = json.loads(existing_draft_body)
            except Exception:
                logger.warning(
                    "starlette_cms.documents.draft_body_parse_failed",
                    doc_id=doc_id,
                    operation="patch",
                )
                existing_draft_body = None

        if existing_draft_body is None:
            # No prior draft — seed draft from the published body.
            base_body = row.get("body", "{}")
            if isinstance(base_body, str):
                try:
                    base_body = json.loads(base_body)
                except Exception:
                    logger.warning(
                        "starlette_cms.documents.body_parse_failed",
                        doc_id=doc_id,
                        operation="patch",
                    )
                    base_body = {}
        else:
            base_body = existing_draft_body

        new_body_data = patch_data.get("body", {})

        # Strip immutable fields before merge — silently drop, no error (ADR 013).
        if doc_model is not None:
            for field_name in getattr(doc_model, "__immutable_fields__", []):
                new_body_data.pop(field_name, None)

        merged_body = {**base_body, **new_body_data}

        # Validate merged body if we have a model
        if doc_model is not None:
            try:
                validated = doc_model.model_validate(merged_body)
                # exclude_none=True: absent optional fields are omitted from storage.
                # A field explicitly set to null in the PATCH body is preserved as null
                # (intentional clear), per ADR 016.
                merged_body = validated.model_dump(exclude_none=True)
            except ValidationError as exc:
                return JSONResponse(
                    {"error": "Validation failed", "detail": exc.errors()},
                    status_code=422,
                )

            # Validate ImageField values against the configured media backend
            if cms.media_backend is not None:
                image_fields = getattr(doc_model, "__image_fields__", [])
                for fname in image_fields:
                    val = merged_body.get(fname)
                    if val and not await cms.media_backend.confirm_exists(val):
                        return JSONResponse(
                            {"error": "Image key not found", "field": fname},
                            status_code=422,
                        )

            # Validate DocumentRef fields in the patched data
            if (err := await _validate_refs(cms, doc_model, new_body_data)) is not None:
                return err

        current_draft_version = row.get("draft_version") or 0
        update_kwargs: dict[Column | str, Any] = {
            CMSDocument.draft_body: json.dumps(merged_body),
            CMSDocument.draft_version: current_draft_version + 1,
            CMSDocument.updated_at: datetime.now(UTC),
        }

        if "slug" in patch_data:
            update_kwargs[CMSDocument.slug] = patch_data["slug"]

        if "import_ref" in patch_data:
            new_import_ref: str | None = patch_data["import_ref"] or None
            update_kwargs[CMSDocument.import_ref] = new_import_ref

        if "meta" in patch_data:
            existing_meta = row.get("meta", "{}")
            if isinstance(existing_meta, str):
                try:
                    existing_meta = json.loads(existing_meta)
                except Exception:
                    logger.warning(
                        "starlette_cms.documents.meta_parse_failed",
                        doc_id=doc_id,
                    )
                    existing_meta = {}
            merged_meta = {**existing_meta, **patch_data["meta"]}
            update_kwargs[CMSDocument.meta] = json.dumps(merged_meta)

        with tracer.start_as_current_span("cms.documents.patch") as span:
            span.set_attribute("doc_id", doc_id)
            try:
                await CMSDocument.update(update_kwargs).where(CMSDocument.id == doc_id).run()
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

        updated_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        updated_row = updated_rows[0]

        loop = asyncio.get_running_loop()
        loop.create_task(
            fire_event(cms, "document.updated", doc_id, doc_type, updated_row.get("slug", ""))
        )

        # Auto-link to a changeset so this edit is grouped for publishing. Shared
        # with the collab (rich-text) write path via link_document_to_changeset.
        response_headers: dict[str, str] = {}
        active_cs_id = request.headers.get("x-active-changeset-id")
        created = await link_document_to_changeset(
            doc_id, doc_type, active_cs_id, cms.default_changeset
        )
        if created is not None:
            new_cs_id, auto_title = created
            response_headers["X-Changeset-Id"] = new_cs_id
            response_headers["X-Changeset-Title"] = auto_title

        result = _row_to_dict(updated_row, use_draft=True)
        return JSONResponse(result, headers=response_headers if response_headers else None)

    async def delete_document(request: Request) -> Response:
        if (err := await require_auth(request, cms)) is not None:
            return err  # type: ignore[return-value]

        doc_id = request.path_params["id"]
        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        row = rows[0]
        doc_type = row["doc_type"]
        slug = row.get("slug", "")

        # append_only=True: DELETE is not allowed (ADR 014)
        try:
            if cms.registry.is_append_only(doc_type):
                return JSONResponse(
                    {"error": "append_only documents cannot be deleted"},
                    status_code=405,
                )
        except Exception:
            logger.warning(
                "starlette_cms.documents.registry_lookup_failed",
                doc_type=doc_type,
                operation="is_append_only",
                doc_id=doc_id,
            )

        # Enforce referential integrity (on_delete="block" → 409; "nullify" → set to None)
        if (err := await _check_ref_integrity(cms, doc_id, doc_type)) is not None:
            return err  # type: ignore[return-value]

        with tracer.start_as_current_span("cms.documents.delete") as span:
            span.set_attribute("doc_id", doc_id)
            try:
                await CMSDocument.delete().where(CMSDocument.id == doc_id).run()
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

        loop = asyncio.get_running_loop()
        loop.create_task(fire_event(cms, "document.deleted", doc_id, doc_type, slug))

        return Response(status_code=204)

    async def publish_document(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        doc_id = request.path_params["id"]
        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        row = rows[0]
        doc_type = row["doc_type"]
        now = datetime.now(UTC)

        # Singleton enforcement — archive the currently active document first
        is_singleton = False
        try:
            is_singleton = cms.registry.is_singleton(doc_type)
        except Exception:
            logger.warning(
                "starlette_cms.documents.registry_lookup_failed",
                doc_type=doc_type,
                operation="is_singleton",
                doc_id=doc_id,
            )

        # If a draft exists, promote it to body before publishing.
        draft_body_raw = row.get("draft_body")
        if isinstance(draft_body_raw, str):
            try:
                draft_body_raw = json.loads(draft_body_raw)
            except Exception:
                logger.warning(
                    "starlette_cms.documents.draft_body_parse_failed",
                    doc_id=doc_id,
                    operation="publish",
                )
                draft_body_raw = None

        # Never promote a draft that fails the document model — publishing is
        # verbatim, so this is the last chance to stop a broken body going live.
        if draft_body_raw is not None:
            error = body_validation_error(cms, doc_type, draft_body_raw)
            if error is not None:
                return JSONResponse(
                    {"error": f"Draft can't be published — {error}"},
                    status_code=422,
                )

        # Snapshot current body before overwriting
        current_body = row.get("body")
        if isinstance(current_body, str):
            try:
                current_body = json.loads(current_body)
            except Exception:
                current_body = {}
        await _snapshot_document_version(
            doc_id=doc_id,
            body=current_body or {},
            action="publish",
            changeset_id=None,
            now=now,
        )

        publish_body_update: dict[Column | str, Any] = {}
        if draft_body_raw is not None:
            publish_body_update[CMSDocument.body] = json.dumps(draft_body_raw)
        publish_body_update[CMSDocument.draft_body] = None
        publish_body_update[CMSDocument.draft_version] = 0

        with tracer.start_as_current_span("cms.documents.publish") as span:
            span.set_attribute("doc_id", doc_id)
            try:
                if is_singleton:
                    await (
                        CMSDocument.update({CMSDocument.singleton_status: "archived"})
                        .where(
                            CMSDocument.doc_type == doc_type,
                            CMSDocument.singleton_status == "active",
                        )
                        .run()
                    )
                    await (
                        CMSDocument.update(
                            {
                                CMSDocument.published: True,
                                CMSDocument.published_at: now,
                                CMSDocument.singleton_status: "active",
                                CMSDocument.updated_at: now,
                                **publish_body_update,
                            }
                        )
                        .where(CMSDocument.id == doc_id)
                        .run()
                    )
                else:
                    await (
                        CMSDocument.update(
                            {
                                CMSDocument.published: True,
                                CMSDocument.published_at: now,
                                CMSDocument.updated_at: now,
                                **publish_body_update,
                            }
                        )
                        .where(CMSDocument.id == doc_id)
                        .run()
                    )
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

        # Live now, so no longer a pending change in any changeset.
        await unlink_from_open_changesets(doc_id)

        updated_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        updated_row = updated_rows[0]

        extra: dict[str, Any] = {}
        if is_singleton:
            extra["singleton"] = True

        loop = asyncio.get_running_loop()
        loop.create_task(
            fire_event(
                cms,
                "document.published",
                doc_id,
                updated_row["doc_type"],
                updated_row.get("slug", ""),
                extra=extra,
            )
        )

        return JSONResponse(_row_to_dict(updated_row))

    async def unpublish_document(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        doc_id = request.path_params["id"]
        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        now = datetime.now(UTC)
        await (
            CMSDocument.update(
                {
                    CMSDocument.published: False,
                    CMSDocument.updated_at: now,
                }
            )
            .where(CMSDocument.id == doc_id)
            .run()
        )

        updated_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        updated_row = updated_rows[0]

        loop = asyncio.get_running_loop()
        loop.create_task(
            fire_event(
                cms,
                "document.unpublished",
                doc_id,
                updated_row["doc_type"],
                updated_row.get("slug", ""),
            )
        )

        return JSONResponse(_row_to_dict(updated_row))

    async def discard_draft(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        doc_id = request.path_params["id"]
        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        with tracer.start_as_current_span("cms.documents.discard_draft") as span:
            span.set_attribute("doc_id", doc_id)
            try:
                await (
                    CMSDocument.update(
                        {
                            CMSDocument.draft_body: None,
                            CMSDocument.draft_deleted: None,
                            CMSDocument.draft_published: None,
                            CMSDocument.draft_version: 0,
                            CMSDocument.updated_at: datetime.now(UTC),
                        }
                    )
                    .where(CMSDocument.id == doc_id)
                    .run()
                )
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                raise

        updated_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        return JSONResponse(_row_to_dict(updated_rows[0]))

    async def set_draft_publish_state(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        doc_id = request.path_params["id"]
        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        row = rows[0]
        doc_type = row["doc_type"]

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        if "published" not in data:
            return JSONResponse({"error": "published field is required"}, status_code=422)

        # Value is the desired draft_published state:
        # true = pending publish, false = pending unpublish, null = clear pending change
        draft_published = data["published"]

        now = datetime.now(UTC)
        await (
            CMSDocument.update(
                {
                    CMSDocument.draft_published: draft_published,
                    CMSDocument.updated_at: now,
                }
            )
            .where(CMSDocument.id == doc_id)
            .run()
        )

        # Auto-link to changeset (same pattern as patch_document)
        response_headers: dict[str, str] = {}
        active_cs_id = request.headers.get("x-active-changeset-id")
        created = await link_document_to_changeset(
            doc_id, doc_type, active_cs_id, cms.default_changeset
        )
        if created is not None:
            response_headers["X-Changeset-Id"], response_headers["X-Changeset-Title"] = created

        updated_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        result = _row_to_dict(updated_rows[0])
        return JSONResponse(result, headers=response_headers if response_headers else None)

    async def set_draft_delete_state(request: Request) -> JSONResponse:
        if (err := await require_auth(request, cms)) is not None:
            return err

        doc_id = request.path_params["id"]
        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        if not rows:
            return JSONResponse({"error": "Document not found"}, status_code=404)

        row = rows[0]
        doc_type = row["doc_type"]

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        if "deleted" not in data:
            return JSONResponse({"error": "deleted field is required"}, status_code=422)

        draft_deleted = True if data["deleted"] else None

        now = datetime.now(UTC)
        await (
            CMSDocument.update(
                {
                    CMSDocument.draft_deleted: draft_deleted,
                    CMSDocument.updated_at: now,
                }
            )
            .where(CMSDocument.id == doc_id)
            .run()
        )

        response_headers: dict[str, str] = {}
        active_cs_id = request.headers.get("x-active-changeset-id")
        created = await link_document_to_changeset(
            doc_id, doc_type, active_cs_id, cms.default_changeset
        )
        if created is not None:
            response_headers["X-Changeset-Id"], response_headers["X-Changeset-Title"] = created

        updated_rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        result = _row_to_dict(updated_rows[0])
        return JSONResponse(result, headers=response_headers if response_headers else None)

    async def get_singleton(request: Request) -> JSONResponse:
        """Return the currently active singleton for a block type, or 404."""
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        block_type = request.path_params["block_type"]

        rows = await (
            CMSDocument.select()
            .where(
                CMSDocument.doc_type == block_type,
                CMSDocument.singleton_status == "active",
            )
            .limit(1)
            .run()
        )
        if not rows:
            return JSONResponse(
                {"error": f"No published singleton for {block_type!r}"},
                status_code=404,
            )
        return JSONResponse(_row_to_dict(rows[0]))

    async def publish_singleton(request: Request) -> JSONResponse:
        """Create and publish a singleton document in a single step (for seed scripts)."""
        if (err := await require_auth(request, cms)) is not None:
            return err

        block_type = request.path_params["block_type"]

        if block_type not in cms.registry:
            return JSONResponse(
                {"error": f"Unknown block type: {block_type!r}"},
                status_code=422,
            )
        if not cms.registry.is_singleton(block_type):
            return JSONResponse(
                {"error": f"Block type {block_type!r} is not a singleton"},
                status_code=422,
            )

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        body_data = data.get("body", {})
        version_message = data.get("version_message", "")

        block_model = cms.registry.get(block_type)
        try:
            validated = block_model.model_validate(body_data)
        except ValidationError as exc:
            return JSONResponse(
                {"error": "Validation failed", "detail": exc.errors()},
                status_code=422,
            )

        from nanoid import generate

        doc_id = generate(size=21)
        now = datetime.now(UTC)
        doc_meta: dict[str, Any] = {}
        if version_message:
            doc_meta["version_message"] = version_message

        await (
            CMSDocument.update({CMSDocument.singleton_status: "archived"})
            .where(
                CMSDocument.doc_type == block_type,
                CMSDocument.singleton_status == "active",
            )
            .run()
        )

        await CMSDocument.insert(
            CMSDocument(
                id=doc_id,
                doc_type=block_type,
                slug="",
                body=json.dumps(validated.model_dump(exclude_none=True)),
                meta=json.dumps(doc_meta),
                created_at=now,
                updated_at=now,
                published=True,
                published_at=now,
                singleton_status="active",
            )
        ).run()

        rows = await CMSDocument.select().where(CMSDocument.id == doc_id).run()
        new_doc = _row_to_dict(rows[0])

        loop = asyncio.get_running_loop()
        loop.create_task(
            fire_event(
                cms,
                "document.published",
                doc_id,
                block_type,
                "",
                extra={"singleton": True},
            )
        )

        return JSONResponse(new_doc, status_code=201)

    async def get_singleton_history(request: Request) -> JSONResponse:
        """Return archived versions of a singleton, newest first."""
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        block_type = request.path_params["block_type"]

        rows = await (
            CMSDocument.select()
            .where(
                CMSDocument.doc_type == block_type,
                CMSDocument.singleton_status == "archived",
            )
            .order_by(CMSDocument.published_at, ascending=False)
            .run()
        )
        return JSONResponse({"history": [_row_to_dict(r) for r in rows]})

    async def list_tags(request: Request) -> JSONResponse:
        """List the distinct tags used on one document type, most used first.

        ``GET /api/tags?type=blog_post`` reads the ``tags`` field;
        ``&field=<name>`` reads another list-of-strings field, and a dotted
        path reads one inside a list of objects (``field=links.tags`` gives
        the tags on a ``LinksField``'s items). A document's
        draft counts as well as its published body, so a tag typed on a draft
        is offered on the next one before anything is published. Each tag
        counts once per document.

        This backs the editor's tag chips (``TagsField``).
        """
        if cms.read_auth:
            if (err := await require_auth(request, cms)) is not None:
                return err

        doc_type = request.query_params.get("type") or ""
        if not doc_type:
            return JSONResponse({"error": "type is required"}, status_code=400)
        path = (request.query_params.get("field") or "tags").split(".")

        rows = (
            await CMSDocument.select(CMSDocument.body, CMSDocument.draft_body)
            .where(CMSDocument.doc_type == doc_type)
            .run()
        )
        counts: dict[str, int] = {}
        for row in rows:
            seen: set[str] = set()
            for raw in (row.get("body"), row.get("draft_body")):
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                seen.update(_strings_at(raw, path))
            for tag in seen:
                counts[tag] = counts.get(tag, 0) + 1

        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return JSONResponse({"tags": [{"tag": t, "count": n} for t, n in ranked]})

    return [
        Route("/api/documents", endpoint=list_documents, methods=["GET"]),
        Route("/api/tags", endpoint=list_tags, methods=["GET"]),
        Route("/api/documents", endpoint=create_document, methods=["POST"]),
        Route(
            "/api/documents/singleton/{block_type}",
            endpoint=get_singleton,
            methods=["GET"],
        ),
        Route(
            "/api/documents/singleton/{block_type}",
            endpoint=publish_singleton,
            methods=["POST"],
        ),
        Route(
            "/api/documents/singleton/{block_type}/history",
            endpoint=get_singleton_history,
            methods=["GET"],
        ),
        Route("/api/documents/{id}", endpoint=get_document, methods=["GET"]),
        Route("/api/documents/{id}", endpoint=patch_document, methods=["PATCH"]),
        Route("/api/documents/{id}", endpoint=delete_document, methods=["DELETE"]),
        Route("/api/documents/{id}/publish", endpoint=publish_document, methods=["POST"]),
        Route("/api/documents/{id}/unpublish", endpoint=unpublish_document, methods=["POST"]),
        Route("/api/documents/{id}/discard-draft", endpoint=discard_draft, methods=["POST"]),
        Route("/api/documents/{id}/draft-publish-state", endpoint=set_draft_publish_state, methods=["POST"]),
        Route("/api/documents/{id}/draft-delete", endpoint=set_draft_delete_state, methods=["POST"]),
    ]
