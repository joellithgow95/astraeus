"""
MCP server implementation for starlette-cms (ADR 005).

All tools are thin wrappers around the starlette-cms HTTP API.
No business logic lives here — only tool definitions and httpx calls.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The MCP server requires the 'mcp' extra. Install with: pip install starlette-cms[mcp]"
    ) from exc


DEFAULT_LIST_LIMIT = 10
MAX_LIST_LIMIT = 50

# Hard cap on the size of any single tool response, in characters.  A weaker
# model that gets a megabyte back burns its context window and gives up.
MAX_RESPONSE_CHARS = 20_000
DEFAULT_GET_MAX_CHARS = 20_000
MAX_GET_MAX_CHARS = 200_000

EXCERPT_CHARS = 200

# Body keys checked, in order, to derive a title / excerpt for list summaries.
TITLE_KEYS = ("title", "name", "headline", "heading")
EXCERPT_KEYS = ("excerpt", "summary", "description", "subtitle", "dek")

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
UPDATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
DELETE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
# Publishing and unpublishing fire the document.published / document.unpublished
# webhook, which triggers a Netlify rebuild of the public site.
PUBLISH = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

LIMIT_DESCRIPTION = (
    f"Max documents to return, 1-{MAX_LIST_LIMIT} (default {DEFAULT_LIST_LIMIT}). "
    "Use offset to page through more."
)


class DocumentSummary(BaseModel):
    """Body-free view of a document, used by list_documents."""

    created_at: str | None = None
    doc_type: str
    excerpt: str | None = None
    has_draft: bool = False
    id: str
    published: bool = False
    published_at: str | None = None
    slug: str = ""
    status: str
    title: str | None = None
    updated_at: str | None = None


class DocumentListResponse(BaseModel):
    """Shape returned by list_documents."""

    documents: list[dict[str, Any]]
    filters_applied: dict[str, Any]
    has_more: bool
    hint: str | None = None
    limit: int
    next_offset: int | None
    notice: str | None = None
    offset: int
    returned: int
    total: int
    truncated: bool = False


def _first_text(body: Any, keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty string value among ``keys`` in a body dict."""
    if not isinstance(body, dict):
        return None
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def summarize_document(doc: dict[str, Any]) -> DocumentSummary:
    """Reduce a full API document to a small, body-free summary."""
    body = doc.get("body")
    excerpt = _first_text(body, EXCERPT_KEYS)
    if excerpt is not None and len(excerpt) > EXCERPT_CHARS:
        excerpt = excerpt[:EXCERPT_CHARS].rstrip() + "..."

    published = bool(doc.get("published"))
    return DocumentSummary(
        created_at=doc.get("created_at"),
        doc_type=doc.get("doc_type", ""),
        excerpt=excerpt,
        has_draft=bool(doc.get("has_draft")),
        id=doc.get("id", ""),
        published=published,
        published_at=doc.get("published_at"),
        slug=doc.get("slug") or "",
        status=STATUS_PUBLISHED if published else STATUS_DRAFT,
        title=_first_text(body, TITLE_KEYS),
        updated_at=doc.get("updated_at"),
    )


class BodyEdit(BaseModel):
    """One anchored edit to a rich-text body (see edit_document_body)."""

    model_config = ConfigDict(extra="forbid")

    find: str | None = Field(
        default=None, description="Exact text to replace, copied from the current draft."
    )
    section: str | None = Field(
        default=None, description="A heading: replace it and its section."
    )
    replace: str | None = Field(
        default=None, description="Markdown to put in place of find/section; empty deletes."
    )
    insert: str | None = Field(default=None, description="Markdown to add.")
    after_heading: str | None = Field(
        default=None, description="With insert: add at the end of this heading's section."
    )
    at: Literal["end", "start"] | None = Field(
        default=None, description='With insert: "end" (default) or "start" of the body.'
    )


def _error(message: str, hint: str | None = None, **extra: Any) -> dict[str, Any]:
    """Build an error payload: what went wrong, plus what to try next."""
    payload: dict[str, Any] = {"error": message}
    if hint:
        payload["hint"] = hint
    payload.update(extra)
    return payload


def _normalise_tag(tag: str) -> str:
    """The lowercase, hyphenated form a tag should take."""
    return re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")


