"""
Tests for the GatewayAdmin — API routes and shell page.

Uses a real in-process CMS (same pattern as test_sync_e2e.py) and patches
entry-point discovery so the tests don't depend on any installed gateways.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from unittest.mock import patch

import httpx
import pytest_asyncio
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette_cms import CMS, TextField
from starlette_cms_gateways.admin import GatewayAdmin
from starlette_cms_gateways.base import BaseGateway, GatewayItem

# ---------------------------------------------------------------------------
# Minimal test gateway (mirrors test_sync_e2e.py)
# ---------------------------------------------------------------------------

_ADMIN_FAKE_DB: list[dict] = []


class AdminTestGateway(BaseGateway):
    service_name = "admin_test_service"
    block_type = "admin_test_item"
    auto_publish = True

    async def fetch(self) -> AsyncIterator[GatewayItem]:
        for entry in _ADMIN_FAKE_DB:
            yield GatewayItem(
                import_ref=f"admin_test:item:{entry['id']}",
                slug=f"admin-test-{entry['id']}",
                body={"name": entry["name"]},
            )


_FAKE_ENTRY_POINTS = {"admin-test": AdminTestGateway}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def admin_app():
    """
    Spin up a CMS + GatewayAdmin with an in-process ASGI client.

    Yields a dict with:
      - ``client``: httpx.AsyncClient routed through the full Starlette app
      - ``cms``: the CMS instance (for direct inspection)
      - ``admin``: the GatewayAdmin instance
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        jobs_db_path = f.name

    try:
        cms_instance = CMS(
            database_url=f"sqlite:///{db_path}",
            auth="apikey",
            api_key="test-secret",
            mount_path="/cms",
        )

        @cms_instance.block("admin_test_item")
        class AdminTestItemBlock:
            name: str = TextField(required=True)

        admin_instance = GatewayAdmin(cms=cms_instance, jobs_db_path=jobs_db_path)

        root = Starlette(
            routes=[
                Mount("/cms", app=cms_instance.app),
                Mount("/gateways", app=admin_instance.app),
            ]
        )

        async with cms_instance.lifespan_context(None):
            transport = ASGITransport(app=root)
            client = httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            )
            try:
                yield {"client": client, "cms": cms_instance, "admin": admin_instance}
            finally:
                await client.aclose()
    finally:
        for path in (db_path, jobs_db_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-secret"}


# ---------------------------------------------------------------------------
# Shell tests
# ---------------------------------------------------------------------------


async def test_shell_returns_html(admin_app):
    """GET /gateways/shell should return an HTML page."""
    resp = await admin_app["client"].get("/gateways/shell")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Gateway Admin" in resp.text
    assert "__GATEWAY_CONFIG__" not in resp.text  # uses inline var, not window config
    # The JS config must inject the correct CMS base path
    assert "/cms" in resp.text


# ---------------------------------------------------------------------------
# GET /cms/api/gateways
# ---------------------------------------------------------------------------


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_list_gateways(mock_disc, admin_app):
    """GET /api/gateways returns all installed gateways."""
    resp = await admin_app["client"].get("/cms/api/gateways", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    gw = data["gateways"][0]
    assert gw["name"] == "admin-test"
    assert gw["service_name"] == "admin_test_service"
    assert gw["block_type"] == "admin_test_item"
    assert gw["auto_publish"] is True
    # No runs yet — last_synced must be None
    assert gw["last_synced"] is None


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value={},
)
async def test_list_gateways_empty(mock_disc, admin_app):
    """Returns an empty list when no gateways are installed."""
    resp = await admin_app["client"].get("/cms/api/gateways", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# GET /cms/api/gateways/{name}
# ---------------------------------------------------------------------------


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_get_gateway_found(mock_disc, admin_app):
    resp = await admin_app["client"].get("/cms/api/gateways/admin-test", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "admin-test"
    assert "recent_jobs" in data


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_get_gateway_not_found(mock_disc, admin_app):
    resp = await admin_app["client"].get(
        "/cms/api/gateways/no-such-gateway", headers=_auth_headers()
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"].lower()


# ---------------------------------------------------------------------------
# POST /cms/api/gateways/{name}/sync
# ---------------------------------------------------------------------------


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_trigger_sync_returns_202(mock_disc, admin_app):
    """POST /api/gateways/{name}/sync returns 202 immediately."""
    global _ADMIN_FAKE_DB
    _ADMIN_FAKE_DB = [{"id": "1", "name": "Alpha"}]

    resp = await admin_app["client"].post(
        "/cms/api/gateways/admin-test/sync",
        headers=_auth_headers(),
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "running"
    assert "run_id" in data
    assert "poll_url" in data


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_trigger_sync_requires_auth(mock_disc, admin_app):
    """POST without auth header should return 401."""
    resp = await admin_app["client"].post("/cms/api/gateways/admin-test/sync")
    assert resp.status_code == 401


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_trigger_sync_unknown_gateway(mock_disc, admin_app):
    resp = await admin_app["client"].post(
        "/cms/api/gateways/nonexistent/sync",
        headers=_auth_headers(),
    )
    assert resp.status_code == 404


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_trigger_sync_passes_the_requested_range(mock_disc, admin_app):
    """A JSON body picks the range for this run, overriding the gateway default."""
    import asyncio

    global _ADMIN_FAKE_DB
    _ADMIN_FAKE_DB = [{"id": "R1", "name": "Ranged"}]
    seen: list = []

    original_sync = AdminTestGateway.sync

    async def capturing_sync(self, range=None):  # noqa: A002
        seen.append(range)
        return await original_sync(self, range)

    with patch.object(AdminTestGateway, "sync", capturing_sync):
        resp = await admin_app["client"].post(
            "/cms/api/gateways/admin-test/sync",
            headers=_auth_headers(),
            json={"range": "custom", "from": "2026-01-01", "to": "2026-01-31"},
        )
        assert resp.status_code == 202
        for _ in range(20):
            await asyncio.sleep(0.25)
            if seen:
                break

    assert seen and seen[0].mode == "custom"
    assert (seen[0].start.isoformat(), seen[0].end.isoformat()) == ("2026-01-01", "2026-01-31")


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_trigger_sync_rejects_a_bad_range(mock_disc, admin_app):
    for payload in (
        {"range": "sometimes"},
        {"range": "custom"},  # needs dates
        {"range": "all_time", "from": "2026-01-01"},  # dates need custom
        {"range": "custom", "from": "not-a-date"},
    ):
        resp = await admin_app["client"].post(
            "/cms/api/gateways/admin-test/sync",
            headers=_auth_headers(),
            json=payload,
        )
        assert resp.status_code == 422, payload


# ---------------------------------------------------------------------------
# GET /cms/api/gateways/{name}/sync/{run_id} — poll
# ---------------------------------------------------------------------------


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_poll_job_not_found(mock_disc, admin_app):
    resp = await admin_app["client"].get(
        "/cms/api/gateways/admin-test/sync/nonexistent-run-id"
    )
    assert resp.status_code == 404


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_trigger_and_poll_sync_to_done(mock_disc, admin_app):
    """
    Trigger a sync, receive a run_id, then poll until the job is done.

    The sync itself runs as an asyncio.Task; by the time poll requests land the
    task may still be running.  We retry polling up to 10 times with a short
    sleep to give the task time to finish.
    """
    import asyncio

    global _ADMIN_FAKE_DB
    _ADMIN_FAKE_DB = [{"id": "P1", "name": "PollTest"}]

    # Kick off sync
    trigger_resp = await admin_app["client"].post(
        "/cms/api/gateways/admin-test/sync",
        headers=_auth_headers(),
    )
    assert trigger_resp.status_code == 202
    run_id = trigger_resp.json()["run_id"]

    # Poll until done (or timeout after ~5s)
    final: dict = {}
    for _ in range(20):
        await asyncio.sleep(0.25)
        poll_resp = await admin_app["client"].get(
            f"/cms/api/gateways/admin-test/sync/{run_id}"
        )
        assert poll_resp.status_code == 200
        final = poll_resp.json()
        if final["status"] != "running":
            break

    assert final["status"] == "done", f"Job did not finish: {final}"
    result = final["result"]
    assert result["created"] == 1
    assert result["errors"] == []

    # After sync completes, last_synced should appear in the gateway list
    list_resp = await admin_app["client"].get("/cms/api/gateways", headers=_auth_headers())
    assert list_resp.status_code == 200
    gw_after = list_resp.json()["gateways"][0]
    assert gw_after["last_synced"] is not None, "last_synced must be set after a successful sync"
    # Must be a parseable ISO datetime string
    from datetime import datetime
    datetime.fromisoformat(gw_after["last_synced"])


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_second_sync_skips_identical(mock_disc, admin_app):
    """Run the same sync twice; second run should skip all docs."""
    import asyncio

    global _ADMIN_FAKE_DB
    _ADMIN_FAKE_DB = [{"id": "S1", "name": "SkipMe"}]

    async def _sync_and_wait() -> dict:
        resp = await admin_app["client"].post(
            "/cms/api/gateways/admin-test/sync",
            headers=_auth_headers(),
        )
        run_id = resp.json()["run_id"]
        for _ in range(20):
            await asyncio.sleep(0.25)
            poll = await admin_app["client"].get(
                f"/cms/api/gateways/admin-test/sync/{run_id}"
            )
            data = poll.json()
            if data["status"] != "running":
                return data
        return {}

    r1 = await _sync_and_wait()
    assert r1["status"] == "done"
    assert r1["result"]["created"] == 1

    r2 = await _sync_and_wait()
    assert r2["status"] == "done"
    assert r2["result"]["skipped"] == 1
    assert r2["result"]["created"] == 0


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_job_persisted_in_sqlite(mock_disc, admin_app):
    """
    Job records are stored in the SQLite job store, not in the CMS.

    After a sync completes the run_id is readable directly from the JobStore,
    proving persistence survives beyond any in-memory state.  Also confirms
    the gateway_sync_job block type is NOT registered in the CMS block registry
    (it should never appear in the editor).
    """
    import asyncio

    global _ADMIN_FAKE_DB
    _ADMIN_FAKE_DB = [{"id": "PERSIST1", "name": "Persistent"}]

    resp = await admin_app["client"].post(
        "/cms/api/gateways/admin-test/sync",
        headers=_auth_headers(),
    )
    run_id = resp.json()["run_id"]

    # Wait for completion via poll endpoint
    final: dict = {}
    for _ in range(20):
        await asyncio.sleep(0.25)
        poll = await admin_app["client"].get(
            f"/cms/api/gateways/admin-test/sync/{run_id}"
        )
        final = poll.json()
        if final["status"] != "running":
            break

    assert final["status"] == "done"

    # Read directly from the job store — confirms it's in SQLite
    job = await admin_app["admin"].jobs.get(run_id)
    assert job is not None, "Job should be in the SQLite job store"
    assert job["gateway_name"] == "admin-test"
    assert job["status"] == "done"
    assert job["result"]["created"] == 1

    # Confirm gateway_sync_job is NOT in the CMS block registry
    cms = admin_app["cms"]
    assert "gateway_sync_job" not in cms.registry._blocks, (
        "gateway_sync_job block must not appear in the CMS registry or editor"
    )


# ---------------------------------------------------------------------------
# Pre-built JobStore acceptance
# ---------------------------------------------------------------------------


async def test_gateway_admin_accepts_pre_built_job_store():
    """
    GatewayAdmin should use a pre-built JobStore when passed as job_store=,
    rather than constructing its own.
    """
    import tempfile

    from starlette_cms_gateways.admin import GatewayAdmin
    from starlette_cms_gateways.jobstore import JobStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        cms_db = f.name
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        jobs_db = f.name

    try:
        cms_instance = CMS(
            database_url=f"sqlite:///{cms_db}",
            auth="apikey",
            api_key="test-secret",
            mount_path="/cms",
        )

        @cms_instance.block("pre_built_test_item")
        class PreBuiltTestItemBlock:
            name: str = TextField(required=True)

        store = JobStore(jobs_db)
        admin_instance = GatewayAdmin(cms=cms_instance, job_store=store)

        assert admin_instance.jobs is store, (
            "admin.jobs should be the pre-built store, not a new one"
        )
    finally:
        for path in (cms_db, jobs_db):
            try:
                os.unlink(path)
            except OSError:
                pass


async def test_gateway_admin_warns_if_both_job_store_and_path_given():
    """
    Passing both job_store= and a non-default jobs_db_path should emit a
    UserWarning.
    """
    import tempfile
    import warnings

    from starlette_cms_gateways.admin import GatewayAdmin
    from starlette_cms_gateways.jobstore import JobStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        cms_db = f.name
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        jobs_db = f.name

    try:
        cms_instance = CMS(
            database_url=f"sqlite:///{cms_db}",
            auth="apikey",
            api_key="test-secret",
            mount_path="/cms",
        )

        @cms_instance.block("warn_test_item")
        class WarnTestItemBlock:
            name: str = TextField(required=True)

        store = JobStore(jobs_db)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            GatewayAdmin(
                cms=cms_instance,
                job_store=store,
                jobs_db_path="/some/custom/path.db",
            )
        assert any("ignored" in str(warning.message).lower() for warning in w), (
            "Expected a UserWarning about jobs_db_path being ignored"
        )
    finally:
        for path in (cms_db, jobs_db):
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# job_store wired to gateway constructor
# ---------------------------------------------------------------------------


@patch(
    "starlette_cms_gateways.admin.api.discover_gateways",
    return_value=_FAKE_ENTRY_POINTS,
)
async def test_gateway_construction_passes_job_store(mock_disc, admin_app):
    """
    When the admin triggers a sync, the gateway class should be constructed
    with job_store= and job_store_key= matching the admin's job store and
    the entry-point name.
    """
    import asyncio

    global _ADMIN_FAKE_DB
    _ADMIN_FAKE_DB = [{"id": "WS1", "name": "WireStore"}]

    constructed_kwargs: dict = {}

    original_init = AdminTestGateway.__init__

    def capturing_init(self, **kwargs):
        constructed_kwargs.update(kwargs)
        original_init(self, **kwargs)

    with patch.object(AdminTestGateway, "__init__", capturing_init):
        resp = await admin_app["client"].post(
            "/cms/api/gateways/admin-test/sync",
            headers=_auth_headers(),
        )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        # Wait for the task to finish so __init__ has been called
        final: dict = {}
        for _ in range(20):
            await asyncio.sleep(0.25)
            poll = await admin_app["client"].get(
                f"/cms/api/gateways/admin-test/sync/{run_id}"
            )
            final = poll.json()
            if final["status"] != "running":
                break

    assert final["status"] == "done", f"Sync did not finish: {final}"
    assert "job_store" in constructed_kwargs, (
        "gateway constructor must receive job_store= from the admin"
    )
    assert constructed_kwargs["job_store"] is admin_app["admin"].jobs, (
        "job_store must be the admin's shared job store"
    )
    assert constructed_kwargs.get("job_store_key") == "admin-test", (
        "job_store_key must be the entry-point name"
    )


async def test_shell_is_also_at_the_mount_root(admin_app):
    resp = await admin_app["client"].get("/gateways/")
    assert resp.status_code == 200
    assert "Gateway Admin" in resp.text


async def test_shell_loads_the_app_bar_when_given_a_portal(admin_app):
    assert "app-bar.js" not in (await admin_app["client"].get("/gateways/shell")).text

    admin_app["admin"].portal_url = "/admin/"
    resp = await admin_app["client"].get("/gateways/shell")
    assert '<script src="/admin/app-bar.js" defer></script>' in resp.text
