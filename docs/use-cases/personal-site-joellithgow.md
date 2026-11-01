# Use Case: Personal Site (joellithgow.com)

**Status:** Active — primary motivation for Astraeus  
**Explored:** 2026-06-13  
**Repo:** `github.com/ASneakyToast/joellithgow` (separate from this monorepo)  
**Packages:** `starlette-cms`, `starlette-editor`, `mediakit` (full stack)  
**Roadmap impact:** This is the original design driver — headless-first architecture, MCP as first-class goal, Netlify webhook, agent-driven publishing, ProseMirror editor

> **Current state (2026-10-28).** The sketch below is the original plan from 2026-06. The live site has moved on:
> the CMS runs in Joel's k3s cluster, not Fly.io or Railway, and the content model follows
> [ADR 026](../decisions/026-data-documents-and-post-slices.md): one `blog_post` type that shows collected data
> (Spotify likes, iNaturalist observations, definitions, links) through slices, and gateways that write one data
> document per item. The joellithgow repo's `docs/blog-conventions.md` describes the model as it is.

---

## Background

joellithgow.com is an Astro.js personal site currently using MDX files for content — blog posts,
project write-ups, and portfolio entries checked into the repository. Every content update
requires opening a file in an editor, writing Markdown, committing, and pushing. There is no
visual editing, no media management, and no way to publish content without touching code.

This is the use case Astraeus was built to solve. Not as a one-off backend, but as a reusable
stack that works the same way across this site, client projects, and anything else that needs
a governed content layer.

---

## The target stack

```
joellithgow (Astro.js, github.com/ASneakyToast/joellithgow)
        │
        │  fetch content at build time
        ▼
starlette-cms + mediakit backend
  (deployed on Fly.io or Railway)
        │
        │  document.published webhook
        ▼
Netlify build hook → site rebuilds with fresh content
        │
        │  (parallel path)
        ▼
Claude Code (via MCP server)
  → create_document, update_document, publish_document tools
  → Joel publishes a blog post by talking to Claude
```

The agent path is a first-class goal, not a nice-to-have. The whole point is that Joel can open
Claude Code, say "publish a post about what we built today," and the content goes live — no file
editing, no git, no deploy step required beyond what Astraeus handles.

---

## Content types

The site has three primary content types, each becoming a block:

### `BlogPost`
```python
@cms.block("blog_post")
class BlogPost:
    title:        TextField(label="Title", required=True)
    slug:         TextField(label="URL Slug", required=True)
    published_on: TextField(label="Publish Date")   # DateField — Phase 4
    summary:      TextField(label="Summary", required=True,
                             help_text="Used in og:description and post listings")
    body:         RichTextField(label="Body")
    cover_image:  ImageField(label="Cover Image", required=False)
    tags:         ListField(item_type=TextField(), label="Tags")
```

### `Project`
```python
@cms.block("project")
class Project:
    title:        TextField(label="Title", required=True)
    slug:         TextField(label="URL Slug", required=True)
    summary:      TextField(label="Summary", required=True)
    body:         RichTextField(label="Body")
    repo_url:     TextField(label="Repo URL", required=False)
    live_url:     TextField(label="Live URL", required=False)
    cover_image:  ImageField(label="Cover Image", required=False)
    tags:         ListField(item_type=TextField(), label="Tags")
    featured:     BoolField(label="Featured on homepage", default=False)  # Phase 4
```

### `Page`
```python
@cms.block("page")
class Page:
    title:        TextField(label="Title", required=True)
    slug:         TextField(label="URL Slug", required=True)
    body:         RichTextField(label="Body")
```

---

## The migration path from MDX

**Phase A — Backend deployed, Astro fetching from CMS:**
1. Deploy starlette-cms + mediakit to Fly.io or Railway
2. Seed the CMS by importing existing MDX content as documents
3. Update Astro to fetch from `/cms/api/documents?type=blog_post` at build time instead of
   reading MDX files from disk
4. Register the Netlify build hook as a CMS webhook on `document.published`
5. Verify: publish a document → Netlify rebuilds → post appears on site

At this point the site works identically from the user's perspective, but content now lives in
the CMS rather than in the repository. MDX files are archived.

**Phase B — Agent-driven publishing:**
1. Install `starlette-cms[mcp]`
2. Configure the MCP server in Claude Code: `starlette-cms mcp serve --url ... --api-key ...`
3. Claude Code can now: `list_block_types`, `get_block_schema`, `create_document`,
   `update_document`, `publish_document`