def _similar_tags(tag: str, known: dict[str, int], limit: int = 3) -> list[str]:
    """Existing tags that look like ``tag`` (near spelling, shared word or stem), by use."""
    words = set(tag.split("-"))
    found = []
    for other, uses in known.items():
        if other == tag:
            continue
        close = difflib.SequenceMatcher(None, tag, other).ratio() >= 0.7
        shared_word = bool(words & set(other.split("-")))
        shared_stem = len(os.path.commonprefix([tag, other])) >= 4
        if close or shared_word or shared_stem:
            found.append((-uses, other))
    return [f"{t} ({-n})" for n, t in sorted(found)[:limit]]


def _unknown_parameter_message(tool_name: str, unknown: list[str], valid: list[str]) -> str:
    """Explain unknown tool arguments, suggesting the closest valid name."""
    parts: list[str] = []
    for name in unknown:
        close = difflib.get_close_matches(name, valid, n=1, cutoff=0.5)
        suggestion = f" Did you mean `{close[0]}`?" if close else ""
        parts.append(f"Unknown parameter `{name}` for {tool_name}.{suggestion}")
    valid_list = ", ".join(f"`{v}`" for v in valid) or "(none)"
    return " ".join(parts) + f" Valid parameters: {valid_list}."


def _reject_unknown_arguments(mcp: FastMCP) -> None:
    """
    Make every registered tool fail loudly on unknown arguments.

    FastMCP validates arguments with a pydantic model whose default is to
    silently ignore extra keys, so a misnamed parameter (``document_type``
    instead of ``doc_type``) used to be dropped and the call ran unfiltered.
    Each tool's argument model is replaced by a subclass whose ``before``
    validator raises a message naming the closest valid parameter.  FastMCP
    reports it to the caller as a tool error.
    """
    for tool in mcp._tool_manager.list_tools():
        arg_model = tool.fn_metadata.arg_model
        valid = sorted(arg_model.model_fields)
        tool_name = tool.name

        def _check(cls: Any, data: Any, _tool: str = tool_name, _valid: list[str] = valid) -> Any:
            if isinstance(data, dict):
                unknown = sorted(set(data) - set(_valid))
                if unknown:
                    raise ValueError(_unknown_parameter_message(_tool, unknown, _valid))
            return data

        tool.fn_metadata.arg_model = type(
            arg_model.__name__,
            (arg_model,),
            {"check_unknown_arguments": model_validator(mode="before")(classmethod(_check))},
        )
        tool.parameters["additionalProperties"] = False


