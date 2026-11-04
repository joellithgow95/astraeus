"""
Chat tool dispatcher.

Implements the curated AI tool suite scoped to the editing session.
Tools are thin wrappers over the existing CMS and astraeus-media HTTP APIs
plus the ProseMirror collab WebSocket for edit operations.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

_EMPTY_DOC: dict[str, Any] = {"type": "doc", "content": []}


def _field_doc(document: dict[str, Any], field: str) -> dict[str, Any]:
    """Return *field*'s ProseMirror doc from a fetched document, or an empty doc."""
    body = document.get("body") or {}
    if isinstance(body, str):
        body = json.loads(body)
    return body.get(field) or _EMPTY_DOC


async def _receive_until(ws: Any, wanted: set[str], context: dict[str, Any]) -> dict[str, Any]:
    """Read collab messages until one whose type is in *wanted* arrives.

    The server interleaves other messages (peer presence, and ``changeset`` when
    it groups an edit into a changeset it just created), so the next message is
    not necessarily the reply. A created changeset is adopted into *context* so
    later edits in this turn join it rather than making another.

    :raises RuntimeError: if the server reports an error (e.g. document gone).
    """
    while True:
        message = json.loads(await ws.recv())
        message_type = message.get("type")
        if message_type in wanted:
            return message
        if message_type == "changeset":
            context["active_changeset_id"] = message.get("id")
        elif message_type == "error":
            raise RuntimeError(message.get("message", "collab error"))


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class ToolDispatcher:
    """Dispatch AI tool calls to the appropriate backend.

    :param cms_base: HTTP base URL for the CMS API (e.g. ``http://localhost:8000/cms``).
    :param api_key: API key for CMS authentication.
    :param collab_ws_base: WebSocket base URL for the collab endpoint
        (e.g. ``ws://localhost:8000/cms``).
    """

    def __init__(
        self,
        cms_base: str,
        api_key: str,
        collab_ws_base: str,
    ) -> None:
        self._cms_base = cms_base.rstrip("/")
        self._api_key = api_key
        self._collab_ws_base = collab_ws_base.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def dispatch(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Route a tool call to the appropriate implementation.

        :param tool_name: Tool name (matches a tool registered in ``make_tools``).
        :param tool_input: Tool input dict from the LLM.
        :param context: Session context — may contain ``doc_id``, ``version``,
            ``draft_body``, ``selection``.
        :returns: Result dict forwarded back to the LLM as a tool result.
        """
        if tool_name == "edit_document":
            doc_id = tool_input.get("doc_id") or (context or {}).get("doc_id")
            if not doc_id:
                # On a page listing several documents none is selected until the
                # user clicks into one, so ask rather than guess.
                return {
                    "status": "error",
                    "message": (
                        "No document selected. Ask the user which one they mean, "
                        "or pass doc_id (search_documents can find it)."
                    ),
                }
            return await self._edit_document(
                doc_id=doc_id,
                markdown_content=tool_input["markdown_content"],
                edit_rationale=tool_input.get("edit_rationale", ""),
                context=context,
                field=tool_input.get("field") or None,
            )

        elif tool_name == "search_documents":
            return await self._search_documents(tool_input)

        elif tool_name == "search_media":
            return await self._search_media(tool_input)

        elif tool_name == "get_available_doc_types":
            return await self._get_available_doc_types()

        elif tool_name == "get_document_history":
            return await self._get_document_history(tool_input)

        elif tool_name == "publish_document":
            return await self._publish_document(tool_input)

        elif tool_name == "create_document":
            return await self._create_document(tool_input, context)

        elif tool_name == "update_document":
            return await self._update_document(tool_input, context)

        elif tool_name == "create_changeset":
            return await self._create_changeset(tool_input)

        elif tool_name == "list_changesets":
            return await self._list_changesets()

        elif tool_name == "link_doc_to_changeset":
            return await self._link_doc_to_changeset(tool_input, context)

        else:
            return {"status": "error", "message": f"Unknown tool: {tool_name}"}

    # ------------------------------------------------------------------
    # edit_document
    # ------------------------------------------------------------------

    async def _edit_document(
        self,
        doc_id: str,
        markdown_content: str,
        edit_rationale: str,
        context: dict[str, Any],
        field: str | None = None,
    ) -> dict[str, Any]:
        """Apply edits to one rich-text field of a live document via the collab WebSocket.

        The socket is scoped to a single field. This tool used to send a doc
        without one, which overwrote the document's whole body (title,
        description, …) with it; the CMS now refuses field-less connections.
        """
        import websockets

        from .diff import diff_docs
        from .prosemirror import markdown_to_pm

        ctx = context if context is not None else {}

        document = await self._fetch_document(doc_id)
        if document is None:
            return {"status": "error", "message": f"Document {doc_id!r} not found"}

        try:
            field = await self._resolve_rich_text_field(document.get("doc_type", ""), field)
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}

        new_doc = markdown_to_pm(markdown_content)
        if not diff_docs(_field_doc(document, field), new_doc):
            return {"status": "no_change", "message": "No differences found"}

        query = urlencode({"field": field, "api_key": self._api_key})
        ws_url = f"{self._collab_ws_base}/api/documents/{doc_id}/collab?{query}"

        for _attempt in range(2):
            try:
                async with websockets.connect(ws_url) as ws:
                    # Send presence as AI client
                    await ws.send(
                        json.dumps(
                            {
                                "type": "presence",
                                "display": "Claude",
                                "client_type": "ai",
                            }
                        )
                    )
                    # init carries the field's authoritative doc at the server's
                    # version, so diff against it rather than the earlier fetch —
                    # a reconnect after a reject picks up whatever landed meanwhile.
                    init = await _receive_until(ws, {"init"}, ctx)
                    steps = diff_docs(init.get("doc") or _EMPTY_DOC, new_doc)
                    if not steps:
                        return {"status": "no_change", "message": "No differences found"}

                    # Send editing status
                    await ws.send(json.dumps({"type": "editing", "doc_id": doc_id}))

                    # Submit steps. The server groups the edit into this changeset
                    # (or creates one and tells us), so no separate link call.
                    await ws.send(
                        json.dumps(
                            {
                                "type": "steps",
                                "steps": steps,
                                "clientID": "claude-assistant",
                                "version": init.get("version", 0),
                                "doc": new_doc,
                                "activeChangesetId": ctx.get("active_changeset_id"),
                            }
                        )
                    )

                    # Wait for confirm or reject
                    result = await _receive_until(ws, {"steps", "reject"}, ctx)
                    await ws.send(json.dumps({"type": "editing_done"}))

            except Exception as exc:
                return {"status": "error", "message": str(exc)}

            if result.get("type") == "steps":
                return {
                    "status": "accepted",
                    "step_count": len(steps),
                    "edit_rationale": edit_rationale,
                    "doc_id": doc_id,
                    "field": field,
                }
            # Rejected: someone else's steps landed first — reconnect and re-diff.

        return {
            "status": "conflict",
            "message": "Version conflict after retry — ask user to try again",
        }

    async def _fetch_document(self, doc_id: str) -> dict[str, Any] | None:
        """Fetch a document with its draft body (the published body if no draft).

        The API returns the published body unless asked for the draft, and the
        edit must be judged against the draft — that's what the socket edits.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._cms_base}/api/documents/{doc_id}",
                params={"draft": "true"},
                headers=self._headers,
            )
        return resp.json() if resp.status_code == 200 else None

    async def _resolve_rich_text_field(self, doc_type: str, field: str | None) -> str:
        """Pick the rich-text field of *doc_type* to edit.

        With no *field*, a doc type's single rich-text field is used. Raises
        ``ValueError`` with guidance for the model when that isn't possible.
        """
        schema = await self._get_available_doc_types()
        if schema.get("status") != "ok":
            raise ValueError(f"Couldn't load the CMS schema: {schema.get('message')}")

        properties = schema["schema"].get(doc_type, {}).get("schema", {}).get("properties", {})
        rich_fields = [
            name
            for name, prop in properties.items()
            if prop.get("cms:field_meta", {}).get("field_type") == "rich_text"
        ]

        if field:
            if field in rich_fields:
                return field
            raise ValueError(
                f"{field!r} is not a rich-text field of {doc_type!r} "
                f"(rich-text fields: {', '.join(rich_fields) or 'none'})"
            )
        if len(rich_fields) == 1:
            return rich_fields[0]
        if not rich_fields:
            raise ValueError(
                f"{doc_type!r} has no rich-text field — use update_document for its fields"
            )
        raise ValueError(
            f"{doc_type!r} has several rich-text fields ({', '.join(rich_fields)}); "
            "pass field to choose one"
        )

    # ------------------------------------------------------------------
    # Other tools
    # ------------------------------------------------------------------

    async def _search_documents(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if "doc_type" in tool_input:
            params["doc_type"] = tool_input["doc_type"]
        if "published" in tool_input:
            params["published"] = str(tool_input["published"]).lower()
        params["limit"] = tool_input.get("limit", 10)

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._cms_base}/api/documents",
                params=params,
                headers=self._headers,
            )
            if resp.status_code == 200:
                return {"status": "ok", "documents": resp.json()}
            return {"status": "error", "message": resp.text}

    async def _search_media(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if "query" in tool_input:
            params["q"] = tool_input["query"]
        params["limit"] = tool_input.get("limit", 10)

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._cms_base}/assets",
                params=params,
                headers=self._headers,
            )
            if resp.status_code == 200:
                return {"status": "ok", "assets": resp.json()}
            return {"status": "media_search_not_configured"}

    async def _get_available_doc_types(self) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._cms_base}/api/schema",
                headers=self._headers,
            )
            if resp.status_code == 200:
                return {"status": "ok", "schema": resp.json()}
            return {"status": "error", "message": resp.text}

    async def _get_document_history(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        doc_id = tool_input.get("doc_id", "")
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._cms_base}/api/documents/{doc_id}/history",
                headers=self._headers,
            )
            if resp.status_code == 200:
                return {"status": "ok", "history": resp.json()}
            return {"status": "error", "message": resp.text}

    async def _publish_document(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        doc_id = tool_input.get("doc_id", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._cms_base}/api/documents/{doc_id}/publish",
                headers=self._headers,
            )
            if resp.status_code in (200, 201, 204):
                return {"status": "published", "doc_id": doc_id}
            return {"status": "error", "message": resp.text}

    async def _update_document(
        self, tool_input: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        doc_id = tool_input.get("doc_id")
        if not doc_id:
            return {"status": "error", "message": "doc_id is required"}
        body = tool_input.get("body", {})
        slug = tool_input.get("slug")
        payload: dict[str, Any] = {"body": body}
        if slug:
            payload["slug"] = slug
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{self._cms_base}/api/documents/{doc_id}",
                json=payload,
                headers=self._headers,
            )
            if resp.status_code == 200:
                result: dict[str, Any] = {"status": "updated", "document": resp.json()}
                await self._auto_link_changeset(doc_id, context)
                return result
            return {"status": "error", "message": resp.text}

    async def _create_document(
        self, tool_input: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._cms_base}/api/documents",
                json=tool_input,
                headers=self._headers,
            )
            if resp.status_code in (200, 201):
                new_doc = resp.json()
                result: dict[str, Any] = {"status": "created", "document": new_doc}
                if new_doc_id := new_doc.get("id"):
                    await self._auto_link_changeset(new_doc_id, context)
                return result
            return {"status": "error", "message": resp.text}

    # ------------------------------------------------------------------
    # Changeset tools
    # ------------------------------------------------------------------

    async def _auto_link_changeset(
        self, doc_id: str, context: dict[str, Any] | None
    ) -> None:
        """Link a doc to the active changeset, creating one if needed."""
        ctx = context or {}
        active_cs_id = ctx.get("active_changeset_id")

        if active_cs_id:
            try:
                await self._link_doc_to_changeset(
                    {"changeset_id": active_cs_id, "doc_id": doc_id}, context
                )
            except Exception:
                pass
            return

        # No active changeset — auto-create one
        try:
            today = datetime.now(UTC)
            auto_title = today.strftime("%b %-d")
            result = await self._create_changeset({"title": auto_title})
            if result.get("status") == "created":
                new_cs = result["changeset"]
                new_cs_id = new_cs["id"]
                ctx["active_changeset_id"] = new_cs_id
                await self._link_doc_to_changeset(
                    {"changeset_id": new_cs_id, "doc_id": doc_id}, context
                )
        except Exception:
            pass

    async def _create_changeset(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        title = tool_input.get("title", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._cms_base}/api/changesets",
                json={"title": title},
                headers=self._headers,
            )
            if resp.status_code in (200, 201):
                return {"status": "created", "changeset": resp.json()}
            return {"status": "error", "message": resp.text}

    async def _list_changesets(self) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._cms_base}/api/changesets?status=open",
                headers=self._headers,
            )
            if resp.status_code == 200:
                return {"status": "ok", "changesets": resp.json().get("changesets", [])}
            return {"status": "error", "message": resp.text}

    async def _link_doc_to_changeset(
        self, tool_input: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        changeset_id = tool_input.get("changeset_id") or (context or {}).get(
            "active_changeset_id"
        )
        doc_id = tool_input.get("doc_id") or (context or {}).get("doc_id")
        if not changeset_id:
            return {"status": "error", "message": "changeset_id required"}
        if not doc_id:
            return {"status": "error", "message": "doc_id required"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._cms_base}/api/changesets/{changeset_id}/documents/{doc_id}",
                headers=self._headers,
            )
            if resp.status_code in (200, 201, 204):
                return {
                    "status": "linked",
                    "changeset_id": changeset_id,
                    "doc_id": doc_id,
                }
            return {"status": "error", "message": resp.text}


