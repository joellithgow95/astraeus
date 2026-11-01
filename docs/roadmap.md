# Astraeus — Implementation Roadmap

Phases are ordered by dependency. A future agent should always start at the earliest incomplete phase.

**Current status:** Phases 0–14 complete (including EPIC-001 VPP MVP primitives, mediakit core+admin+MCP+CLI, starlette-editor Phases 1–3 + Phase 14 polish, ADR 017 observability). starlette-cms-gateways in use by joellithgow.com (Phases GW-0 to GW-4). starlette-editor is fully polished and PyPI-ready.

**Use cases:** See `docs/use-cases/` for worked examples of Astraeus applied to real projects. These inform roadmap priorities and surface new primitives.

**Primary (original design driver):**
- [`personal-site-joellithgow.md`](use-cases/personal-site-joellithgow.md) — agent-driven publishing for joellithgow.com; drives MCP server (Phase 5), editor auto-generation (Phase 10), and the webhook→build trigger architecture

**Extended (discovered through real-world application):**
- [`vpp-underwriting-intake.md`](use-cases/vpp-underwriting-intake.md) — structured intake forms as governed documents; drives `NumberField`, `SelectField`, `BoolField`, `URLField` and validates schema API for external consumers
- [`vpp-rule-governance.md`](use-cases/vpp-rule-governance.md) — actuarial rule parameters as singleton governed config; drives singleton pattern (ADR 009) and compliance/audit narrative
- [`vpp-eval-dataset.md`](use-cases/vpp-eval-dataset.md) — human-scored AI runs as documents; introduces `DocumentRef` (ADR 010) and closes the governed feedback loop
- [`vpp-test-case-library.md`](use-cases/vpp-test-case-library.md) — curated test scenarios as authored fixtures; drives `JSONField` and webhook-triggered CI pattern
- [`vpp-prompt-versioning.md`](use-cases/vpp-prompt-versioning.md) — AI pipeline prompts as singleton governed config; extends ADR 009 and positions Astraeus for production AI use cases

Together these establish Astraeus as a **governed data platform** — not just a headless CMS. The personal site is the original use case and remains the primary one; the VPP use cases extend the same primitives into AI system governance territory.

---

## Phase 0 — Scaffold ✅

- [x] UV workspace monorepo
- [x] All three package `pyproject.toml` files
- [x] Package skeleton (`__init__.py`, field types, registry, app stub, exceptions)
- [x] `CLAUDE.md`, `docs/architecture.md`, `docs/roadmap.md`, ADRs
- [x] `examples/demo/app.py`
- [x] Initial GitHub push

---

## Phase 1 — starlette-cms core ✅

**Goal:** A running CMS server that can create, read, update, delete, and publish documents via HTTP. No migrations, no webhooks yet — just the core data layer and API.

**Done when:** `examples/demo/app.py` starts without errors, and all endpoint tests pass.

### 1a — Pydantic model generation from block definitions ✅
- [x] `@block` / `@cms.block` decorator injects `block_type: Literal[name]` field
- [x] Field types (`TextField`, `RichTextField`, `ImageField`, `ListField`, `BlockField`) generate correct Pydantic `FieldInfo`
- [x] `ListField(blocks=[...])` generates discriminated union via `Union[...] + Field(discriminator="block_type")`
- [x] Self-referential blocks via string forward refs + `model_rebuild()`
- [x] `@cms.document()` decorator builds the document Pydantic model

### 1b — Piccolo ORM + SQLite ✅
- [x] `cms_documents` table (id, type, slug, body JSON, meta JSON, created_at, updated_at, published, published_at)
- [x] `cms_meta` table (key, value) — stores `schema_version`
- [x] WAL mode enabled on SQLite init
- [x] Lifespan context opens and closes the DB connection

### 1c — Document CRUD API ✅
- [x] `GET /api/documents` — list with `type`, `slug`, `published`, `limit`, `offset` query params
- [x] `POST /api/documents` — create, validates body against registered block schemas
- [x] `GET /api/documents/{id}` — get by nanoid
- [x] `PATCH /api/documents/{id}` — partial update
- [x] `DELETE /api/documents/{id}` — hard delete
- [x] `POST /api/documents/{id}/publish` — set published=true, published_at=now
- [x] `POST /api/documents/{id}/unpublish` — set published=false

