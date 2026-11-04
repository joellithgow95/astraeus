"""Editor routes — /shell, /embed.js, and /static/*"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette_cms.app_bar import app_bar_tag as _app_bar_tag

if TYPE_CHECKING:
    from starlette_editor.app import Editor

STATIC_DIR = pathlib.Path(__file__).parent / "static"


def _file_hash(path: pathlib.Path) -> str:
    """Return first 8 chars of the file's MD5 for cache-busting."""
    if not path.exists():
        return "0"
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


_CSS_HASH = _file_hash(STATIC_DIR / "editor.css")
_JS_HASH = _file_hash(STATIC_DIR / "editor.js")


def make_editor_routes(editor: Editor) -> list:
    """Return the routes for the editor sub-application.

    Routes:
      GET /shell     — HTML shell page (injects JS config, loads static assets)
      GET /embed.js  — Public embed script for live editing on static sites
      /static/*      — Static file serving (editor.css, editor.js, embed.js)
    """

    async def shell_endpoint(request: Request) -> HTMLResponse:
        """Serve the single-page editor shell."""
        # Honour optional auth guard
        if editor.auth is not None:
            allowed = editor.auth(request)
            # Support both sync and async auth callables
            if hasattr(allowed, "__await__"):
                allowed = await allowed
            if not allowed:
                # Redirect unauthenticated visitors to the login page, sending
                # them back to the shell (?next=) once they have signed in.
                return RedirectResponse(
                    f"{editor.login_path}?next={request.url.path}",
                    status_code=302,
                )

        mount = editor.mount_path.rstrip("/")
        # Shared design tokens, served by starlette-cms (ADR 020 §4). Loaded
        # before editor.css so the stylesheet can consume the custom properties.
        cms_mount = editor.cms.mount_path.rstrip("/")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CMS Editor</title>
  <link rel="stylesheet" href="{cms_mount}/static/tokens.css" />
  <script src="{cms_mount}/static/theme.js"></script>{_app_bar_tag(editor.portal_url)}
  <link rel="stylesheet" href="{mount}/static/editor.css?v={_CSS_HASH}" />
</head>
<body>
  <div id="app"></div>

  <!-- Editor bootstrap config — injected server-side -->
  <script>
  window.__EDITOR_CONFIG__ = {{
    cmsBase: "",
    apiKey: {_js_string(editor.cms.api_key)},
    mountPath: {_js_string(mount)},
    mediaBase: {_js_string(editor.media_base)},
    actions: {_js_json(editor.actions)}
  }};
  </script>

  <script src="{mount}/static/editor.js?v={_JS_HASH}"></script>
</body>
</html>"""
        return HTMLResponse(html)

    async def embed_js_endpoint(request: Request) -> Response:
        """Serve the embed script for live editing on static sites.

        No auth required — the script itself is public. It checks
        authentication at runtime via /api/auth/me and exits silently
        for unauthenticated visitors.
        """
        embed_path = STATIC_DIR / "embed.js"
        if not embed_path.exists():
            return Response("embed.js not built", status_code=404)
        content = embed_path.read_bytes()
        return Response(
            content,
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    routes: list = [
        # /editor/ is the editor; /shell stays for bookmarks.
        Route("/", endpoint=shell_endpoint, methods=["GET"]),
        Route("/shell", endpoint=shell_endpoint, methods=["GET"]),
        Route("/embed.js", endpoint=embed_js_endpoint, methods=["GET"]),
    ]

    if STATIC_DIR.exists():
        routes.append(Mount("/static", app=StaticFiles(directory=str(STATIC_DIR))))

    return routes


def _js_json(value) -> str:
    """Encode a Python value as a JSON literal safe to inline in a <script>."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")  # prevent </script> injection
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _js_string(value: str | None) -> str:
    """Safely encode a Python string value for inline JavaScript."""
    if value is None:
        return "null"
    # Escape characters that would break the JS string literal
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("<", "\\u003c")  # prevent </script> injection
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f'"{escaped}"'