# ---------------------------------------------------------------------------
# LangChain tool factory
# ---------------------------------------------------------------------------


def make_tools(dispatcher: ToolDispatcher, context: dict[str, Any]) -> list:
    """Return a list of LangChain tools backed by *dispatcher*.

    Each tool closes over *context* (the session's ``doc_id``, ``version``,
    ``draft_body``, etc.) so callers don't need to thread it through every
    call.  The underlying :class:`ToolDispatcher` methods are unchanged.

    :param dispatcher: Configured dispatcher for this session.
    :param context: Session context dict forwarded to each dispatcher call.
    :returns: List of LangChain ``BaseTool`` instances ready for
        ``model.bind_tools()``.
    """
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    async def edit_document(
        markdown_content: str,
        edit_rationale: str,
        scope: str = "full",
        doc_id: str = "",
        field: str = "",
    ) -> dict:
        """Rewrite a document's rich-text prose (e.g. a blog post body) as Markdown.

        markdown_content replaces the whole field. Only rich-text fields are
        editable here — use update_document for fields like title or tags.
        doc_id defaults to the document the user is working on; pass one found
        via search_documents to target another. field is only needed when the
        document type has more than one rich-text field.
        """
        tool_input: dict[str, Any] = {
            "markdown_content": markdown_content,
            "edit_rationale": edit_rationale,
            "scope": scope,
        }
        if doc_id:
            tool_input["doc_id"] = doc_id
        if field:
            tool_input["field"] = field
        return await dispatcher.dispatch("edit_document", tool_input, context)

    @lc_tool
    async def search_documents(
        doc_type: str = "",
        published: bool | None = None,
        limit: int = 10,
    ) -> dict:
        """Find existing CMS documents by type or content."""
        tool_input: dict[str, Any] = {"limit": limit}
        if doc_type:
            tool_input["doc_type"] = doc_type
        if published is not None:
            tool_input["published"] = published
        return await dispatcher.dispatch("search_documents", tool_input, context)

    @lc_tool
    async def publish_document(doc_id: str) -> dict:
        """Publish a document. This triggers a site rebuild."""
        return await dispatcher.dispatch(
            "publish_document", {"doc_id": doc_id}, context
        )

    @lc_tool
    async def get_available_doc_types() -> dict:
        """List all CMS document types and their fields."""
        return await dispatcher.dispatch("get_available_doc_types", {}, context)

    @lc_tool
    async def update_document(doc_id: str, body: dict, slug: str = "") -> dict:
        """Update fields on an existing CMS document. Use for structured docs (experience, projects, etc.) where fields like company, title, start_date need to be set directly. Do NOT use edit_document for these — that tool is only for rich_text prose fields."""
        tool_input: dict[str, Any] = {"doc_id": doc_id, "body": body}
        if slug:
            tool_input["slug"] = slug
        return await dispatcher.dispatch("update_document", tool_input, context)

    @lc_tool
    async def create_document(doc_type: str, body: dict, slug: str) -> dict:
        """Create a new CMS document."""
        return await dispatcher.dispatch(
            "create_document",
            {"doc_type": doc_type, "body": body, "slug": slug},
            context,
        )

    @lc_tool
    async def create_changeset(title: str = "") -> dict:
        """Create a new changeset for grouping document edits for atomic publish."""
        return await dispatcher.dispatch("create_changeset", {"title": title}, context)

    @lc_tool
    async def list_changesets() -> dict:
        """List all currently open changesets."""
        return await dispatcher.dispatch("list_changesets", {}, context)

    @lc_tool
    async def link_doc_to_changeset(
        changeset_id: str = "", doc_id: str = ""
    ) -> dict:
        """Add a document to a changeset. Falls back to the active changeset from context if changeset_id is omitted."""
        return await dispatcher.dispatch(
            "link_doc_to_changeset",
            {"changeset_id": changeset_id, "doc_id": doc_id},
            context,
        )

    return [
        edit_document,
        update_document,
        search_documents,
        publish_document,
        get_available_doc_types,
        create_document,
        create_changeset,
        list_changesets,
        link_doc_to_changeset,
    ]