### 1d — Auth middleware ✅
- [x] Mode: `"none"` — all routes open
- [x] Mode: `"apikey"` — mutating endpoints require `Authorization: Bearer {key}`
- [x] Mode: callable — `async (request) -> bool`, applied to mutating endpoints
- [x] `read_auth=True` flag extends auth to GET endpoints

### 1e — Schema introspection ✅
- [x] `GET /api/schema` — JSON Schema for all registered block types
- [x] `GET /api/schema/{block_type}` — JSON Schema for one block type, including `cms:field_meta`
- [x] `GET /api/schema/version` — current schema version string

### 1f — Nanoid document IDs ✅
- [x] `nanoid` generates document IDs at create time
- [x] IDs are opaque strings, not sequential integers

### 1g — Extension route mechanism ✅
- [x] `cms.register_extension_route()` stores routes in a list
- [x] `cms.app` (lazy property) builds the Starlette app, including extension routes, on first access
- [x] Calling `register_extension_route()` after `cms.app` is accessed raises `RuntimeError`

### 1h — Tests ✅
- [x] Unit tests for block decorator + Pydantic model generation
- [x] Unit tests for registry (register, collision, override, discover)
- [x] Integration tests for all document CRUD endpoints (httpx + ASGITransport)
- [x] Integration tests for auth modes
- [x] Integration tests for schema introspection

---

## Phase 2 — starlette-cms schema versioning ✅

**Goal:** The CMS detects version mismatches on startup and refuses to run until migrations are applied.

**Done when:** `cms migrate` CLI works end-to-end and the startup check correctly blocks on mismatch.

- [x] `cms_meta` stores `schema_version = "{package_version}"`
- [x] On startup: read `schema_version`, compare to package version, raise `CMSSchemaMismatch` on mismatch
- [x] `@cms.migration(from_version, to_version)` decorator registers migration functions
- [x] Migration chain runner — executes functions in order, updates `schema_version` after each
- [x] `cms migrate status` — shows pending migrations
- [x] `cms migrate --dry-run` — shows what would run
- [x] `cms migrate` — applies pending migrations
- [x] `cms validate` — checks all stored documents validate against current block schemas

---

## Phase 3 — starlette-cms webhooks + polish ✅

**Goal:** Publish events trigger registered webhook URLs. The core CMS is production-ready.

**Done when:** Publishing a document fires registered webhooks, and `examples/demo` works end-to-end including Netlify rebuild trigger.

- [x] `GET /api/webhooks` — list registered webhooks
- [x] `POST /api/webhooks` — register a URL + event list
- [x] `DELETE /api/webhooks/{id}` — remove a webhook
- [x] Webhook delivery on: `document.created`, `document.updated`, `document.deleted`, `document.published`, `document.unpublished`
- [x] Webhook payload: `{ event, document_id, document_type, slug, timestamp }`
- [x] Fire-and-forget delivery (v1 — no retry queue)
- [x] `ImageField` + `MediaBackend` protocol — if configured, validate image keys on save

---

## Phase 4 — starlette-cms testing utilities + VPP MVP primitives (EPIC-001) ✅

**Goal:** Testing utilities for block authors, plus all primitives needed for the VPP underwriting MVP.

**Done when:** All six EPIC-001 stories pass, acceptance smoke test green (241 tests).

### 4a — Testing utilities (STORY-006) ✅
- [x] `validate_block(block_cls, data)` — validates dict against block, returns model or raises `ValidationError`
- [x] `BlockTestCase.assert_valid/invalid/fields/field_label/field_required/field_optional/roundtrip`
- [x] `RegistryTestCase` — fresh CMS per test, `assert_registered`, `assert_no_collision`
- [x] `contrib/blocks/basic.py` blocks are tested using `BlockTestCase`