def _cap_json(value: dict[str, Any], max_chars: int) -> tuple[str, bool]:
    """Serialise ``value`` and cut it to ``max_chars``; return (text, was_cut)."""
    text = json.dumps(value, default=str)
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _fit_documents(docs: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Return the longest leading run of ``docs`` that serialises within ``max_chars``."""
    fitted: list[dict[str, Any]] = []
    used = 0
    for doc in docs:
        used += len(json.dumps(doc, default=str))
        if used > max_chars:
            break
        fitted.append(doc)
    return fitted


def build_mcp_server(
    base_url: str,
    api_key: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastMCP:
    """
    Build and return a FastMCP server wired to the starlette-cms HTTP API.

    :param base_url: Base URL of the deployed starlette-cms instance,
        e.g. ``https://mysite.com/cms``.  Must not have a trailing slash.
    :param api_key: API key for ``Authorization: Bearer`` header on all
        mutating requests.  Pass ``None`` when the CMS is running in
        ``auth="none"`` mode.
    :param transport: Optional httpx transport.  Tests pass an ``ASGITransport``
        so the tools run against a real in-process CMS instead of the network.

    All tools share one httpx.AsyncClient (connection-pooled).  The client is
    created lazily on the first tool call so the server can be imported without
    making any network connections.
    """

    _client: httpx.AsyncClient | None = None

    def _get_client() -> httpx.AsyncClient:
        nonlocal _client
        if _client is None or _client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            _client = httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=30.0,
                transport=transport,
            )
        return _client

    # Ensure any API key is included in read headers too (for read_auth=True CMSes)
    def _read_headers() -> dict[str, str]:
        if api_key:
            return {"Authorization": f"Bearer {api_key}"}
        return {}

    mcp = FastMCP(
        name="starlette-cms",
        instructions=(
            "Tools for managing content in a starlette-cms instance.\n"
            "Workflow:\n"
            "1. list_block_types to see the available document types, then "
            "get_block_schema(block_type) to see a type's fields.\n"
            "2. list_documents(doc_type=..., limit=5) to find documents. It returns "
            "short summaries (id, title, slug, status, dates, excerpt) WITHOUT bodies. "
            "Keep limit small and page with offset / next_offset.\n"
            "3. get_document(doc_id) to read one document's full body. It reads the "
            "DRAFT (what the editor shows) and its draft_version.\n"
            "4. create_document / update_document / edit_document_body write DRAFTS "
            "only and do not change the public site. Check the result, then "
            "publish_document to go live.\n"
            "Rich-text fields (the post body) may be open in an editor while you work. "
            "Change one with edit_document_body: anchored edits (find exact text and "
            "replace it, replace a section by its heading, insert after a heading or at "
            "the end) applied to the live draft, so the person editing sees them land "
            "with their typing intact. Use update_document for plain fields (title, "
            "tags, image); never rewrite a whole body through it to change part of it.\n"
            "5. publish_document and unpublish_document change the public site and "
            "trigger a site rebuild; only call them when asked to publish.\n"
            "Changesets group drafts so they publish together as one site build. "
            "get_default_changeset shows what is waiting to go live; list_changesets "
            "shows them all. Move a document with remove_document_from_changeset and "
            "add_document_to_changeset; publish_changeset publishes every document "
            "in one, so only call it when asked.\n"
            "Tags: before tagging a document call list_tags(doc_type) and reuse an "
            "existing tag where one fits; create_document and update_document say in "
            "tag_notes when a tag is new and which existing ones look like it.\n"
            "Parameter names are exact: an unknown parameter is rejected with a "
            "suggestion, not ignored."
        ),
    )

    # ------------------------------------------------------------------
    # Schema tools (read-only, no auth required for most deployments)
    # ------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def list_block_types() -> list[str]:
        """
        Return the names of all block types (document types) in this CMS.

        Call this first when you do not know which types exist. Cheap: returns a
        short list of names. Then call get_block_schema(block_type) for a type's
        fields, or list_documents(doc_type=...) to see its documents.
        """
        client = _get_client()
        r = await client.get("/api/schema", headers=_read_headers())
        r.raise_for_status()
        data = r.json()
        # Response is a JSON Schema with top-level definitions/properties
        definitions: dict[str, Any] = data.get("$defs", data.get("definitions", {}))
        return list(definitions.keys())

    @mcp.tool(annotations=READ_ONLY)
    async def get_block_schema(
        block_type: Annotated[
            str, Field(description='Block type name from list_block_types, e.g. "blog_post".')
        ],
    ) -> dict[str, Any]:
        """
        Return the JSON Schema for one block type: field names, types, which
        fields are required, and constraints.

        Use this before create_document or update_document so the body matches the
        schema. Do not use it to find documents; use list_documents for that.
        """
        client = _get_client()
        r = await client.get(f"/api/schema/{block_type}", headers=_read_headers())
        if r.status_code == 404:
            return _error(
                f"Block type {block_type!r} not found",
                hint="Call list_block_types to see the valid names.",
            )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Document read tools
    # ------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def list_documents(
        doc_type: Annotated[
            str | None,
            Field(
                description=(
                    'Only documents of this block type, e.g. "blog_post". The parameter is '
                    "named `doc_type`. Omit for all types (call list_block_types for names)."
                )
            ),
        ] = None,
        published: Annotated[
            bool | None,
            Field(
                description=(
                    "true = only published documents, false = only unpublished drafts, omit = both."
                )
            ),
        ] = None,
        limit: Annotated[
            int, Field(ge=1, le=MAX_LIST_LIMIT, description=LIMIT_DESCRIPTION)
        ] = DEFAULT_LIST_LIMIT,
        offset: Annotated[
            int,
            Field(
                ge=0,
                description="Documents to skip. Use `next_offset` from the previous page.",
            ),
        ] = 0,
        include_body: Annotated[
            bool,
            Field(
                description=(
                    "false (default) returns short summaries. true returns each full "
                    "document including its body, which can be very large; prefer "
                    "get_document for one document."
                )
            ),
        ] = False,
    ) -> dict[str, Any]:
        """
        List documents, newest first, as short summaries WITHOUT bodies.

        Use this to find documents by type or status. To read a document's content,
        take its `id` from here and call get_document. Do not set include_body just
        to read one document.

        Each summary has: id, doc_type, slug, title, status ("draft" or
        "published"), has_draft, created_at, updated_at, published_at, excerpt.
        The response also has `total` (all matches), `limit`, `offset`, `returned`,
        `has_more` and `next_offset` (pass it as `offset` to get the next page).
        If a size cap cut the response short, `truncated` is true and `notice` says
        how to get the rest.
        """
        client = _get_client()
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if doc_type is not None:
            params["type"] = doc_type
        if published is not None:
            params["published"] = str(published).lower()
        r = await client.get("/api/documents", params=params, headers=_read_headers())
        r.raise_for_status()
        data = r.json()

        docs: list[dict[str, Any]] = data.get("documents", [])
        total: int = data.get("total", len(docs))
        truncated = False
        notice: str | None = None

        summaries = [summarize_document(d).model_dump() for d in docs]
        shaped = summaries

        if include_body:
            fitted = _fit_documents(docs, MAX_RESPONSE_CHARS)
            if len(fitted) == len(docs):
                shaped = docs
            else:
                truncated = True
                if fitted:
                    shaped = fitted
                    notice = (
                        f"Response capped at {MAX_RESPONSE_CHARS} characters: returned "
                        f"{len(fitted)} of {len(docs)} documents with bodies. Use next_offset "
                        "for the rest, or lower limit."
                    )
                else:
                    notice = (
                        f"The first document alone exceeds {MAX_RESPONSE_CHARS} characters, "
                        "so bodies were omitted and summaries returned. Call "
                        "get_document(doc_id, max_chars=...) to read one document."
                    )

        returned = len(shaped)
        has_more = offset + returned < total
        hint = None
        if total == 0 and doc_type is not None:
            hint = (
                f"No documents of type {doc_type!r}. Check the name with list_block_types; "
                "it must match exactly."
            )

        return DocumentListResponse(
            documents=shaped,
            filters_applied=data.get("filters_applied", {}),
            has_more=has_more,
            hint=hint,
            limit=limit,
            next_offset=offset + returned if has_more else None,
            notice=notice,
            offset=offset,
            returned=returned,
            total=total,
            truncated=truncated,
        ).model_dump(exclude_none=False)

    @mcp.tool(annotations=READ_ONLY)
    async def get_document(
        doc_id: Annotated[
            str,
            Field(description="Document id (nanoid) from list_documents."),
        ],
        max_chars: Annotated[
            int,
            Field(
                ge=1_000,
                le=MAX_GET_MAX_CHARS,
                description=(
                    f"Cap on the response size in characters (default {DEFAULT_GET_MAX_CHARS}, "
                    f"max {MAX_GET_MAX_CHARS}). Raise it only if the response was truncated."
                ),
            ),
        ] = DEFAULT_GET_MAX_CHARS,
        published: Annotated[
            bool,
            Field(
                description=(
                    "True to read the live published version instead of the draft. "
                    "Default false: the draft, which is what the editor shows and what "
                    "your edits change."
                ),
            ),
        ] = False,
    ) -> dict[str, Any]:
        """
        Return ONE document with its full body: the DRAFT by default.

        Use this to read or check a single document after finding its id with
        list_documents. `draft_version` in the result is the version to pass as
        update_document's base_version if you replace a rich-text field. Bodies
        (rich text especially) can be large; if the
        response exceeds `max_chars` it comes back as
        {"truncated": true, "notice": ..., "content": <first max_chars characters of the JSON>}
        and `notice` says how to get more.
        """
        client = _get_client()
        r = await client.get(
            f"/api/documents/{doc_id}",
            params=None if published else {"draft": "true"},
            headers=_read_headers(),
        )
        if r.status_code == 404:
            return _error(
                f"Document {doc_id!r} not found",
                hint="Use an `id` from list_documents; ids are case-sensitive nanoids, not slugs.",
            )
        r.raise_for_status()
        doc = r.json()

        text, was_cut = _cap_json(doc, max_chars)
        if not was_cut:
            return doc
        return {
            "content": text,
            "doc_id": doc_id,
            "notice": (
                f"Document JSON is {len(json.dumps(doc, default=str))} characters; showing the "
                f"first {max_chars}. Call get_document again with a larger max_chars "
                f"(up to {MAX_GET_MAX_CHARS}) to read more."
            ),
            "truncated": True,
        }

    async def _tag_notes(doc_type: str, body: dict[str, Any]) -> list[dict[str, str]]:
        """Notes on tags in ``body`` that no other ``doc_type`` document uses.

        Advisory: any failure gives none.

        Checks the type's top-level tag fields, so call it BEFORE the write (a saved
        draft's own tags count as used). Never blocks a save: a new tag is sometimes right.
        """
        try:
            client = _get_client()
            r = await client.get(f"/api/schema/{doc_type}", headers=_read_headers())
            if r.status_code != 200:
                return []
            field_meta = r.json().get("field_meta", {})
            notes: list[dict[str, str]] = []
            for field, meta in field_meta.items():
                values = body.get(field)
                if meta.get("field_type") != "tags" or not isinstance(values, list) or not values:
                    continue
                source = meta.get("suggest_from") or doc_type
                r = await client.get(
                    "/api/tags",
                    params={"type": source, "field": meta.get("suggest_field") or field},
                    headers=_read_headers(),
                )
                if r.status_code != 200:
                    continue
                known = {t["tag"]: t["count"] for t in r.json().get("tags", [])}
                for tag in values:
                    if not isinstance(tag, str) or tag in known:
                        continue
                    note = f"No other {source} uses this tag."
                    if (fixed := _normalise_tag(tag)) != tag:
                        note += f" Tags are lowercase and hyphenated: {fixed!r}."
                        tag = fixed
                        if tag in known:
                            note += " That one exists already."
                    if similar := _similar_tags(tag, known):
                        note += f" Similar existing tags: {', '.join(similar)}."
                    note += " Fine if it is genuinely new; otherwise reuse an existing tag."
                    notes.append({"field": field, "tag": tag, "note": note})
            return notes
        except Exception:  # advisory only; the write must not depend on it
            return []

    @mcp.tool(annotations=READ_ONLY)
    async def list_tags(
        doc_type: Annotated[
            str,
            Field(description='Block type name whose tags to list, e.g. "blog_post" or "link".'),
        ],
        field: Annotated[
            str,
            Field(description="The list-of-strings field to read. Default: tags."),
        ] = "tags",
        limit: Annotated[
            int, Field(ge=1, le=500, description="Most tags to return (default 100).")
        ] = 100,
    ) -> dict[str, Any]:
        """
        List the tags already used on one document type, most used first, with how
        many documents carry each.

        Call this before tagging a document and reuse an existing tag where one fits;
        a near-duplicate ("visual-design" beside "design") splits what a tag page or
        a link collection gathers. Drafts count as well as published bodies. For a
        post's `links_by_tag` slice the tags come from doc_type "link".
        """
        client = _get_client()
        r = await client.get(
            "/api/tags", params={"type": doc_type, "field": field}, headers=_read_headers()
        )
        if r.status_code == 400:
            return _error("doc_type is required", hint="Pass a name from list_block_types.")
        r.raise_for_status()
        tags = r.json().get("tags", [])
        return {
            "doc_type": doc_type,
            "field": field,
            "total": len(tags),
            "tags": tags[:limit],
            "guidance": "Tags are lowercase and hyphenated. Reuse one of these where it fits.",
        }

    # ------------------------------------------------------------------
    # Document write tools
    # ------------------------------------------------------------------

    @mcp.tool(annotations=CREATE)
    async def create_document(
        doc_type: Annotated[
            str, Field(description='Block type name from list_block_types, e.g. "blog_post".')
        ],
        body: Annotated[
            dict[str, Any],
            Field(description="Document body. Must match the block schema from get_block_schema."),
        ],
        slug: Annotated[str, Field(description='Optional URL slug, e.g. "my-first-post".')] = "",
        meta: Annotated[
            dict[str, Any] | None,
            Field(description="Optional metadata (arbitrary key/value pairs)."),
        ] = None,
    ) -> dict[str, Any]:
        """
        Create a new document as a DRAFT. It is not public until you call
        publish_document.

        Call get_block_schema(doc_type) first so the body is valid. Not idempotent:
        calling twice creates two documents, so check list_documents before retrying.
        For append_only block types the document is published immediately.
        """
        client = _get_client()
        payload: dict[str, Any] = {
            "doc_type": doc_type,
            "body": body,
            "slug": slug,
        }
        if meta:
            payload["meta"] = meta
        notes = await _tag_notes(doc_type, body)
        r = await client.post("/api/documents", json=payload)
        if r.status_code == 422:
            return _error(
                "Validation failed",
                hint=(
                    "Fix the fields listed in `detail`; get_block_schema(doc_type) shows the "
                    "required fields and types, and list_block_types the valid doc_type names."
                ),
                detail=r.json(),
            )
        r.raise_for_status()
        created = r.json()
        if notes:
            created["tag_notes"] = notes
        return created

    @mcp.tool(annotations=UPDATE)
    async def update_document(
        doc_id: Annotated[str, Field(description="The document id (nanoid) from list_documents.")],
        body: Annotated[
            dict[str, Any] | None,
            Field(description="Partial body: only the fields to change; they are merged in."),
        ] = None,
        slug: Annotated[str | None, Field(description="New slug, if changing it.")] = None,
        meta: Annotated[
            dict[str, Any] | None,
            Field(description="Partial metadata to merge into existing meta."),
        ] = None,
        base_version: Annotated[
            int | None,
            Field(
                description=(
                    "The draft_version from the get_document you based this on. Needed "
                    "to replace a rich-text field (the body) while it is open in an "
                    "editor; if the draft has moved on the update is refused, not applied."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Partially update a document (PATCH: only the fields you provide change).

        For plain fields (title, tags, image, dates, refs). To change a rich-text
        body use edit_document_body instead: a body passed here REPLACES the
        whole field, so anything typed since you read it would be lost. If you
        must replace one, pass base_version. Does not publish. Not allowed for
        append_only block types.
        """
        client = _get_client()
        payload: dict[str, Any] = {}
        if body is not None:
            payload["body"] = body
        if slug is not None:
            payload["slug"] = slug
        if meta is not None:
            payload["meta"] = meta
        if base_version is not None:
            payload["base_version"] = base_version
        notes: list[dict[str, str]] = []
        if body and any(isinstance(v, list) for v in body.values()):
            found = await client.get(f"/api/documents/{doc_id}", headers=_read_headers())
            if found.status_code == 200:
                notes = await _tag_notes(found.json().get("doc_type", ""), body)
        r = await client.patch(f"/api/documents/{doc_id}", json=payload)
        if r.status_code == 409:
            detail = r.json()
            return _error(
                detail.get("error", "Conflict"),
                hint=(
                    "Someone edited the document since you read it, or has it open. To change "
                    "the body use edit_document_body; to replace it, get_document again and "
                    "pass its draft_version as base_version."
                ),
                detail=detail,
            )
        if r.status_code == 404:
            return _error(
                f"Document {doc_id!r} not found",
                hint="Use an `id` from list_documents.",
            )
        if r.status_code == 405:
            return _error(
                r.json().get("error", "Method not allowed"),
                hint="append_only documents cannot be modified; create a new document instead.",
            )
        if r.status_code == 422:
            return _error(
                "Validation failed",
                hint="Fix the fields listed in `detail`; get_block_schema shows the valid shape.",
                detail=r.json(),
            )
        r.raise_for_status()
        updated = r.json()
        if notes:
            updated["tag_notes"] = notes
        return updated

    @mcp.tool(annotations=UPDATE)
    async def edit_document_body(
        doc_id: Annotated[str, Field(description="The document id (nanoid) from list_documents.")],
        edits: Annotated[
            list[BodyEdit],
            Field(
                min_length=1,
                description=(
                    "Edits applied in order, all or nothing. Each is one of: "
                    '{"find": "exact text", "replace": "markdown"} (empty replace deletes); '
                    '{"section": "Heading", "replace": "markdown"} (the heading and its '
                    'section); {"insert": "markdown", "after_heading": "Heading"}; '
                    '{"insert": "markdown", "at": "end"|"start"}.'
                ),
            ),
        ],
        field: Annotated[
            str | None,
            Field(
                description=(
                    "The rich-text field, e.g. body_markdown. Leave empty when the type has "
                    "only one."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Change a document's rich-text body: add, rewrite or delete text and sections.

        The way to edit a post body, even one someone has open in the editor
        right now: every edit is applied to the live draft, and their editor
        shows it land with their own typing untouched. Anchor edits on text
        you have just read with get_document: `find` must be copied exactly
        and occur once (inside one paragraph or heading, or whole paragraphs
        separated by a blank line), or name a heading. If an anchor is missing
        or ambiguous, nothing is applied and the error says which edit; read
        the draft again and retry. Replacement and inserted text is markdown.
        Writes the draft only; does not publish.
        """
        client = _get_client()
        payload: dict[str, Any] = {
            "edits": [e.model_dump(exclude_none=True) for e in edits],
            "source": "mcp",
        }
        if field:
            payload["field"] = field
        r = await client.post(f"/api/documents/{doc_id}/edit", json=payload)
        if r.status_code == 404:
            return _error(
                f"Document {doc_id!r} not found", hint="Use an `id` from list_documents."
            )
        if r.status_code in (405, 422, 501):
            detail = r.json()
            return _error(
                detail.get("error", "Edit refused"),
                hint=(
                    "Nothing was applied. Read the draft again with get_document and copy "
                    "`find` exactly; a missing heading lists the headings that exist; a type "
                    "with several rich-text fields needs `field`."
                ),
                detail=detail,
            )
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=DELETE)
    async def delete_document(
        doc_id: Annotated[str, Field(description="The document id (nanoid) from list_documents.")],
    ) -> dict[str, Any]:
        """
        Permanently delete a document. This cannot be undone.

        To take a live document offline but keep it, use unpublish_document instead.
        Fails if other documents reference this one, and for append_only types.
        """
        client = _get_client()
        r = await client.delete(f"/api/documents/{doc_id}")
        if r.status_code == 404:
            return _error(
                f"Document {doc_id!r} not found",
                hint="Use an `id` from list_documents; it may already be deleted.",
            )
        if r.status_code == 405:
            return _error(
                r.json().get("error", "Method not allowed"),
                hint="append_only documents cannot be deleted.",
            )
        if r.status_code == 409:
            return _error(
                r.json().get("error", "Conflict — referenced document"),
                hint="Remove or change the referencing documents first, then retry.",
            )
        r.raise_for_status()
        return {"deleted": True, "doc_id": doc_id}

    @mcp.tool(annotations=PUBLISH)
    async def publish_document(
        doc_id: Annotated[str, Field(description="The document id (nanoid) from list_documents.")],
    ) -> dict[str, Any]:
        """
        Publish a document: it becomes publicly visible and the site is rebuilt
        (this fires the publish webhook that triggers a Netlify rebuild).

        Only call this when asked to publish; use create_document / update_document
        for drafts. Safe to repeat. For singleton block types, publishing archives
        the previously active version.
        """
        client = _get_client()
        r = await client.post(f"/api/documents/{doc_id}/publish")
        if r.status_code == 404:
            return _error(
                f"Document {doc_id!r} not found",
                hint="Use an `id` from list_documents.",
            )
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=PUBLISH)
    async def unpublish_document(
        doc_id: Annotated[str, Field(description="The document id (nanoid) from list_documents.")],
    ) -> dict[str, Any]:
        """
        Unpublish a document: it reverts to a draft, disappears from the public
        site, and the site is rebuilt (fires the same webhook as publishing).

        The content is kept; use delete_document only to remove it permanently.
        """
        client = _get_client()
        r = await client.post(f"/api/documents/{doc_id}/unpublish")
        if r.status_code == 404:
            return _error(
                f"Document {doc_id!r} not found",
                hint="Use an `id` from list_documents.",
            )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Changeset tools
    # ------------------------------------------------------------------

    def _changeset_not_found(changeset_id: str) -> dict[str, Any]:
        return _error(
            f"Changeset {changeset_id!r} not found",
            hint="Use an `id` from list_changesets or get_default_changeset.",
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_changesets(
        status: Annotated[
            str | None,
            Field(
                description=(
                    'Only changesets with this status: "open", "review", "scheduled", '
                    '"published" or "reverted". Omit for all; "open" is the usual one.'
                )
            ),
        ] = None,
        include_documents: Annotated[
            bool,
            Field(
                description=(
                    "true (default) lists each changeset's documents (id, doc_type, slug). "
                    "false returns only document_count."
                )
            ),
        ] = True,
    ) -> dict[str, Any]:
        """
        List changesets, newest first: the groups of drafts that publish together.

        Use status="open" to see what is waiting to go live. A document listed with
        `also_in` is in more than one open changeset. A draft in no changeset is not
        listed here; find it with list_documents(published=false) and compare ids.
        """
        client = _get_client()
        params: dict[str, Any] = {"include_documents": str(include_documents).lower()}
        if status is not None:
            params["status"] = status
        r = await client.get("/api/changesets", params=params, headers=_read_headers())
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=READ_ONLY)
    async def get_default_changeset() -> dict[str, Any]:
        """
        Return the changeset new drafts collect in (for joellithgow.com, "Staging"),
        with its documents, or {"changeset": null} when none is open.

        This is "what is waiting to go live". Read-only: it never creates one.
        """
        client = _get_client()
        r = await client.get("/api/changesets/default", headers=_read_headers())
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=READ_ONLY)
    async def get_changeset(
        changeset_id: Annotated[
            str, Field(description="Changeset id from list_changesets or get_default_changeset.")
        ],
    ) -> dict[str, Any]:
        """Return ONE changeset with its documents and its status."""
        client = _get_client()
        r = await client.get(f"/api/changesets/{changeset_id}", headers=_read_headers())
        if r.status_code == 404:
            return _changeset_not_found(changeset_id)
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=CREATE)
    async def create_changeset(
        title: Annotated[str, Field(description='Name for the changeset, e.g. "Half-written".')],
    ) -> dict[str, Any]:
        """
        Create an empty open changeset, e.g. to move a post that is not ready out of
        the default one. Not idempotent: calling twice creates two.
        """
        client = _get_client()
        r = await client.post("/api/changesets", json={"title": title})
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=UPDATE)
    async def add_document_to_changeset(
        changeset_id: Annotated[str, Field(description="An open changeset's id.")],
        doc_id: Annotated[str, Field(description="The document id (nanoid) from list_documents.")],
    ) -> dict[str, Any]:
        """
        Add a document to a changeset. It does not leave the changesets it is
        already in, so to MOVE a document call remove_document_from_changeset on
        the old one too, or it will be in both (shown as `also_in`).

        Only "open" and "review" changesets accept documents.
        """
        client = _get_client()
        r = await client.post(f"/api/changesets/{changeset_id}/documents/{doc_id}")
        if r.status_code in (400, 404, 409):
            return _error(
                r.json().get("error", f"HTTP {r.status_code}"),
                hint=(
                    "Check both ids with get_changeset and list_documents. A 409 means the "
                    "document is already in this changeset."
                ),
            )
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=UPDATE)
    async def remove_document_from_changeset(
        changeset_id: Annotated[str, Field(description="The changeset to take it out of.")],
        doc_id: Annotated[str, Field(description="The document id (nanoid) from list_documents.")],
    ) -> dict[str, Any]:
        """
        Remove a document from a changeset. The document and its draft are kept; it
        is just no longer published with that changeset. If it was in no other, it
        is now in none and publish_changeset will not ship it.
        """
        client = _get_client()
        r = await client.delete(f"/api/changesets/{changeset_id}/documents/{doc_id}")
        if r.status_code == 404:
            return _error(
                r.json().get("error", "Not found"),
                hint="Check the ids with get_changeset; the document must be in this changeset.",
            )
        r.raise_for_status()
        return r.json()

    @mcp.tool(annotations=PUBLISH)
    async def publish_changeset(
        changeset_id: Annotated[
            str, Field(description="Changeset id from list_changesets or get_default_changeset.")
        ],
    ) -> dict[str, Any]:
        """
        Publish EVERY document in a changeset at once. They go live together and the
        site is rebuilt once (one changeset.published webhook, one Netlify build).

        Only call this when asked to publish. Check get_changeset first: a changeset
        ships everything in it, including a half-written post, and remove_document_from_changeset
        takes one out beforehand. Only open, review or scheduled changesets publish.
        """
        client = _get_client()
        r = await client.post(f"/api/changesets/{changeset_id}/publish")
        if r.status_code == 404:
            return _changeset_not_found(changeset_id)
        if r.status_code == 400:
            return _error(
                r.json().get("error", "Cannot publish"),
                hint="Only open, review or scheduled changesets can be published.",
            )
        r.raise_for_status()
        return r.json()

    _reject_unknown_arguments(mcp)

    return mcp
