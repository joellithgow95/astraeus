"""
Portal routes — ``GET /`` (portal shell page) and ``/static/*``.

The shell is a server-rendered HTML page with an inline dark-themed design
that shows all registered apps as a grid of clickable cards.
"""

from __future__ import annotations

import html
import importlib.metadata
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from astraeus_portal.app import Portal


_KNOWN_PACKAGES = [
    "starlette-cms",
    "starlette-editor",
    "starlette-cms-gateways",
    "mediakit",
    "starlette-chat",
    "astraeus-portal",
    "astraeus-otel",
]


def _gather_package_info() -> list[dict[str, str]]:
    """Try to import known astraeus packages and return their versions."""
    results: list[dict[str, str]] = []
    for name in _KNOWN_PACKAGES:
        normalized = name.replace("-", "_")
        try:
            ver = importlib.metadata.version(normalized)
            results.append({"name": name, "version": ver})
        except importlib.metadata.PackageNotFoundError:
            try:
                ver = importlib.metadata.version(name)
                results.append({"name": name, "version": ver})
            except importlib.metadata.PackageNotFoundError:
                pass
    return results


def _build_shell_html(portal: Portal) -> str:
    """Build the portal shell HTML page."""
    apps = portal.apps

    # Card HTML rows
    cards_html = ""
    for i, app in enumerate(apps):
        icon_html = f'<span class="card-icon">{html.escape(app.icon)}</span>' if app.icon else ""
        tags_html = ""
        if app.tags:
            tags_html = (
                '<div class="card-tags">'
                + "".join(f'<span class="tag">{tag}</span>' for tag in app.tags)
                + "</div>"
            )
        cards_html += f"""
        <a href="{app.path}" class="card" id="card-{i}">
          {icon_html}
          <div class="card-body">
            <div class="card-title">{app.name}</div>
            <div class="card-desc">{app.description}</div>
            {tags_html}
          </div>
          <div class="card-arrow">→</div>
        </a>"""

    # Package info
    pkg_rows = ""
    if portal.show_package_info:
        pkgs = _gather_package_info()
        if pkgs:
            pkg_rows = (
                            '<div class="pkg-section">\n'
                            "<h2>Installed Packages</h2>\n"
                            '<div class="pkg-grid">\n'
                        )
            for pkg in pkgs:
                pkg_rows += (
                    f'<div class="pkg-item">\n'
                    f'  <span class="pkg-name">{pkg["name"]}</span>\n'
                    f'  <span class="pkg-version">{pkg["version"]}</span>\n'
                    f"</div>\n"
                )
            pkg_rows += "</div>\n</div>"

    # Header links
    header_items = ""
    for label, url in portal.header_links.items():
        header_items += f'<a href="{url}" class="header-link">{label}</a>'

    footer_html = ""
    footer_parts = []
    if portal.docs_url:
        footer_parts.append(f'<a href="{portal.docs_url}" class="footer-link">📄 Docs</a>')
    if portal.github_url:
        footer_parts.append(f'<a href="{portal.github_url}" class="footer-link">🐙 GitHub</a>')
    pkg_name = importlib.metadata.version("astraeus_portal")
    footer_parts.append(
        f'<span class="footer-version">astraeus-portal {pkg_name}</span>'
    )
    if footer_parts:
        footer_html = '<div class="footer">' + "  ·  ".join(footer_parts) + "</div>"

    # Empty state
    if not apps:
        cards_html = """
        <div class="empty-state">
          <div class="empty-icon">🏗️</div>
          <div class="empty-title">No apps registered</div>
          <div class="empty-desc">
            Pass a list of <code>PortalApp</code> entries when creating the portal,
            or use <code>compose_app()</code> to auto-populate from your components.
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{portal.title}</title>
  <link rel="stylesheet" href="{portal.cms_base}/static/tokens.css" />
  <script src="{portal.cms_base}/static/theme.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: var(--font-sans);
      background: var(--bg-base);
      color: var(--text-primary);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}
    a {{ color: inherit; text-decoration: none; }}

    /* ── Header ──────────────────────────────────── */
    .header {{
      padding: 2rem 2rem 1rem;
      max-width: 900px;
      margin: 0 auto;
      width: 100%;
    }}
    .header-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.75rem;
    }}
    .header-title {{
      font-size: 1.65rem;
      font-weight: 700;
      color: var(--text-primary);
      letter-spacing: -0.02em;
    }}
    .header-title-sub {{
      font-size: 0.85rem;
      color: var(--text-secondary);
      font-weight: 400;
      margin-left: 0.5rem;
    }}
    .header-links {{
      display: flex;
      gap: 1rem;
    }}
    .header-link {{
      font-size: 0.85rem;
      color: var(--text-secondary);
      transition: color 0.15s;
    }}
    .header-link:hover {{ color: var(--text-primary); }}
    .header-subtitle {{
      font-size: 0.95rem;
      color: var(--text-secondary);
      margin-top: 0.35rem;
      max-width: 500px;
    }}

    /* ── Card Grid ────────────────────────────────── */
    .main {{
      flex: 1;
      padding: 1rem 2rem 2rem;
      max-width: 900px;
      margin: 0 auto;
      width: 100%;
    }}
    .card-grid {{
      display: grid;
      gap: 0.75rem;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    }}
    .card {{
      display: flex;
      align-items: center;
      gap: 0.85rem;
      background: var(--bg-elevated);
      border: 1px solid var(--border-default);
      border-radius: 10px;
      padding: 1.15rem 1.15rem 1.15rem 1rem;
      transition: background 0.15s, border-color 0.15s, transform 0.1s;
      cursor: pointer;
    }}
    .card:hover {{
      background: var(--bg-hover);
      border-color: var(--border-strong);
      transform: translateY(-1px);
    }}
    .card-icon {{
      font-size: 1.6rem;
      width: 2.2rem;
      text-align: center;
      flex-shrink: 0;
    }}
    .card-body {{
      flex: 1;
      min-width: 0;
    }}
    .card-title {{
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: 0.2rem;
    }}
    .card-desc {{
      font-size: 0.82rem;
      color: var(--text-secondary);
      line-height: 1.35;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .card-tags {{
      display: flex;
      gap: 0.35rem;
      margin-top: 0.4rem;
      flex-wrap: wrap;
    }}
    .tag {{
      font-size: 0.7rem;
      background: var(--bg-hover);
      color: var(--text-secondary);
      padding: 0.1rem 0.5rem;
      border-radius: 3px;
      font-weight: 500;
    }}
    .card-arrow {{
      font-size: 1.1rem;
      color: var(--text-muted);
      transition: color 0.15s, transform 0.15s;
      flex-shrink: 0;
    }}
    .card:hover .card-arrow {{
      color: var(--text-primary);
      transform: translateX(2px);
    }}

    /* ── Section headings ────────────────────────── */
    section h2 {{
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-secondary);
      margin: 2rem 0 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}

    /* ── Package info ─────────────────────────────── */
    .pkg-grid {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }}
    .pkg-item {{
      background: var(--bg-surface);
      border: 1px solid var(--bg-hover);
      border-radius: 6px;
      padding: 0.4rem 0.75rem;
      display: flex;
      align-items: center;
      gap: 0.6rem;
      font-size: 0.8rem;
    }}
    .pkg-name {{
      color: var(--text-secondary);
    }}
    .pkg-version {{
      color: var(--text-secondary);
      font-family: "SFMono-Regular", Consolas, monospace;
    }}

    /* ── Empty state ──────────────────────────────── */
    .empty-state {{
      text-align: center;
      padding: 4rem 1rem;
    }}
    .empty-icon {{ font-size: 2.5rem; margin-bottom: 0.75rem; }}
    .empty-title {{
      font-size: 1.2rem;
      font-weight: 600;
      color: var(--text-secondary);
      margin-bottom: 0.5rem;
    }}
    .empty-desc {{
      font-size: 0.9rem;
      color: var(--text-secondary);
      max-width: 400px;
      margin: 0 auto;
    }}
    .empty-desc code {{
      background: var(--bg-hover); padding: 0.1rem 0.35rem;
      border-radius: 3px; font-size: 0.82rem;
    }}

    /* ── Footer ───────────────────────────────────── */
    .footer {{
      padding: 1.5rem 2rem;
      text-align: center;
      font-size: 0.78rem;
      color: var(--text-muted);
    }}
    .footer-link {{
      color: var(--text-secondary);
      transition: color 0.15s;
    }}
    .footer-link:hover {{ color: var(--text-primary); }}
    .footer-version {{ color: var(--text-muted); }}

    /* ── Responsive ───────────────────────────────── */
    @media (max-width: 640px) {{
      .header {{ padding: 1.25rem 1rem 0.5rem; }}
      .main {{ padding: 0.5rem 1rem 1.5rem; }}
      .card-grid {{ grid-template-columns: 1fr; }}
      .header-top {{ flex-direction: column; align-items: flex-start; }}
    }}
  </style>
</head>
<body>
  <header class="header">
    <div class="header-top">
      <div>
        <span class="header-title">{portal.title}</span>
        <span class="header-title-sub">Portal</span>
      </div>
      <div class="header-links">
        {header_items}
      </div>
    </div>
    <div class="header-subtitle">
      Admin dashboard and navigation hub for the Astraeus content stack
    </div>
  </header>

  <main class="main">
    <section>
      <h2>Applications</h2>
      <div class="card-grid" id="app-grid">
        {cards_html}
      </div>
    </section>

    {pkg_rows}
  </main>

  {footer_html}

  <script>
    (function () {{
      // Keyboard navigation — number keys jump to cards
      var cards = document.querySelectorAll('.card');
      document.addEventListener('keydown', function (e) {{
        if (e.altKey || e.ctrlKey || e.metaKey) return;
        var n = parseInt(e.key, 10);
        if (n >= 1 && n <= cards.length) {{
          e.preventDefault();
          cards[n - 1].click();
        }}
      }});
    }})();
  </script>
</body>
</html>"""


def make_portal_routes(portal: Portal) -> list:
    """Return the routes for the portal sub-application.

    Routes:
      GET /  — HTML shell page
    """

    async def shell_endpoint(request: Request) -> HTMLResponse:
        """Serve the portal shell page."""
        if portal.auth is not None:
            allowed = portal.auth(request)
            if hasattr(allowed, "__await__"):
                allowed = await allowed
            if not allowed:
                return RedirectResponse(
                    f"{portal.login_path}?next={request.url.path}",
                    status_code=302,
                )

        html = _build_shell_html(portal)
        return HTMLResponse(html)

    return [
        Route("/", endpoint=shell_endpoint, methods=["GET"]),
    ]
