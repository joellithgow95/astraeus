"""
FeedbackAdmin — mountable Starlette sub-application for the feedback admin UI.

Extends starlette-cms at init time by registering four API routes on the CMS
via :meth:`~starlette_cms.app.CMS.register_extension_route`, then serves a
shell page from its own sub-application.

Reports are stored in a dedicated SQLite database, not as CMS documents — see
:mod:`starlette_cms_feedback.store` for why.

Usage::

    from starlette_cms import CMS
    from starlette_cms_feedback import FeedbackAdmin
    from starlette.applications import Starlette
    from starlette.routing import Mount

    cms = CMS(database_url="sqlite:///content.db", auth="apikey", api_key="secret")
    feedback = FeedbackAdmin(cms=cms, db_path="feedback.db")

    app = Starlette(
        routes=[
            Mount("/cms", app=cms.app),
            Mount("/feedback", app=feedback.app),
        ],
        lifespan=cms.lifespan,
    )

Shell:   GET   /feedback/shell
API:     GET   /cms/api/feedback
         POST  /cms/api/feedback
         GET   /cms/api/feedback/{id}
         PATCH /cms/api/feedback/{id}
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette

from starlette_cms_feedback.admin.routes import make_admin_routes
from starlette_cms_feedback.api import make_feedback_api_routes
from starlette_cms_feedback.models import DEFAULT_COMPONENTS
from starlette_cms_feedback.store import FeedbackStore

if TYPE_CHECKING:
    from starlette_cms.app import CMS

DEFAULT_DB = "feedback.db"


class FeedbackAdmin:
    """
    Mountable Starlette feedback admin sub-application.

    :param cms: The CMS instance to extend.  Four feedback API routes are
        registered on it at init time.  Must be called before the first
        access of ``cms.app``.
    :param components: Suggestions offered in the form's component field.
        Defaults to the Astraeus packages.
    :param db_path: Path to the SQLite database holding reports.  Defaults to
        ``feedback.db`` in the current working directory — use an absolute
        path in production.  Ignored if *store* is provided.
    :param login_path: Where unauthenticated visitors to the shell are sent.
    :param mount_path: The path this admin UI is mounted at.
    :param portal_url: Where the Astraeus portal is mounted (e.g. ``"/"``). Set, the
        shell loads the portal's app bar, which links home and to the other apps.
    :param auth: Optional ``(request) -> bool`` callable protecting the
        ``/shell`` HTML page.  The API routes always use the CMS auth model,
        so the page being open exposes no reports — but set this on a public
        server so visitors are sent to sign in rather than shown a dead form.
    :param store: A pre-built :class:`~starlette_cms_feedback.store.FeedbackStore`.
    """

    def __init__(
        self,
        *,
        cms: CMS,
        auth: Callable | None = None,
        components: list[str] | None = None,
        db_path: str | Path = DEFAULT_DB,
        login_path: str = "/api/auth/login",
        mount_path: str = "/feedback",
        portal_url: str | None = None,
        store: FeedbackStore | None = None,
    ) -> None:
        self.auth = auth
        self.cms = cms
        self.components = components if components is not None else list(DEFAULT_COMPONENTS)
        self.login_path = login_path
        self.mount_path = mount_path
        self.portal_url = portal_url
        self.store = store if store is not None else FeedbackStore(db_path)

        # Must happen before cms.app is accessed.
        for route in make_feedback_api_routes(self):
            cms.register_extension_route(
                path=route.path,
                endpoint=route.endpoint,
                methods=list(route.methods or []),
                name=route.name or route.path.replace("/", "_").strip("_"),
            )

        self._app: Starlette | None = None

    @property
    def app(self) -> Starlette:
        """Build and return the Starlette sub-application. Built once on first access."""
        if self._app is None:
            self._app = self._build_app()
        return self._app

    def _build_app(self) -> Starlette:
        return Starlette(routes=make_admin_routes(self))