### 4b — New field types (STORY-001) ✅
- [x] `NumberField` — float with optional `min_value`, `max_value`, `precision`
- [x] `SelectField` — string constrained to a `choices` list (Pydantic `Literal` enum)
- [x] `BoolField` — boolean with configurable default
- [x] `URLField` — string with `format: url` in `cms:field_meta`
- [x] `JSONField` — arbitrary dict/list, maps to `Any` in Pydantic

### 4c — Singleton documents (STORY-002) ✅
- [x] `@cms.block("name", singleton=True)` marks a block as singleton
- [x] `singleton_status` column (`active` / `archived`) on `cms_documents`
- [x] Archive-then-activate publish semantics (only one active at a time)
- [x] `GET /api/singletons/{block_type}` — fetch active singleton
- [x] `POST /api/singletons/{block_type}/publish` — publish new version
- [x] `GET /api/singletons/{block_type}/history` — version history
- [x] `cms.documents.get_singleton(block_type)` Python accessor

### 4d — Immutable fields (STORY-003) ✅
- [x] `immutable=True` flag on any `_BaseField` subclass
- [x] `__immutable_fields__` set on generated Pydantic models
- [x] PATCH endpoint strips immutable fields from update payload
- [x] `immutable: true` in `cms:field_meta` schema output

### 4e — DocumentRef (STORY-004) ✅
- [x] `DocumentRef(block_type=..., on_delete="block"|"nullify"|"cascade")` field type
- [x] `__ref_fields__` mapping on generated models
- [x] `_validate_refs()` — checks referenced doc exists and has correct block_type on create/patch
- [x] `_check_ref_integrity()` — enforces `on_delete` semantics on delete
- [x] `_bulk_resolve_refs()` — O(1)-per-field inline resolution in list responses
- [x] `resolve_refs` query param on `GET /api/documents`

### 4f — List filters (STORY-005) ✅
- [x] `filters=` JSON query param for body-field filtering
- [x] `filter[key]=value` bracket syntax alternative
- [x] `order_by` and `order` (asc/desc) query params
- [x] `_coerce_filter_value()` — URL string to bool/int/float/str
- [x] `_matches_filters()` — Python-level body field matching (v1)
- [x] `cms.documents.list(block_type, filters=...)` Python accessor
- [x] `filters_applied` in list response metadata

---

## Phase 5 — starlette-cms MCP server ✅

**Goal:** An agent (Claude Code, Claude Desktop) can create, edit, and publish documents using tools.

**Done when:** Running `starlette-cms mcp serve --url ... --api-key ...` registers tools that a connected agent can call successfully against a live CMS.

- [x] `pip install starlette-cms[mcp]` installs `mcp` dependency
- [x] `starlette-cms mcp serve --url {base_url} --api-key {key}` starts MCP server
- [x] Tools: `list_documents`, `get_document`, `create_document`, `update_document`, `delete_document`, `publish_document`, `unpublish_document`, `list_block_types`, `get_block_schema`
- [x] Changeset tools: `list_changesets`, `get_default_changeset`, `get_changeset`, `create_changeset`, `add_document_to_changeset`, `remove_document_from_changeset`, `publish_changeset`. Tested against a real in-process CMS (`build_mcp_server(..., transport=ASGITransport(...))`)
- [x] Tool descriptions are agent-legible (explain what each arg does, what gets returned)
- [x] Auth passed as `Authorization: Bearer {key}` header on every request

---

## ADR 014 — append_only=True documents ✅

**Goal:** Machine-written audit records with structural immutability — auto-published on creation, frozen body, cannot be deleted.

**Done when:** POST creates and publishes in one call; PATCH and DELETE return 405; read endpoints unaffected.

- [x] `append_only: bool` on `BlockRegistration`, `@block()`, and `@cms.block()`
- [x] `BlockRegistry.is_append_only()` introspection method
- [x] `POST /api/documents` with append_only block type → auto-publishes atomically
- [x] `PATCH /api/documents/{id}` on append_only document → 405 Method Not Allowed
- [x] `DELETE /api/documents/{id}` on append_only document → 405 Method Not Allowed
- [x] `ImmutableDocumentError` exception exported from `starlette_cms`
- [x] Webhook for append_only creation carries `append_only: true` in extra payload
- [x] `POST /api/documents` falls back to block registry for append_only / singleton types

