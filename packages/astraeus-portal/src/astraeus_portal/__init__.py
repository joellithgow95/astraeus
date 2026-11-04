"""
astraeus-portal — Central navigation hub for Astraeus.

Provides a mountable Starlette sub-application that serves a portal/homepage
linking to all admin interfaces in the Astraeus stack (editor, mediakit,
gateways, feedback, and any custom apps).

Quickstart::

    from astraeus_portal import Portal, PortalApp

    portal = Portal(apps=[
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
    ])

    app = Starlette(routes=[Mount("/", app=portal.app)])

Compose shortcut — wire everything together::

    from astraeus_portal import compose_app

    app = compose_app(
        cms=cms, editor=editor, media=media,
        chat=chat, gateways=gateway_admin,
    )
    # Portal auto-populated at /
"""

from __future__ import annotations

import logging as _logging

from astraeus_portal.app import Portal, PortalApp

# Library contract: install NullHandler so the host app controls log routing.
_logging.getLogger("astraeus_portal").addHandler(_logging.NullHandler())

__version__ = "0.1.0"

__all__ = ["Portal", "PortalApp"]


def __getattr__(name: str):
    if name == "compose_app":
        from astraeus_portal.compose import compose_app as _compose_app

        return _compose_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
