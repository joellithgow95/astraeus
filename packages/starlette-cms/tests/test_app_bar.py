"""app_bar_tag — the script tag admin pages use to load the portal's app bar."""

from __future__ import annotations

from starlette_cms.app_bar import app_bar_tag


def test_no_portal_no_tag() -> None:
    assert app_bar_tag(None) == ""


def test_root_and_prefixed_portals() -> None:
    assert app_bar_tag("/").strip() == '<script src="/app-bar.js" defer></script>'
    assert app_bar_tag("").strip() == '<script src="/app-bar.js" defer></script>'
    assert app_bar_tag("/admin/").strip() == '<script src="/admin/app-bar.js" defer></script>'


def test_escapes_the_url() -> None:
    assert '"' not in app_bar_tag('/x"><script>').split('src="', 1)[1].split('"', 1)[0]