---

## Phase 6 — mediakit core ✅

**Goal:** A running Mediakit server that can accept uploads, serve IIIF derivatives, and maintain the catalog.

**Can develop in parallel with starlette-cms Phases 2–4.**

**Done when:** The upload flow works end-to-end against a real S3-compatible bucket, IIIF derivatives are generated and cached, and the catalog accurately reflects the bucket state.

### 6a — Storage backend ✅
- [x] `S3CompatibleBackend`: `prepare_upload`, `confirm_exists`, `get_url`, `delete`, `list_objects`
- [x] Presigned PUT URL generation (obstore)
- [x] Presigned GET URL generation
- [x] Public URL mode (`public_read=True`)
- [x] Works against GCS, Cloudflare R2, AWS S3 (endpoint_url configuration)

### 6b — Upload flow ✅
- [x] `POST /upload/prepare` — validates request, generates presigned PUT URL, returns `{ upload_url, key, expires_at }`
- [x] `POST /upload/confirm` — verifies object exists, runs processing pipeline, inserts into catalog
- [x] Processing pipeline: EXIF strip, WebP conversion, max dimension cap (Pillow) — `mediakit/processing/pipeline.py` (opt-in: `keep_originals=False`; by default uploads are kept untouched)
- [x] Content-addressed key generation: `originals/{sha256_prefix}/{filename}`

### 6c — Catalog ✅
- [x] `Catalog.insert_asset`, `get_asset`, `list_assets`, `update_asset`, `delete_asset`
- [x] `get_or_create_derivative`, `insert_derivative`
- [x] `set_references` (upsert-and-replace), `remove_references`
- [x] `find_orphans`, `export_csv`

### 6d — IIIF Image API ✅
- [x] IIIF URL parsing and parameter validation — `mediakit/routes/iiif.py`
- [x] Level 1 compliance: `full/square/x,y,w,h` region; `full/max/w,/,h/w,h/!w,h` size; `0/90/180/270` rotation; `default/color/gray` quality; `jpg/webp/png` format
- [x] `GET /iiif/{key}/info.json`
- [x] `GET /iiif/{key}/{region}/{size}/{rotation}/{quality}.{format}` — 302 redirect to cached derivative (or generate + cache)

### 6e — Asset routes ✅
- [x] `GET /assets` — paginated, filterable
- [x] `GET /assets/{key}` — metadata + URL
- [x] `PATCH /assets/{key}` — update alt_text, tags
- [x] `DELETE /assets/{key}` — removes from bucket, catalog, and all references

### 6f — References route ✅
- [x] `POST /references` — upsert-and-replace for a content record
- [x] `DELETE /references` — remove all references for a content record

### 6g — Auth ✅
- [x] Same three modes as starlette-cms: none, apikey, callable

---

## starlette-cms-gateways Phase GW-0 — import_ref ✅

**Goal:** Add `import_ref` as a first-class nullable indexed column on `CMSDocument`.

- [x] `import_ref = Varchar(length=256, null=True, index=True)` on `CMSDocument`
- [x] Migration `003_import_ref.py` — `ALTER TABLE` + composite index on `(doc_type, import_ref)`
- [x] `GET /api/documents?import_ref=...` — filter support
- [x] `POST /api/documents` accepts `"import_ref"` in body; 409 on duplicate `(doc_type, import_ref)`
- [x] `PATCH /api/documents/{id}` accepts `"import_ref"` in body
- [x] GET responses include `"import_ref"` field
- [x] `starlette-cms` version bumped to `0.5.0`

---

## starlette-cms-gateways Phase GW-1 — Package scaffold + core framework ✅

**Goal:** A working gateway framework that any developer can subclass in ~50 lines.

