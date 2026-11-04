# astraeus-portal

Central navigation hub (portal) for Astraeus — discover and navigate all admin
interfaces from one page.

Part of the [Astraeus](https://github.com/ASneakyToast/astraeus) content stack.

---

## Install

```bash
pip install astraeus-portal
```

With the optional compose shortcut:

```bash
pip install astraeus-portal[compose]
```

---

## Usage

### Standalone portal

```python
from starlette.applications import Starlette
from starlette.routing import Mount
from astraeus_portal import Portal, PortalApp

portal = Portal(
    apps=[
        PortalApp(
            name="Editor",
            path="/editor/",
            description="Create and edit content documents",
            icon="✏️",
        ),
        PortalApp(
            name="Media",
            path="/media/admin",
            description="Browse, upload, and manage media assets",
            icon="🖼️",
        ),
        PortalApp(
            name="Gateways",
            path="/gateways/",
            description="Sync data from external services",
            icon="🔄",
        ),
    ]
)

app = Starlette(
    routes=[
        Mount("/cms", app=cms.app),
        Mount("/editor", app=editor.app),
        Mount("/", app=portal.app),  # portal at root
    ],
    lifespan=cms.lifespan,
)
```

### Composed app (auto-wires everything)

```python
from astraeus_portal import compose_app

app = compose_app(
    cms=cms,
    editor=editor,
    media=media,
    gateways=gateway_admin,
    feedback=feedback_admin,
    docs_url="https://github.com/ASneakyToast/astraeus#readme",
    github_url="https://github.com/ASneakyToast/astraeus",
)
```

This mounts everything at conventional paths and adds the portal at `/`
with auto-populated app cards. Lifespans are composed automatically.

Visit `http://localhost:8000/` to see the portal.

### Portal at the site root, beside a CMS that is also there

When the CMS is mounted at `/` too (its public API at `/api/...`), splice the
portal's routes in ahead of it instead of mounting it, or the portal's mount
would answer every path:

```python
app = Starlette(routes=[*portal.routes, Mount("/editor", app=editor.app), Mount("/", app=cms.app)])
```

---

## The app bar

The portal also serves `app-bar.js`: a thin strip across the top of every
admin page with a home link to the portal and a link to each app, the current
one highlighted. A page loads it with one tag:

```html
<script src="/app-bar.js" defer></script>
```

The editor, gateway admin, feedback admin and astraeus-media add that tag when given
`portal_url=` (astraeus-media: `MediakitConfig(portal_url=...)`); `compose_app` sets
it on each for you. The bar reads the app list from `GET /apps.json`, which sits
behind the portal's `auth` like the page. If that fails it still shows the home
link. It sets `--astraeus-bar-h` on the page, so a layout sized to the viewport
takes `calc(100dvh - var(--astraeus-bar-h, 0px))`, and it draws nothing inside
an iframe (the media picker).

---

## PortalApp fields

| Field        | Required | Description                                      |
|-------------|----------|--------------------------------------------------|
| `name`       | Yes      | Human-readable app name (e.g. "Editor")          |
| `path`       | Yes      | URL to the app's shell page (e.g. `/editor/`) |
| `description`| No       | One-line description shown on the card            |
| `icon`       | No       | Emoji or icon character                           |
| `tags`       | No       | List of tag strings for categorization            |

---

## Auth

Pass a callable to protect the portal page:

```python
Portal(apps=apps, auth=lambda r: r.headers.get("X-Admin") == "secret")
```

Unauthenticated visitors are redirected to the login path, and `apps.json`
answers 401. With starlette-cms, the usual guard is the session login:
`auth=lambda r: check_session_auth(r, cms)`.

---

## Development

```bash
# Install
uv sync --package astraeus-portal

# Run tests
uv run pytest packages/astraeus-portal/

# Type check
uv run pyright packages/astraeus-portal/

# Lint
uv run ruff check packages/astraeus-portal/
```

---

## Status

Alpha — 0.1.0