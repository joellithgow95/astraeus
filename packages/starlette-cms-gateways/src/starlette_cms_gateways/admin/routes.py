"""
Gateway admin shell routes — ``GET /shell`` and ``/static/*``.

Mirrors the structure of ``starlette_editor.routes`` — a single HTML shell page
with an inlined bootstrap config object and a small vanilla-JS frontend that
calls the gateway API routes registered on the CMS.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette_cms.app_bar import app_bar_tag as _app_bar_tag

if TYPE_CHECKING:
    from starlette_cms_gateways.admin.app import GatewayAdmin

STATIC_DIR = pathlib.Path(__file__).parent / "static"


def _js_string(value: str | None) -> str:
    """Safely encode a Python string value for inline JavaScript."""
    if value is None:
        return "null"
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f'"{escaped}"'


def make_admin_routes(admin: GatewayAdmin) -> list:
    """
    Return the routes for the gateway admin sub-application.

    Routes:
      GET /shell     — HTML shell page (injects JS config, renders gateway UI)
      /static/*      — Static file serving (if ``static/`` directory exists)
    """

    async def shell_endpoint(request: Request) -> HTMLResponse:
        """Serve the gateway admin single-page shell."""
        if admin.auth is not None:
            allowed = admin.auth(request)
            if hasattr(allowed, "__await__"):
                allowed = await allowed
            if not allowed:
                # Redirect unauthenticated visitors to the login page, sending
                # them back to the shell (?next=) once they have signed in.
                return RedirectResponse(
                    f"{admin.login_path}?next={request.url.path}",
                    status_code=302,
                )

        mount = admin.mount_path.rstrip("/")
        cms_base = admin.cms.mount_path.rstrip("/")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Gateway Admin</title>
  <link rel="stylesheet" href="{cms_base}/static/tokens.css" />
  <script src="{cms_base}/static/theme.js"></script>{_app_bar_tag(admin.portal_url)}
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: var(--font-sans);
      background: var(--bg-base);
      color: var(--text-primary);
      padding: var(--space-8);
      line-height: 1.5;
    }}
    h1 {{
      font-size: var(--font-size-xl);
      font-weight: 600;
      margin-bottom: var(--space-6);
      color: var(--text-primary);
    }}
    h2 {{
      font-size: var(--font-size-lg);
      font-weight: 600;
      margin-bottom: var(--space-3);
      color: var(--text-secondary);
    }}
    #gateways {{ display: grid; gap: var(--space-4); }}
    .gateway-card {{
      background: var(--bg-elevated);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-lg);
      padding: var(--space-5);
    }}
    .gateway-name {{
      font-size: var(--font-size-lg);
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: var(--space-1);
    }}
    .gateway-meta {{
      font-size: var(--font-size-sm);
      color: var(--text-secondary);
      margin-bottom: var(--space-2);
    }}
    .gateway-meta span {{ margin-right: var(--space-4); }}
    .last-synced {{
      font-size: var(--font-size-sm);
      color: var(--text-muted);
      margin-bottom: var(--space-3);
    }}
    .sync-btn {{
      background: var(--accent);
      color: var(--accent-on);
      border: none;
      border-radius: var(--radius-md);
      min-height: var(--target-min);
      padding: 0 var(--space-4);
      font-size: var(--font-size-md);
      cursor: pointer;
      transition: background var(--transition-fast);
    }}
    .sync-btn:hover {{ background: var(--accent-hover); }}
    .sync-btn:disabled {{
      background: var(--bg-hover);
      color: var(--text-muted);
      cursor: not-allowed;
    }}
    .job-status {{
      margin-top: var(--space-3);
      font-size: var(--font-size-md);
      min-height: 1.2em;
    }}
    /* A sync in flight is work outstanding, so it carries the one hue.
       A finished sync is the settled state and goes quiet. */
    .status-running {{ color: var(--pending); }}
    .status-done {{ color: var(--text-secondary); }}
    .status-error {{ color: var(--pending); }}
    .result-detail {{
      margin-top: var(--space-2);
      font-size: var(--font-size-sm);
      color: var(--text-secondary);
      font-family: var(--font-mono);
    }}
    #loading {{ color: var(--text-secondary); }}
    #error-msg {{ color: var(--pending); }}
  </style>
