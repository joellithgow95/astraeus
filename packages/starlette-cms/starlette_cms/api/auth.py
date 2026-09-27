"""Session auth endpoints — /api/auth/login, /api/auth/logout, /api/auth/me"""

from __future__ import annotations

from typing import TYPE_CHECKING

import bcrypt
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from starlette_cms.session import generate_session_token, validate_session_token

if TYPE_CHECKING:
    from starlette_cms.app import CMS

_SESSION_COOKIE = "cms_session"
_COOKIE_MAX_AGE = 86400  # 24 hours


def _login_html(error: bool = False, next_url: str = "", mount: str = "") -> str:
    error_block = (
        '<p class="error">Invalid username or password.</p>' if error else ""
    )
    next_field = (
        f'<input type="hidden" name="next" value="{next_url}">' if next_url else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CMS Login</title>
  <link rel="stylesheet" href="{mount}/static/tokens.css">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg-base);
      color: var(--text-primary);
      font-family: var(--font-sans);
      display: flex;
      align-items: center;
      justify-content: center;
      /* 100vh excludes the iOS URL bar. */
      min-height: 100dvh;
      padding: var(--space-4);
    }}
    .card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-lg);
      padding: var(--space-8);
      width: 100%;
      max-width: 360px;
    }}
    h1 {{
      font-size: var(--font-size-xl);
      font-weight: 600;
      margin-bottom: var(--space-6);
      color: var(--text-primary);
    }}
    label {{
      display: block;
      font-size: var(--font-size-md);
      font-weight: 500;
      color: var(--text-secondary);
      margin-bottom: var(--space-1);
    }}
    input[type="text"], input[type="password"] {{
      width: 100%;
      min-height: var(--target-min);
      padding: var(--space-2) var(--space-3);
      background: var(--bg-input);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-sm);
      color: var(--text-primary);
      /* iOS zooms the page when focusing anything below 16px. */
      font-size: var(--font-size-input);
      margin-bottom: var(--space-4);
      outline: none;
      transition: border-color var(--transition-fast);
    }}
    input[type="text"]:focus, input[type="password"]:focus {{
      border-color: var(--border-focus);
    }}
    button {{
      width: 100%;
      min-height: var(--target-min);
      padding: var(--space-2);
      background: var(--accent);
      color: var(--accent-on);
      border: none;
      border-radius: var(--radius-sm);
      font-family: var(--font-sans);
      font-size: var(--font-size-input);
      font-weight: 500;
      cursor: pointer;
      transition: background var(--transition-fast);
    }}
    button:hover {{ background: var(--accent-hover); }}
    .error {{
      color: var(--pending);
      font-size: var(--font-size-md);
      margin-bottom: var(--space-4);
      padding: var(--space-2) var(--space-3);
      background: var(--pending-dim);
      border: 1px solid var(--pending);
      border-radius: var(--radius-sm);
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>CMS Login</h1>
    {error_block}
    <form method="POST">
      {next_field}
      <label for="username">Username</label>
      <input type="text" id="username" name="username" autocomplete="username" required>
      <label for="password">Password</label>
      <input type="password" id="password" name="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form>
  </div>
</body>
</html>"""


def make_auth_routes(cms: CMS) -> list[Route]:
    """Build and return the session auth routes, closed over ``cms``."""

    async def login_get(request: Request) -> HTMLResponse:
        error = request.query_params.get("error") == "1"
        next_url = request.query_params.get("next", "")
        mount = cms.mount_path.rstrip("/")
        return HTMLResponse(_login_html(error=error, next_url=next_url, mount=mount))

    async def login_post(request: Request) -> Response:
        if cms.session_secret is None:
            return Response(
                "CMS_SESSION_SECRET not configured",
                status_code=500,
                media_type="text/plain",
            )

        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")
        next_url = form.get("next", "")

        # Build redirect-to-error URL preserving next param
        error_url = "/api/auth/login?error=1"
        if next_url:
            error_url += f"&next={next_url}"

        admin_users = cms.admin_users or {}
        stored_hash = admin_users.get(username)
        if stored_hash is None:
            return RedirectResponse(error_url, status_code=302)

        if not bcrypt.checkpw(password.encode(), stored_hash.encode()):
            return RedirectResponse(error_url, status_code=302)

        # Valid credentials — issue session cookie.
        # SameSite=Lax works everywhere this needs to cross an origin boundary:
        # prod's joellithgow.com/cms.joellithgow.com are same-site subdomains,
        # and local dev's localhost:4322/localhost:8001 are same-site too — the
        # "site" comparison ignores port, only scheme+hostname matter, and both
        # are plain "localhost". SameSite=None (the previous local-dev choice)
        # requires Secure, which browsers enforce unconditionally regardless of
        # whether the traffic is actually cross-site — and Secure cookies are
        # never sent back over plain http, so that combination never actually
        # worked; it silently dropped the cookie in real browsers.
        is_localhost = request.url.hostname in ("localhost", "127.0.0.1", "::1")
        secure_flag = "" if is_localhost else "Secure; "
        token = generate_session_token(username, cms.session_secret)
        cookie = (
            f"{_SESSION_COOKIE}={token}; "
            f"HttpOnly; {secure_flag}SameSite=Lax; "
            f"Max-Age={_COOKIE_MAX_AGE}; Path=/"
        )

        # Validate next URL — allow absolute URLs to localhost (dev Astro server)
        # and any path-only URL starting with "/".
        if next_url and (
            next_url.startswith("/")
            or (is_localhost and next_url.startswith("http://localhost"))
        ):
            redirect_to = next_url
        else:
            redirect_to = "/"

        response = RedirectResponse(redirect_to, status_code=302)
        response.headers["Set-Cookie"] = cookie
        return response

    async def logout(request: Request) -> Response:
        is_localhost = request.url.hostname in ("localhost", "127.0.0.1", "::1")
        secure_flag = "" if is_localhost else "Secure; "
        clear_cookie = (
            f"{_SESSION_COOKIE}=; "
            f"HttpOnly; {secure_flag}SameSite=Lax; "
            f"Max-Age=0; Path=/"
        )
        response = RedirectResponse("/api/auth/login", status_code=302)
        response.headers["Set-Cookie"] = clear_cookie
        return response

    async def me(request: Request) -> JSONResponse:
        token = request.cookies.get(_SESSION_COOKIE)
        result: dict[str, object] = {"authenticated": False}

        if token and cms.session_secret:
            user_id = validate_session_token(token, cms.session_secret)
            if user_id:
                result = {"authenticated": True, "user": user_id}

        response = JSONResponse(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    return [
        Route("/api/auth/login", endpoint=login_get, methods=["GET"]),
        Route("/api/auth/login", endpoint=login_post, methods=["POST"]),
        Route("/api/auth/logout", endpoint=logout, methods=["POST"]),
        Route("/api/auth/me", endpoint=me, methods=["GET"]),
    ]
