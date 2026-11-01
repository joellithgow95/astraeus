# Astraeus — Architecture

Astraeus is a governed data platform for Python/Starlette developers. It provides the
primitives — blocks, documents, singletons, references, webhooks — that make any structured
data artifact auditable, versioned, and agent-accessible.

The canonical use case is editorial content (see `docs/use-cases/personal-site-joellithgow.md`),
but the same stack works for intake forms, rule configuration, AI pipeline prompts, eval
datasets, and curated test cases (see `docs/use-cases/`).

## The full stack

```
┌─────────────────────────────────────────────────────────────────────┐
│  Your Starlette / FastAPI app                                        │
│                                                                      │
│  app.mount("/cms",    app=cms.app)    ← starlette-cms               │
│  app.mount("/editor", app=editor.app) ← starlette-editor            │
│  app.mount("/media",  app=media)      ← mediakit                    │
│  app.mount("/chat",   app=chat.app)   ← starlette-chat              │
└──────────┬────────────────────────────────────────┬─────────────────┘
           │ webhooks on document.published          │ MCP tools
           ▼                                         ▼
 Netlify / Vercel build hook              Claude Code / Claude Desktop
           │                               (create, edit, publish docs
           ▼                                via starlette-cms MCP)
Astro (or any static frontend)
  fetches from /cms/api at build time
```

---

## Package relationships

```
                    ┌─────────────────────┐
                    │   starlette-cms     │
                    │                     │
                    │  - BlockRegistry    │
                    │  - Document API     │
                    │  - Webhook system   │
                    │  - Schema introspect│
                    │  - ProseMirrorBridge│
                    │    (optional stub)  │
                    └──────────┬──────────┘
                               │ depends on
               ┌───────────────┴────────────────┐
               │                                │
  ┌────────────▼──────────┐        ┌────────────▼────────────┐
  │   starlette-editor    │        │  starlette-cms MCP      │
  │                       │        │  (optional, [mcp] extra) │
  │  - StandardEditor SPA │        │                         │
  │  - EditorContext API  │        │  HTTP client wrapper    │
  │  - ProseMirror UI     │        │  exposes CMS API as     │
  │  - Activates bridge   │        │  agent tools            │
  └───────────────────────┘        └─────────────────────────┘
          both are clients of cms.app — no special access

  ┌────────────────────────┐        ┌─────────────────────────┐
  │      mediakit          │        │  mediakit MCP           │
  │                        │        │  (optional, [mcp] extra) │
  │  - S3-compatible store │        │                         │
  │  - SQLite catalog      │        │  HTTP client wrapper    │
  │  - IIIF Image API      │        │  exposes media API as   │
  │  - Admin UI            │        │  agent tools            │
  │  - Picker protocol     │        └─────────────────────────┘
  └────────────────────────┘

  ┌────────────────────────────────────┐
  │      starlette-chat                │
  │                                    │
  │  - ChatAPI Starlette sub-app       │
  │  - ChatSession/Message blocks      │
  │  - Python PM builder + differ      │
  │  - AnthropicProvider (streaming)   │
  │  - ToolDispatcher (CMS HTTP +      │
  │    collab WS client)               │
  │  - ChatPanel JS embed module       │
  └────────────────────────────────────┘

  starlette-chat DEPENDS ON starlette-cms — never the reverse.
  starlette-cms has no knowledge of starlette-chat.

  starlette-chat acts as a collab WS CLIENT of starlette-cms:
  edit_document submits ProseMirror steps to /api/documents/{id}/collab
  using client_id="claude-assistant", identical to human browser clients.

  starlette-chat uses mediakit indirectly via the CMS HTTP API
  (search_media tool calls a mediakit assets endpoint if configured);
  it never imports mediakit directly.

  mediakit has NO dependency on starlette-cms.
  starlette-cms references mediakit only via the MediaBackend Protocol —
  never a direct import.
```

---

## starlette-cms internals

```
┌────────────────────────────────────────────┐
│  CMS instance                              │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │  BlockRegistry                       │  │
│  │  name → Pydantic model class         │  │
│  │  collision detection, entry points   │  │
│  └──────────────┬───────────────────────┘  │
│                 │ used by                  │
│  ┌──────────────▼───────────────────────┐  │
│  │  Content API  (lazy — built on first │  │
│  │  access to cms.app)                  │  │
│  │                                      │  │
│  │  /api/documents    CRUD + publish    │  │
│  │  /api/schema       introspection     │  │
│  │  /api/webhooks     registration      │  │
│  │  [extension routes from plugins]     │  │
│  └──────────────┬───────────────────────┘  │
│                 │                          │
│  ┌──────────────▼───────────────────────┐  │
│  │  Storage (Piccolo ORM)               │  │
│  │  SQLite (WAL) or Postgres            │  │
│  │                                      │  │
│  │  cms_documents  — content store      │  │
│  │  cms_meta       — schema version     │  │
│  └──────────────────────────────────────┘  │
└────────────────────────────────────────────┘
```

---

## The lazy `cms.app` property

`cms.app` is a **lazy property** — the Starlette sub-application is built on first access. This is critical because:

1. Extension routes must be registered before the app is built
2. `starlette-editor` registers `/api/editor-schema` at `Editor.__init__` time
3. The host app then accesses `cms.app` when constructing its routing table

**Correct initialization order:**
```python
cms = CMS(...)           # 1. create CMS
editor = Editor(cms=cms) # 2. editor registers extension route on cms
app = Starlette(routes=[ # 3. cms.app built here — routes finalized
    Mount("/cms", app=cms.app),
    Mount("/editor", app=editor.app),
])
```