- [x] `packages/starlette-cms-gateways/` package with `pyproject.toml`, `README.md`, `CLAUDE.md`
- [x] `GatewayItem`, `SyncResult` dataclasses
- [x] `BaseGateway` ABC — `fetch()` abstract, `sync()` framework-provided
- [x] `CMSClient` — `find_by_import_ref`, `upsert`, `get_last_synced`, `save_sync_state`
- [x] `GatewaySyncStateBlock` — singleton block helper; `register(cms)` utility
- [x] `gateways` CLI — `list`, `status`, `sync`, `register-blocks` commands
- [x] MCP server factory `build_gateway_mcp_server()` (`[mcp]` extra)
- [x] ADR 015 written

---

## starlette-cms-gateways Phase GW-4 — Ownership, state and publishing ✅ ([ADR 023](decisions/023-gateway-state-and-publishing.md))

**Goal:** Re-syncs that write nothing, updates that go live, and sync state that survives restarts.

- [x] `owned_fields` + owned-only hashing; no-op re-syncs
- [x] `SyncRange` (`since_last_sync` / `all_time` / `custom`) through admin API, CLI and MCP
- [x] Cursor and job history in the CMS database; `/api/gateways/{name}/cursor|runs`; `RemoteSyncState`
- [x] Cursor never freezes; deferred and failed items are reported in the result (caught up by `all_time`)
- [x] `draft_verdict`: the gateway's own draft is finished, a person's is deferred; review gateways take repeated updates
- [x] A run is one changeset, published once at the end; the cursor moves only after it publishes

---

## Data documents and post slices ✅ ([ADR 026](decisions/026-data-documents-and-post-slices.md))

**Goal:** Gateways write one data document per item; posts show slices of that data.

- [x] `TagsField` chips and `LinksField` cards
- [x] `ListField(DocumentRef(...))` with write-time validation, and a multi-ref picker in the editor
- [x] Document types filed under a sidebar group (`group=`)
- [x] joellithgow.com: `spotify_like` / `inat_observation` data documents, slices on `blog_post`, months and outings migrated
- [ ] Slice editor in the editor, and "paste a URL" creating a `link` document
- [ ] joellithgow.com: embedded post links moved to `link` documents

---

## starlette-cms-gateways Phase GW-2 — Tests ✅

**Goal:** Full test coverage for `import_ref` CMS changes and gateway framework.

- [x] `import_ref` filter on `GET /api/documents`
- [x] 409 on duplicate `(doc_type, import_ref)` at `POST /api/documents`
- [x] `import_ref` included in GET response body
- [x] `CMSClient.upsert()` create/update/skip logic (mocked via `respx`)
- [x] `gateways list` discovers test gateway via entry point
- [x] End-to-end idempotency: local CMS + test gateway, second sync run skips all
- [x] `GatewayItem` and `SyncResult` dataclass unit tests

---

## starlette-cms-gateways Phase GW-3 — Examples ✅

**Goal:** Reference gateway implementations for documentation.

- [x] `examples/spotify_liked_songs/blocks.py` + `gateway.py`
- [x] `examples/inaturalist_outings/blocks.py` + `gateway.py`

---

## Phase 7 — mediakit admin UI ✅

**Goal:** A usable browser interface for uploading and browsing assets, including picker mode.

- [x] `GET /admin` — asset browser grid (IIIF square/256, thumbnails)
- [x] Normal mode: click → detail view with metadata editor
- [x] Picker mode (`?picker=1`): click → `postMessage("mediakit:asset-selected", ...)` + close
- [x] Cancel button → `postMessage("mediakit:picker-cancelled", ...)`
- [x] `GET /admin/upload` — drag-and-drop uploader (vanilla JS, no framework)
- [x] Filtering by type and tag

---

## Phase 8 — mediakit MCP server ✅

**Goal:** An agent can list, search, and manage media assets using tools.

- [x] `pip install mediakit[mcp]`
- [x] `mediakit mcp serve --url {base_url} --api-key {key}`
- [x] Tools: `list_assets`, `get_asset`, `search_assets`, `update_asset`, `delete_asset`, `get_iiif_url`