4. Workflow: Joel tells Claude what to write → Claude drafts the document → Joel reviews in
   Claude Code → `publish_document` → webhook fires → Netlify rebuilds → live

**Phase C — Visual editor (optional):**
1. Deploy starlette-editor alongside starlette-cms
2. Editor reads `/api/editor-schema` — auto-generates form fields from block definitions
3. Joel can edit content in a browser UI if he prefers — same API, same blocks, same publish flow

---

## The webhook → build trigger flow

This is the core loop that makes the agent workflow feel right:

```
Joel: "publish the post about Astraeus"
         │
         ▼
Claude calls publish_document(id="doc_01j...")
         │
         ▼
POST /api/documents/doc_01j.../publish
         │
         ▼
CMS sets published=true, fires document.published webhook
         │
         ▼
POST https://api.netlify.com/build_hooks/{hook_id}
         │
         ▼
Netlify rebuilds — Astro fetches from CMS API
         │
         ▼
joellithgow.com shows the new post
```

The agent never triggers a build directly. It publishes content; the webhook handles the
cascade. The semantic is right — `publish_document` means "this content is ready for the world,"
not "rebuild the site."

---

## Why this use case drives the core design

Several Astraeus architectural decisions are direct consequences of this use case:

**Headless-first** — the site is Astro.js, not a Django app. There is no "built-in admin UI"
because the frontend is entirely separate. The CMS is an API that any frontend can consume.

**MCP as first-class** — the agent publishing path is the primary way Joel interacts with his
own site. This isn't a plugin or an afterthought — it's why the MCP server is a named phase
(Phase 5), not a "someday" item.

**Webhook over direct integration** — there is no "publish to Netlify" button in the CMS.
There's a webhook that Netlify happens to listen to. This keeps the CMS agnostic to the frontend
hosting platform — works the same way for Vercel, Cloudflare Pages, or any other hook-capable
service.

**Schema introspection API** — `GET /api/schema/{block_type}` exists so that the editor (and
any other consumer) can render a form without knowing the block definition at compile time.
This is what allows the editor to auto-generate its UI and what allows the MCP server to describe
fields to an agent in a structured way.

**ProseMirrorBridge in starlette-cms, not starlette-editor** — the bridge needs deep knowledge
of field types (`RichTextField`, `BlockField`, etc.) which live in the CMS. The editor activates
it but doesn't own it. See ADR 004.

---

## What this use case does NOT need

Deliberately out of scope for the personal site:

- **Multi-user auth** — it's a personal site; one API key is fine
- **Collaborative editing** — north star, not v1
- **Dynamic/database-backed schemas** — block types are defined in Python; Joel adds them by
  editing code and deploying
- **Complex approval workflows** — there's no editorial team; publish means publish

These are handled by the USAA-VPP use cases, which extend Astraeus into governed multi-user
territory. The personal site is deliberately the simplest possible configuration.

---

## Deployment target

```
Fly.io or Railway
  ├── starlette-cms (FastAPI app)
  │     ├── SQLite (WAL) — simple, no separate DB service needed
  │     └── starlette-cms[mcp] optional extra
  └── mediakit
        ├── Cloudflare R2 (S3-compatible, cheap egress)
        └── mediakit[mcp] optional extra

joellithgow (Astro.js) → Netlify
  ├── build-time: fetch from CMS API
  └── deploy hook: registered as CMS webhook
```

SQLite is sufficient for a personal site — one writer (Joel, via agent), reads only at build
time. No connection pool needed. Upgrade path to Postgres is available if ever needed.

---

## The agent workflow in practice

The intended daily usage pattern:

```
Joel opens Claude Code
Joel: "I want to write a post about today's Astraeus session — we explored
       how it could work as a governed data platform for AI systems,
       specifically for USAA's underwriting workflow."

Claude:
  1. Calls list_block_types → sees "blog_post"
  2. Calls get_block_schema("blog_post") → gets field definitions
  3. Drafts title, slug, summary, body from the conversation context
  4. Calls create_document("blog_post", {...}) → doc created in draft
  5. Shows Joel the draft inline
  6. Joel: "looks good, publish it"
  7. Claude calls publish_document(id) → webhook fires → Netlify rebuilds
  8. Post is live at joellithgow.com/blog/...
```

Total time from "I want to write a post" to "it's live": under 5 minutes, zero file editing,
zero git operations.

This is the use case that justifies everything else. If this doesn't work cleanly, Astraeus
hasn't delivered on its promise.
