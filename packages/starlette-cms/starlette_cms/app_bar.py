"""The ``<script>`` tag that puts the portal's app bar on an admin page.

The bar itself (``app-bar.js``) is served by astraeus-portal, which owns the
list of apps. This only builds the tag, so the editor, the gateway admin and
the feedback admin load it the same way without depending on the portal::

    html = f"<head>...{app_bar_tag(admin.portal_url)}</head>"
"""

from __future__ import annotations

from html import escape


def app_bar_tag(portal_url: str | None) -> str:
    """Return the bar's script tag for a portal mounted at *portal_url*, or ``""``.

    ``"/"`` and ``""`` both mean the site root.
    """
    if portal_url is None:
        return ""
    src = f"{portal_url.rstrip('/')}/app-bar.js"
    return f'\n  <script src="{escape(src, quote=True)}" defer></script>'