---

## Phase 9 — mediakit CLI ✅

- [x] `mediakit sync` — walk bucket, reconcile against catalog, insert missing records
- [x] `mediakit gc` — remove orphaned assets with no references
- [x] `mediakit export` — export catalog as CSV

---

## Phase 10 — starlette-editor Phase 1 (foundation) ✅

**Prerequisite:** starlette-cms Phase 1 complete.

**Goal:** The ProseMirror bridge works and `<se-block-editor>` renders a live editor for a `RichTextField`.

- [x] `ProseMirrorBridge.generate_schema()` — derives ProseMirror schema + `blockTypes` metadata from BlockRegistry
- [x] `/api/editor-schema` endpoint (registered on CMS via extension route)
- [x] `RichTextField.field_meta()` emits `"field_type": "rich_text"`; `ImageField` emits `"field_type": "image"`
- [x] `fieldWidget()` in `editor.js` driven by `field_type` from `cms:field_meta` (heuristics kept as fallback)
- [x] PM doc init/save: `RichTextField` round-trips as ProseMirror JSON (not markdown)
- [x] Editor SPA (`editor.js`) already ships as working vanilla JS; no esbuild/TypeScript pipeline needed at this phase
- [x] Tests: `test_prosemirror_bridge.py` (13), `test_rich_text_field_meta.py` (3), `test_editor_schema_integration.py` (4)

---

## Phase 11 — starlette-editor Phase 2 (StandardEditor) ✅

- [x] Full StandardEditor SPA (document list + block canvas)
- [x] Block canvas: `ListField.field_meta()` → `field_type: "block_list"`; `BlockField.field_meta()` → `field_type: "block"`
- [x] Block canvas UI: card per block, collapsible body, drag-and-drop reorder, type picker with keyboard nav
- [x] Block picker: single-type direct-add; multi-type dropdown with ArrowUp/Down/Enter/Escape
- [x] Publish/unpublish flow (already present from Phase 10)
- [x] Meta panel (`buildMetaPanel`) — Document info details: ID (copy), created, updated, published_at, import_ref, singleton_status
- [x] Auth shell protection (already present from Phase 10)
- [x] PM-flush bug fix in `saveDocument` — RichTextField fields flush as PM JSON, not markdown
- [x] Toolbar active state: `updateToolbarState` called on every `dispatchTransaction`; `hasMark`, `isInList` helpers
- [x] `mediaBase` injected into `window.__EDITOR_CONFIG__` from `Editor(media_base=...)`
- [x] Tests: `test_fields.py` (17), `test_editor_schema_integration.py` extended (+2: `test_shell_injects_media_base_null`, `test_shell_injects_media_base`)

---

## Phase 12 — starlette-editor Phase 3 (media + custom editors) ✅

- [x] `image_picker` widget: `buildImagePickerField` — thumbnail preview + Choose/Clear when `mediaBase` set; plain text input fallback
- [x] `openImagePicker` → Mediakit picker iframe in modal overlay; `postMessage` integration (`mediakit:asset-selected`, `mediakit:picker-cancelled`)
- [x] Richtext toolbar polish: `toggleList` replaces `wrapIn` for list nodes (toggle-to-unwrap); inline `code` mark button
- [x] `<se-block-picker>` keyboard navigation (ArrowUp/Down/Enter/Escape) implemented in `buildBlockTypePicker`
- [x] `core.js` extraction — deferred; `editor.js` is self-contained and the personal site does not yet need a custom editor overlay
- [x] Node views for nested block types — deferred to North Star (requires PM schema changes + serialization overhaul)

---

## Phase 13 — Observability (ADR 017) ✅

**Goal:** Structured logging + OpenTelemetry spans on all critical paths across the monorepo.