Calling `register_extension_route()` after `cms.app` has been accessed raises `RuntimeError`.

---

## Block type safety — discriminated unions

Blocks use Pydantic v2 discriminated unions for type-safe validation of polymorphic block lists. The `@block` / `@cms.block` decorator automatically injects a `block_type` literal field as the discriminator:

```python
@block("hero")
class HeroBlock:
    # After decoration:
    block_type: Literal["hero"] = "hero"  # injected
    title: str = ...

# Internally, ListField(blocks=[HeroBlock, CardBlock]) generates:
BlockBody = Annotated[
    Union[HeroBlock, CardBlock],
    Field(discriminator="block_type")
]
```

Consumer code never sets `block_type` manually — it's always injected and always matches the registered name.

---

## The agent/editor peer model

The editor and the MCP agent are **peers** — both are HTTP clients of the CMS API. Neither has privileged access. The CMS has no knowledge of either:

```
Human (browser)         Agent (Claude)
       │                      │
       ▼                      ▼
starlette-editor     starlette-cms MCP
  (reads schema,       (create_document,
   renders UI,          edit_document_body,
   body: collab WS,     update_document,
   PATCH plain fields)  publish_document)
       │                      │
       └──────────┬───────────┘
                  ▼
           cms.app HTTP API
           /api/documents            (CRUD: plain fields, lifecycle)
           /api/documents/{id}/edit, /collab  (rich text, via CollabAuthority)
           /api/schema
           /api/editor-schema
```

This means an agent can do everything the editor can — create, edit, publish — just without a visual interface.

Every field is live (ADR 027). A rich-text field has one writer, its `CollabAuthority`: the
editor streams body edits over the collab WebSocket, and an agent changes a body with
`edit_document_body`, anchored edits (find and replace, replace a section, insert after a heading)
that the authority resolves against the live document and applies as steps, so a person with the
post open sees them land and keeps their typing. A whole-body `update_document` must carry the
`base_version` it read and is refused if the body has moved on. Plain fields (title, tags, image,
dates, refs) are last-writer-wins per field; they are written with PATCH by both peers, to be
broadcast live to open editors.

---

## Webhook → build trigger flow

```
Agent calls publish_document tool
         │
         ▼
POST /api/documents/{id}/publish
         │
         ▼
CMS fires webhook: document.published
         │
         ▼
POST https://api.netlify.com/build_hooks/{id}
         │
         ▼
Netlify rebuilds — Astro fetches fresh content
```

The agent never triggers a build directly. It publishes, and the webhook handles the cascade. This means the agent's `publish_document` tool has the right semantic — it publishes content, not "rebuilds a site."

---

## AI edits go through CollabAuthority

**AI edits go through CollabAuthority.** starlette-chat submits ProseMirror steps via the same
WS endpoint as human editors. `client_id='claude-assistant'` appears in step history. The
alternative (REST PATCH) was rejected because connected human editors would not see AI edits
in real time.

The two WebSocket connections from the browser are independent:

```
Browser (human editor)
  │
  ├─ collab WS ──► /cms/api/documents/{doc_id}/collab
  │                 (shared ProseMirror step authority for all peers)
  │
  └─ chat WS ───► /chat/api/chat/sessions/{session_id}/ws
                   (streaming turn: tokens, tool chips, edit notices)

Server-side (ToolDispatcher._edit_document)
  │
  └─ collab WS ──► /cms/api/documents/{doc_id}/collab
                    (AI opens its own WS connection, submits steps as
                     clientID="claude-assistant", then closes it)
```

This design means the AI's edits propagate to all connected human editors in real time via the
existing collab broadcast — no special case required in the editor frontend.

---

## Mediakit — no proxying, ever

Mediakit never proxies files through the application server. The upload and serve flows both bypass the server entirely:

**Upload flow:**
```
Browser → POST /upload/prepare → Mediakit → presigned PUT URL
Browser → PUT [file bytes] → S3/GCS/R2 directly
Browser → POST /upload/confirm → Mediakit → processes + catalogs
```

**Serve flow:**
```
Request → /media/iiif/{key}/... → Mediakit → 302 redirect → S3/GCS/R2 directly
```

The first IIIF request for a given derivative generates it (Pillow) and stores it in the bucket. All subsequent requests are redirects to the cached derivative — no server processing.

---

## Rich text storage

Rich text fields (`RichTextField`) store **ProseMirror document JSON** natively — not HTML, not Markdown:

```json
{
  "type": "doc",
  "content": [
    { "type": "paragraph", "content": [{ "type": "text", "text": "Hello" }] }
  ]
}
```

This is the canonical format shared by the editor (which uses ProseMirror) and the CMS (which stores and validates it). Consumers (Astro frontend) are responsible for rendering ProseMirror JSON to HTML — there are standard libraries for this in JS.

---

## Content-addressed keys in Mediakit

Asset keys use the scheme `originals/{sha256_prefix}/{filename}` where the prefix is derived from file content:

- **No silent overwrites** — different file content → different key, even with the same filename
- **Implicit derivative invalidation** — if an original is replaced, the new key is different, old derivatives are orphaned and cleaned by `mediakit gc`
- **No "replace in place"** — content replacement is always upload-new + delete-old

---

## Lifespan composition

When running multiple Astraeus packages together, lifespans must be composed explicitly:

```python
@asynccontextmanager
async def lifespan(app):
    async with cms.lifespan_context(app):
        async with media.lifespan_context(app):
            yield

app = Starlette(..., lifespan=lifespan)
```

Each package's `lifespan_context()` handles its own database connections and cleanup. The host composes them — packages don't know about each other's lifecycle.
