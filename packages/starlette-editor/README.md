# starlette-editor

Visual editing UI for [starlette-cms](../starlette-cms/) — ProseMirror-based rich text, auto-generated form fields, block canvas, and image picker — all driven from your block schema.

Part of the [Astraeus](https://github.com/ASneakyToast/astraeus) content stack.

---

## Install

```bash
pip install starlette-editor
```

## Quickstart

```python
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS
from starlette_editor import Editor

cms = CMS(database_url="sqlite:///./content.db")
editor = Editor(cms=cms, mount_path="/editor")

app = Starlette(
    routes=[
        Mount("/", app=cms.app),
        Mount("/editor", app=editor.app),
    ],
    lifespan=cms.lifespan,
)
```

Editor UI available at: **`/editor/shell`**

---

## Configuration

```python
Editor(
    cms=cms,
    mount_path="/editor",        # where the editor sub-app is mounted (default: /editor)
    media_base="/media",         # optional: mount path of mediakit — enables the image picker
    auth=lambda r: bool(r.user), # optional: callable (request) -> bool protecting /shell
)
```

**Initialization order:** Create the `CMS` instance first, register all blocks, then create `Editor`. `Editor.__init__` registers `/api/editor-schema` on the CMS via `register_extension_route()`.

---

## Auth

Pass any sync or async callable that takes a `Request` and returns `bool`:

```python
# Sync
Editor(cms=cms, auth=lambda r: r.headers.get("X-Admin") == "secret")

# Async
async def require_session(request):
    return await request.session.get("user") is not None

Editor(cms=cms, auth=require_session)
```

Unauthenticated requests to `/shell` receive a `401 Unauthorized` response.

---

## Frontend development

The editor frontend is a bundled vanilla JS application (no framework).
The built artifact at `starlette_editor/static/editor.js` is committed to the repo.

To rebuild after modifying source files in `editor_src/`:

```bash
cd packages/starlette-editor

# Install frontend dependencies (once)
npm ci

# Production build → starlette_editor/static/editor.js
npm run build

# Dev build with inline source maps
npm run build:dev

# JS unit tests
npm test

# Or use make:
make build-frontend
make test-js
```

Source layout:

```
editor_src/
  index.js              ← boot(), buildShell(), DOMContentLoaded handler
  api.js                ← authenticated fetch helpers
  state.js              ← shared state, setState()
  prosemirror/
    markdown.js         ← markdownToPmDoc(), pmDocToMarkdown()
    toolbar.js          ← toolbar command execution and active-state tracking
    mount.js            ← mountProseMirrorEditors(), destroyPmInstances()
  components/
    block-canvas.js     ← ListField / BlockField interactive card canvas
    image-picker.js     ← Mediakit iframe picker widget
    toast.js            ← toast notification system
    confirm.js          ← confirmation dialog
    meta-panel.js       ← document metadata panel
  standard/
    utils.js            ← pure utility functions (humanizeType, docTitle, etc.)
    fields.js           ← fieldWidget() dispatch, buildFieldGroup()
    render.js           ← renderTypeList, renderDocList, renderHeader, renderForm
    actions.js          ← selectType, selectDoc, saveDocument, togglePublish, etc.
```

All ProseMirror packages are bundled from npm — no CDN dependency at runtime.

---

## How it works

1. `Editor.__init__` registers a `/api/editor-schema` endpoint on the CMS.
2. The browser loads `/editor/shell` — a server-rendered HTML page that injects config and loads the JS bundle.
3. `editor.js` boots, fetches `/api/editor-schema`, and auto-generates a form UI from the block schema.
4. CRUD operations go directly to the CMS's own `/api/documents/*` endpoints — the editor is a thin client.

---

## Inline editing on a static site

A site loads `/editor/embed.js` and the editor edits the page in place when you are logged in. The page tells it what is editable:

| Attribute | On | Meaning |
|---|---|---|
| `data-cms-id`, `data-cms-type` | a card | The document this card shows |
| `data-cms-field="title"` | an element in the card | The field that element edits |
| `data-cms-collapsible` | a body field | Show an Expand toggle instead of mounting the editor straight away |

### Showing drafts on a listing

A static site is built from published documents, so a post that has never been published has no card to edit. A listing can opt in to having the editor draw them in the browser, while editing:

```html
<div data-cms-list="blog_post"> …the built cards… </div>

<template data-cms-draft-template="blog_post">
  <article>
    <time data-cms-fill="publish_date" data-cms-fill-format="date"></time>
    <span data-cms-field="title" data-cms-fill="title"></span>
    <p data-cms-field="description" data-cms-fill="description"></p>
    <div data-cms-field="body_markdown" data-cms-collapsible
         data-cms-fill="body_markdown" data-cms-fill-format="excerpt"></div>
  </article>
</template>
```

A feed can show several document types: name them space-separated (`data-cms-list="blog_post definition"`) and give each its own `<template data-cms-draft-template="…">`. A type with no template is not drawn.

The template holds one card, built from the same markup and classes as a real one so the site's own styles apply. `data-cms-fill="<field>"` sets that element's text from the draft; `data-cms-fill-format` is `date` or `excerpt` (plain text of a rich-text or text field, shortened). Fields the editor can edit (`data-cms-field`) are editable on the drawn card; a template with none draws a read-only preview.

The toolbar's picker decides which drafts appear: **Live + all drafts** (the default), **Live only**, or **Live + one open changeset**. Choosing a changeset also makes it the one edits are saved into. The choice is remembered per browser. Drawn drafts are labelled "Draft · not live"; live posts with unpublished edits are labelled "Unpublished edits". Neither Discard nor the single-document Publish fallback touches a drawn draft, so a post you only looked at is never wiped or shipped.

**Publish** ships the changeset you are working in. If this browser has not picked one (a phone, a fresh login), it ships the CMS's default changeset (`CMS(default_changeset=…)`, [ADR 025](../../docs/decisions/025-default-changeset.md)), and the confirm names it and how many documents it holds. With no default and nothing picked, it publishes the one post on the page.

Reload (Close, Discard, Publish) returns the page to what visitors see.

### The editor's own chrome

The changeset panel, chat panel, publish drawer and toasts are written against the shared design tokens and `editor.css` classes. A host site loads neither (and must not: `tokens.css` defines `:root` variables and `editor.css` styles `.btn`), so `embed/styles.js` supplies what they need, scoped to the editor's own elements, and `embed-styles.test.js` keeps its token values equal to `tokens.css`. Add a token to a panel and that test fails until it is in `CHROME_TOKENS`.

---

## Field type mapping

| Python field class | Editor widget |
|---|---|
| `RichTextField` | ProseMirror rich text editor |
| `TextField` | `<input type="text">` (or `<textarea>` for long-name fields) |
| `ImageField` | Mediakit iframe picker (falls back to text input) |
| `ListField` / `BlockField` | Block canvas with drag-and-drop reorder |
| `SelectField` | `<select>` dropdown |
| `NumberField` | `<input type="number">` with min/max |
| `BoolField` | Toggle switch |
| `DocumentRef` | `<select>` of documents of `block_type` (text input when untyped) |
| `ListField(DocumentRef(...))` | A row per referenced document (move up/down, remove; an ID whose document is gone is shown as missing); search box adds by title (`?q=`). Also inside block cards |
| `TagsField` | Chips; type-ahead from `GET /api/tags`; typed tags lowercased and hyphenated |
| `LinksField` | A card per link (URL, title, description, date, tag chips); paste a URL to add, move up/down, remove |
| `JSONField` | `<textarea>` with JSON pretty-printing |

---

## Status

All implementation phases complete. Self-contained bundle — no CDN dependency.