- [x] `astraeus-otel`: `reset_for_tests()` helper in `setup.py` + 5 tests covering `TelemetryConfig` and `setup_telemetry()`
- [x] `starlette-cms`: `tracer = trace.get_tracer(__name__)` in `documents.py` + `webhooks.py`
- [x] `starlette-cms`: OTel spans on `cms.documents.list/create/patch/delete/publish` (DB operations)
- [x] `starlette-cms`: OTel span on `cms.webhooks.deliver` with `url` + `event` attributes
- [x] `starlette-cms`: Silent swallows fixed — `body_parse_failed_in_row`, `meta_parse_failed_in_row`, `events_parse_failed`, `row_field_parse_failed`
- [x] `mediakit`: OTel spans on `mediakit.processing.pipeline`, `mediakit.upload.confirm`, `mediakit.storage.prepare_upload`, `mediakit.storage.confirm_exists`
- [x] `starlette-cms-gateways`: OTel spans on `gateways.sync` (with `item_count`) and `gateways.client.upsert` (with `action`)
- [x] `starlette-cms-gateways`: Silent swallow fixed — `bad_last_synced_timestamp`
- [x] `starlette-editor`: `structlog>=24.0` + `opentelemetry-api>=1.25` added to deps; `NullHandler` installed

---

## Phase 14 — starlette-editor Polish ✅

**Goal:** Polish `starlette-editor` to a production-ready, self-contained, offline-capable state — ready for PyPI release.

- [x] **Build tooling** — `package.json`, `esbuild.config.mjs`, `vitest.config.mjs`, `Makefile`; `npm ci && npm run build` produces `starlette_editor/static/editor.js` (~440KB minified)
- [x] **Module decomposition** — extracted 1,900-line monolithic `editor.js` into `editor_src/` (11 modules across `api`, `state`, `prosemirror/`, `components/`, `standard/`)
- [x] **npm-bundled ProseMirror** — replaced `import(https://esm.sh/...)` CDN dynamic imports with static npm imports; `window.PM` shim eliminated; fully offline-capable
- [x] **Real markdown serialization** — replaced plain-text stubs in `markdownToPmDoc` / `pmDocToMarkdown` with `prosemirror-markdown` (markdownit-based); headings, bold, italic, lists, code fences, blockquotes all round-trip correctly
- [x] **JS unit tests** — 83 Vitest tests in `editor_src/__tests__/` (utils, fields, markdown round-trips, block canvas operations)
- [x] **Expanded Python tests** — 19 tests total (+14): auth path tests (sync/async allow/deny), ProseMirrorBridge field-type coverage (RichTextField, ImageField, SelectField, NumberField, BoolField, DocumentRef), empty registry edge case, display_order and label propagation
- [x] **Vendor cleanup** — deleted `starlette_editor/static/vendor/` directory (all empty/broken stub files)
- [x] **README rewrite** — accurate docs: installation, config, auth, frontend dev workflow, field type mapping table

---

## Personal site integration (ongoing)

**Not a numbered phase — runs in parallel once Phase 1 is stable.**

- [ ] Deploy starlette-cms + mediakit backend (Fly.io or Railway)
- [ ] Add `[tool.uv.sources]` to joellithgow pointing at local packages during dev
- [ ] Migrate Astro content from MDX files to CMS API fetches
- [ ] Register Netlify build hook as a webhook
- [ ] Configure Claude Code MCP servers (starlette-cms + mediakit)
- [ ] Blog post: "Building Astraeus and using it on my own site"

---

## starlette-chat — Collaborative AI Chat (ADR 019) ✅

**Goal:** A governed AI chat collaborator that participates in live ProseMirror editing sessions
as a first-class collab peer — submitting steps through the same WS authority as human editors.

| Phase | What was built |
|-------|----------------|
| CH-1 | `starlette-chat` package scaffold; `ChatSession`, `ChatMessage`, `SystemPrompt`, `ModelConfig` block types; `BaseProvider` ABC |
| CH-2 | Python ProseMirror builder (`markdown_to_pm` via mistune, no Node dependency); block-granularity differ (`diff_docs` → replace steps) |
| CH-3 | `ChatAPI` Starlette sub-app; chat WS endpoint (`/api/chat/sessions/{id}/ws`); `AnthropicProvider` streaming; `ToolDispatcher`; `edit_document` submits PM steps via collab WS with `clientID: "claude-assistant"` |
| CH-4A | `ChatPanel` JS embed module; streaming token UI; tool chips; edit notices; toolbar chat button + unread badge |
| CH-4B | Collab WS peer registry; `peers` array in `init` message; `peer_joined`/`peer_left` broadcasts; AI presence indicator (◆) in toolbar |
| CH-5 | Demo app integration; end-to-end wiring; regression clean |
| CH-6 | Integration tests (`test_integration.py`); `CLAUDE.md`; roadmap and architecture docs updated |

