"""
Admin UI routes for astraeus-media.

Provides a browser-based interface for uploading and browsing assets.

Requires the ``[admin]`` optional dependency (jinja2 + python-multipart)::

    pip install astraeus-media[admin]

Usage — the routes are wired automatically in ``MediaKit._build_app`` when
jinja2 is installed.  Mount the parent app at a path prefix::

    app = Starlette(routes=[Mount("/media", app=mk.app)])

Then visit ``/media/admin`` in a browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from astraeus_media.routes.iiif import ADMIN_DETAIL, ADMIN_THUMBNAIL

if TYPE_CHECKING:
    from astraeus_media.app import MediaKit


# Static files directory is one level up from this file (astraeus_media/static/)
STATIC_DIR = Path(__file__).parent.parent / "static"


def make_admin_routes(mk: MediaKit) -> list[Route | Mount]:
    """Return admin UI routes for *mk*.

    Requires jinja2 to be installed (``astraeus-media[admin]``).  Raises
    ``ImportError`` if jinja2 is absent — the caller in ``app.py``
    catches that and silently skips the admin routes.

    :param mk: The :class:`~astraeus_media.app.MediaKit` instance.
    """
    from jinja2 import Environment, PackageLoader, select_autoescape

    env = Environment(
        loader=PackageLoader("astraeus_media.admin", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    # Available to every template without threading it through each render().
    env.globals["tokens_url"] = mk.config.tokens_url
    portal_url = mk.config.portal_url
    env.globals["app_bar_url"] = (
        f"{portal_url.rstrip('/')}/app-bar.js" if portal_url is not None else None
    )

    def base_of(request: Request) -> str:
        """Where astraeus-media is mounted (``""`` at the root, ``"/media"`` under a host).

        Starlette's ``Mount`` puts the prefix in ``root_path``, so every link the
        admin renders is built from it rather than assuming the root.
        """
        return request.scope.get("root_path", "").rstrip("/")

    def rendition_url(base: str, key: str, region: str, size: str) -> str:
        """Return the IIIF URL for *key* at *region* and *size* (webp, unrotated)."""
        return f"{base}/iiif/{key}/{region}/{size}/0/default.webp"

    async def guard(request: Request):
        """Admin pages list the whole library and expose upload and delete, so they
        take the same auth as the write endpoints. Returns a 401, a redirect to
        ``login_url`` when one is set, or ``None``."""
        from astraeus_media.auth import check_auth, require_auth

        login_url = mk.config.login_url
        if login_url and not await check_auth(request, mk):
            target = request.url.path
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(f"{login_url}?{urlencode({'next': target})}", status_code=302)
        return await require_auth(request, mk)

    # ------------------------------------------------------------------
    # Asset browser — GET /admin
    # ------------------------------------------------------------------

    async def browser(request: Request) -> Response:
        """``GET /admin`` — responsive grid of asset thumbnails."""
        if (err := await guard(request)) is not None:
            return err
        base = base_of(request)
        params = request.query_params
        try:
            limit = int(params.get("limit", 50))
            offset = int(params.get("offset", 0))
        except ValueError:
            limit, offset = 50, 0

        content_type = params.get("content_type") or None
        tags_param = params.get("tags") or None
        tags = [t.strip() for t in tags_param.split(",")] if tags_param else None
        picker = params.get("picker", "0") == "1"

        assets = await mk.catalog.list_assets(
            limit=limit,
            offset=offset,
            content_type=content_type,
            tags=tags,
        )

        template = env.get_template("browser.html")
        html = template.render(
            assets=assets,
            limit=limit,
            offset=offset,
            content_type_filter=content_type or "",
            tags_filter=tags_param or "",
            picker=picker,
            base=base,
            thumbnail_url=lambda key: rendition_url(base, key, *ADMIN_THUMBNAIL),
            detail_url=lambda key: rendition_url(base, key, *ADMIN_DETAIL),
            prev_offset=max(0, offset - limit),
            next_offset=offset + limit,
            has_prev=offset > 0,
            has_next=len(assets) == limit,
        )
        return HTMLResponse(html)

    # ------------------------------------------------------------------
    # Upload page — GET /admin/upload
    # ------------------------------------------------------------------

    async def upload_page(request: Request) -> Response:
        """``GET /admin/upload`` — drag-and-drop upload form."""
        if (err := await guard(request)) is not None:
            return err
        picker = request.query_params.get("picker", "0") == "1"
        template = env.get_template("upload.html")
        html = template.render(picker=picker, base=base_of(request))
        return HTMLResponse(html)

    # ------------------------------------------------------------------
    # Asset detail — GET /admin/assets/{key:path}
    # ------------------------------------------------------------------

    async def asset_detail(request: Request) -> Response:
        """``GET /admin/assets/{key:path}`` — asset detail view."""
        if (err := await guard(request)) is not None:
            return err
        base = base_of(request)
        key = request.path_params["key"]
        asset = await mk.catalog.get_asset(key)
        if asset is None:
            template = env.get_template("base.html")
            html = template.render(title="Not Found", content="<p>Asset not found.</p>", base=base)
            return HTMLResponse(html, status_code=404)

        picker = request.query_params.get("picker", "0") == "1"
        template = env.get_template("detail.html")
        html = template.render(
            asset=asset,
            picker=picker,
            base=base,
            thumbnail_url=lambda key: rendition_url(base, key, *ADMIN_THUMBNAIL),
            detail_url=lambda key: rendition_url(base, key, *ADMIN_DETAIL),
        )
        return HTMLResponse(html)

    # ------------------------------------------------------------------
    # Metadata update — POST /admin/assets/{key:path}
    # ------------------------------------------------------------------

    async def update_asset(request: Request) -> Response:
        """``POST /admin/assets/{key:path}`` — handle metadata form submit."""
        from astraeus_media.auth import require_auth

        if (err := await require_auth(request, mk)) is not None:
            return err

        key = request.path_params["key"]

        form = await request.form()
        _alt_text_raw = form.get("alt_text")
        alt_text: str | None = str(_alt_text_raw) if isinstance(_alt_text_raw, str) and _alt_text_raw else None
        _tags_raw = form.get("tags")
        tags_raw: str = str(_tags_raw) if isinstance(_tags_raw, str) else ""
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

        await mk.catalog.update_asset(key, alt_text=alt_text, tags=tags)

        return RedirectResponse(f"{base_of(request)}/admin/assets/{key}", status_code=303)

    # ------------------------------------------------------------------
    # Route list
    # ------------------------------------------------------------------

    return [
        Route("/admin", endpoint=browser, methods=["GET"]),
        Route("/admin/upload", endpoint=upload_page, methods=["GET"]),
        Route("/admin/assets/{key:path}", endpoint=asset_detail, methods=["GET"]),
        Route("/admin/assets/{key:path}", endpoint=update_asset, methods=["POST"]),
        Mount("/admin/static", app=StaticFiles(directory=str(STATIC_DIR)), name="admin-static"),
    ]