</head>
<body>
  <h1>Gateway Admin</h1>
  <div id="loading">Loading gateways…</div>
  <div id="error-msg" hidden></div>
  <div id="gateways" hidden></div>

  <script>
  (function () {{
    var CONFIG = {{
      cmsBase: {_js_string(cms_base)},
      mountPath: {_js_string(mount)}
    }};

    // API calls authenticate via the same-origin `cms_session` cookie (sent
    // automatically). No API key is embedded in this page.
    function authHeaders() {{
      return {{ "Content-Type": "application/json" }};
    }}

    async function apiFetch(path, opts) {{
      var resp = await fetch(CONFIG.cmsBase + path, Object.assign({{
        headers: authHeaders(),
        credentials: "same-origin"
      }}, opts || {{}}));
      if (!resp.ok) throw new Error(resp.status + " " + resp.statusText);
      return resp.json();
    }}

    function renderMeta(gw) {{
      return [
        '<span>service: <b>' + esc(gw.service_name || '?') + '</b></span>',
        '<span>block: <b>' + esc(gw.block_type || '?') + '</b></span>',
        '<span>auto_publish: <b>' + gw.auto_publish + '</b></span>',
      ].join('');
    }}

    function esc(s) {{
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    async function pollJob(gwName, runId, statusEl) {{
      var done = false;
      while (!done) {{
        await new Promise(function(r) {{ setTimeout(r, 1000); }});
        try {{
          var url = '/api/gateways/' + encodeURIComponent(gwName);
          url += '/sync/' + encodeURIComponent(runId);
          var job = await apiFetch(url);
          if (job.status === 'running') {{
            statusEl.textContent = '⏳ Running…';
            statusEl.className = 'job-status status-running';
          }} else if (job.status === 'done') {{
            var r = job.result || {{}};
            statusEl.className = 'job-status status-done';
            statusEl.innerHTML =
              '✓ Done &nbsp;' +
              '<span class="result-detail">' +
              'created=' + r.created + '  updated=' + r.updated +
              '  skipped=' + r.skipped + '  errors=' + (r.errors ? r.errors.length : 0) +
              '</span>';
            // Update last-synced display live without a page reload
            var lsEl = document.getElementById('lastsynced-' + CSS.escape(gwName));
            if (lsEl) {{ lsEl.innerHTML = 'Last synced: ' + esc(new Date().toISOString()); }}
            done = true;
          }} else {{
            statusEl.className = 'job-status status-error';
            statusEl.textContent = '✗ Error: ' + esc(job.error || 'unknown');
            done = true;
          }}
        }} catch (e) {{
          statusEl.className = 'job-status status-error';
          statusEl.textContent = '✗ Poll failed: ' + esc(e.message);
          done = true;
        }}
      }}
    }}

    function buildCard(gw) {{
      var lastSyncedVal = gw.last_synced
        ? esc(gw.last_synced)
        : '<em>Never</em>';
      var card = document.createElement('div');
      card.className = 'gateway-card';
      card.innerHTML =
        '<div class="gateway-name">' + esc(gw.name) + '</div>' +
        '<div class="gateway-meta">' + renderMeta(gw) + '</div>' +
        '<div class="last-synced" id="lastsynced-' + esc(gw.name) + '">' +
        'Last synced: ' + lastSyncedVal + '</div>' +
        // TODO: relative time ("2 hours ago") — future enhancement
        '<button class="sync-btn" id="btn-' + esc(gw.name) + '">▶ Sync now</button>' +
        '<div class="job-status" id="status-' + esc(gw.name) + '"></div>';

      var btn = card.querySelector('#btn-' + CSS.escape(gw.name));
      var statusEl = card.querySelector('#status-' + CSS.escape(gw.name));

      btn.addEventListener('click', async function () {{
        btn.disabled = true;
        statusEl.textContent = '⏳ Starting…';
        statusEl.className = 'job-status status-running';
        try {{
          var resp = await apiFetch('/api/gateways/' + encodeURIComponent(gw.name) + '/sync', {{
            method: 'POST'
          }});
          await pollJob(gw.name, resp.run_id, statusEl);
        }} catch (e) {{
          statusEl.className = 'job-status status-error';
          statusEl.textContent = '✗ ' + esc(e.message);
        }} finally {{
          btn.disabled = false;
        }}
      }});

      return card;
    }}

    async function init() {{
      var loadingEl = document.getElementById('loading');
      var errorEl = document.getElementById('error-msg');
      var gwEl = document.getElementById('gateways');

      try {{
        var data = await apiFetch('/api/gateways');
        loadingEl.hidden = true;

        if (!data.gateways || data.gateways.length === 0) {{
          gwEl.hidden = false;
          var epHint = '"starlette_cms_gateways.gateways"';
          gwEl.innerHTML =
            '<p style="color:#888">No gateways installed. ' +
            'Register one via <code>project.entry-points.' + epHint + '</code>.</p>';
          return;
        }}

        gwEl.hidden = false;
        data.gateways.forEach(function (gw) {{
          gwEl.appendChild(buildCard(gw));
        }});
      }} catch (e) {{
        loadingEl.hidden = true;
        errorEl.hidden = false;
        errorEl.textContent = 'Failed to load gateways: ' + e.message;
      }}
    }}

    init();
  }})();
  </script>
</body>
</html>"""
        return HTMLResponse(html)

    routes: list = [
        Route("/", endpoint=shell_endpoint, methods=["GET"]),
        Route("/shell", endpoint=shell_endpoint, methods=["GET"]),
    ]

    if STATIC_DIR.exists():
        routes.append(Mount("/static", app=StaticFiles(directory=str(STATIC_DIR))))

    return routes