**Key architectural note:** AI edits go through the collab WS authority, not REST PATCH.
This keeps connected human editors in sync in real time and writes `"claude-assistant"` into
step history alongside human client IDs.  See ADR 019 and `docs/architecture.md`.

---

## Phase POR-1 — astraeus-portal admin hub ✅

**Goal:** A mountable Starlette sub-app that serves as a central navigation hub linking all Astraeus admin interfaces (editor, gateways, chat, media, and custom apps).

| Module | What was built |
|--------|----------------|
| Portal class | `Portal(apps=[...])` mountable Starlette sub-app with `/` shell page; `PortalApp` dataclass with name, path, description, icon, tags |
| compose_app() | `compose_app(cms, editor, media, chat, gateways)` wires everything at conventional paths + portal at `/` with auto-populated cards; lifespan composition |
| Shell page | Dark-themed card grid with header, tags, package version info, empty state, auth guard, keyboard nav (1–9), responsive layout |
| Tests | 20 tests covering PortalApp, Portal rendering, empty state, header/footer links, package info, tags, auth (blocked/allowed), keyboard nav, compose_app all-variants, lifespan composition, mount path overrides |
| Integration | Portal installed as `[compose]` optional extra; workspace member via `packages/*` glob |

**Usage:**
```python
from astraeus_portal import Portal, PortalApp
portal = Portal(apps=[
    PortalApp("Editor", "/editor/shell", "Create and edit content", "✏️"),
])
app = Starlette(routes=[Mount("/", app=portal.app)])
```

---

## Editor Foundations (ADRs 020–022) — planned

**Goal:** Make the fallback authoring surfaces good and reliable, and consolidate the widget
layer both surfaces should have been built from. Triggered by a mobile audit of `/editor/shell`
that found navigation deleted below 640px, silent data loss on both surfaces, and five
unrelated visual languages across the admin pages.

| Phase | What it does |
|-------|--------------|
| ED-1 | Durability — nav guards, `beforeunload`, local draft buffer, fix the collab `reject` handler so pending steps rebase instead of being discarded |
| ED-2 | Unblock small screens — restore document navigation, `dvh`, 16px input floor, safe-area insets, 44px targets, block reorder without drag |
| ED-3 | Token stylesheet in `starlette-cms` adopted by all five admin surfaces; promote widgets to `components/`; fix the `standard/` ↔ `embed/` dependency direction |
| ED-4 | Merge the two changeset panels (1,798 lines across two files) into one component |
| ED-5 | Shell IA — route stack with back navigation, pending view, bottom action bar |
| ED-6 | `embed/` adopts the shared layer; delete its duplicate ProseMirror mount, markdown, and notice |

**Key architectural note:** server-side ProseMirror step application is a documented non-goal
(ADR 021 §5). Correct convergence comes from client-side rebasing, which `prosemirror-collab`
already provides. `cms_steps` is an advisory log of client-asserted steps, not a verified
history — ADR 018 §3 is amended accordingly.

Plan: [`docs/plans/ED-editor-foundations-implementation-plan.md`](plans/ED-editor-foundations-implementation-plan.md).
Widget reference: [`docs/editor-widgets.md`](editor-widgets.md).

---

## North Star (post-v1, don't implement yet)

- Collaborative editing via `cms_steps` table + WebSocket authority endpoint + `prosemirror-collab`
- SQLite FTS5 or Postgres `tsvector` full-text search
- Draft/revision history
- Block package marketplace / curated index
- `starlette-suite` meta-package convenience install
