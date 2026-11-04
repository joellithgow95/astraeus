"""
GatewayAdmin — mountable Starlette sub-application for the gateway admin UI.

Extends starlette-cms at init time by registering the gateway API routes on the CMS
via :meth:`~starlette_cms.app.CMS.register_extension_route`, then serves a
shell page from its own sub-application.

Sync state (job records and the cursor) is stored in the CMS's own
SQLite file by default: the one file that is persistent and backed up. The tables
sit beside the CMS's and never appear in the block registry or editor UI.

Usage::

    from starlette_cms import CMS
    from starlette_cms_gateways.admin import GatewayAdmin
    from starlette.applications import Starlette
    from starlette.routing import Mount

    cms = CMS(database_url="sqlite:///content.db", auth="apikey", api_key="secret")
    admin = GatewayAdmin(cms=cms)

    app = Starlette(
        routes=[
            Mount("/cms", app=cms.app),
            Mount("/gateways", app=admin.app),
        ],
        lifespan=cms.lifespan,
    )

Shell:   GET  /gateways/shell
API:     GET  /cms/api/gateways
         GET  /cms/api/gateways/{name}
         POST /cms/api/gateways/{name}/sync
         GET  /cms/api/gateways/{name}/sync/{run_id}
         GET/PUT /cms/api/gateways/{name}/cursor     (sync state for outside workers)
         POST    /cms/api/gateways/{name}/runs
         PATCH   /cms/api/gateways/{name}/runs/{run_id}
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from starlette.applications import Starlette

from starlette_cms_gateways.jobstore import JobStore

if TYPE_CHECKING:
    from starlette_cms.app import CMS

FALLBACK_JOBS_DB = "gateway_jobs.db"


def sqlite_path(database_url: str) -> str | None:
    """
    The file behind a ``sqlite:///...`` URL, or ``None`` for anything else
    (Postgres, in-memory). Reads the URL the way ``starlette_cms.db`` does.
    """
    parsed = urlparse(database_url)
    if parsed.scheme.lower() != "sqlite":
        return None
    path = parsed.path
    if path.lstrip("/") == ":memory:" or not path:
        return None
    return path[1:] if path.startswith("//") else path


class GatewayAdmin:
    """
    Mountable Starlette gateway admin sub-application.

    :param cms: The CMS instance to extend.  The gateway API routes are
        registered on it via
        :meth:`~starlette_cms.app.CMS.register_extension_route` at init time.
        Must be called before the first access of ``cms.app``.
    :param mount_path: The path this admin UI is mounted at.  Defaults to
        ``"/gateways"``.
    :param auth: Optional auth callable ``(request) -> bool`` protecting the
        ``/shell`` HTML page.  API routes always use the CMS auth model
        (``Authorization: Bearer <api_key>``).  Defaults to ``None`` — set
        this when the admin is exposed on a public server.
    :param portal_url: Where the Astraeus portal is mounted (e.g. ``"/"``). Set, the
        shell loads the portal's app bar, which links home and to the other apps.
    :param jobs_db_path: SQLite file for sync state (job records and the cursor).
        Defaults to **the CMS's own database file**, so the state is
        as persistent and as backed up as the content (a Litestream replica of the
        CMS database carries it too).  When the CMS is not on a SQLite file
        (Postgres, in-memory) the default falls back to ``gateway_jobs.db`` in the
        working directory, with a warning: pass a path that outlives restarts.
        Ignored if *job_store* is provided.
    :param job_store: A pre-built :class:`~starlette_cms_gateways.jobstore.JobStore`
        instance.  Use this to share a job store across multiple components or
        to control the database path explicitly.  When provided, *jobs_db_path*
        is ignored (a warning is emitted if one was also passed).
    """

    def __init__(
        self,
        *,
        cms: CMS,
        mount_path: str = "/gateways",
        auth: Callable | None = None,
        login_path: str = "/api/auth/login",
        portal_url: str | None = None,
        jobs_db_path: str | Path | None = None,
        job_store: JobStore | None = None,
    ) -> None:
        self.cms = cms
        self.mount_path = mount_path
        self.auth = auth
        self.login_path = login_path
        self.portal_url = portal_url

        if job_store is not None:
            if jobs_db_path is not None:
                warnings.warn(
                    "jobs_db_path is ignored when job_store is provided",
                    stacklevel=2,
                )
            self.jobs = job_store
        else:
            if jobs_db_path is None:
                jobs_db_path = sqlite_path(cms.database_url)
                if jobs_db_path is None:
                    warnings.warn(
                        f"The CMS is not on a SQLite file, so gateway state (cursor and "
                        f"job history) goes to {FALLBACK_JOBS_DB!r} in the working "
                        "directory. Pass jobs_db_path= pointing at storage that outlives "
                        "restarts.",
                        stacklevel=2,
                    )
                    jobs_db_path = FALLBACK_JOBS_DB
            self.jobs = JobStore(jobs_db_path)

        # Register the gateway API routes on the CMS.
        # Must happen before cms.app is accessed.
        from starlette_cms_gateways.admin.api import make_gateway_api_routes

        for route in make_gateway_api_routes(self):
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
        from starlette_cms_gateways.admin.routes import make_admin_routes

        return Starlette(routes=make_admin_routes(self))
