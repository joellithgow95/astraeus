"""
Feedback admin shell route — ``GET /shell``.

A single HTML page with an inlined bootstrap config and a small vanilla-JS
frontend over the feedback API registered on the CMS. Same shape as the
gateway admin: styled with the CMS's ``tokens.css``, authenticated by the
same-origin ``cms_session`` cookie, no API key in the page.

Report text is user-supplied, so the script builds the DOM with
``textContent`` only — never ``innerHTML``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from starlette_cms_feedback.models import (
    OPEN_STATUSES,
    FeedbackKind,
    FeedbackSeverity,
    FeedbackStatus,
)

if TYPE_CHECKING:
    from starlette_cms_feedback.admin.app import FeedbackAdmin

_CONFIG_TOKEN = "__FEEDBACK_CONFIG__"

_SHELL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Feedback</title>
  <link rel="stylesheet" href="__CMS_BASE__/static/tokens.css" />
  <script src="__CMS_BASE__/static/theme.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font-sans);
      background: var(--bg-base);
      color: var(--text-primary);
      padding: var(--space-8);
      line-height: 1.5;
      max-width: 60rem;
    }
    h1 { font-size: var(--font-size-xl); font-weight: 600; margin-bottom: var(--space-6); }
    h2 {
      font-size: var(--font-size-lg);
      font-weight: 600;
      color: var(--text-secondary);
      margin-bottom: var(--space-3);
    }
    section { margin-bottom: var(--space-8); }
    form, .report {
      background: var(--bg-elevated);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-lg);
      padding: var(--space-5);
    }
    .row { display: flex; gap: var(--space-3); flex-wrap: wrap; }
    .row > label { flex: 1 1 10rem; }
    label {
      display: block;
      font-size: var(--font-size-sm);
      color: var(--text-secondary);
      margin-bottom: var(--space-3);
    }
    input, select, textarea {
      display: block;
      width: 100%;
      margin-top: var(--space-1);
      padding: var(--space-2) var(--space-3);
      background: var(--bg-base);
      color: var(--text-primary);
      border: 1px solid var(--border-default);
      border-radius: var(--radius-md);
      font: inherit;
    }
    textarea { min-height: 5rem; resize: vertical; }
    button {
      background: var(--accent);
      color: var(--accent-on);
      border: none;
      border-radius: var(--radius-md);
      min-height: var(--target-min);
      padding: 0 var(--space-4);
      font: inherit;
      cursor: pointer;
      transition: background var(--transition-fast);
    }
    button:hover { background: var(--accent-hover); }
    button:disabled { background: var(--bg-hover); color: var(--text-muted); cursor: not-allowed; }
    #filters { display: flex; gap: var(--space-3); margin-bottom: var(--space-4); }
    #filters label { margin: 0; min-width: 10rem; }
    .report { margin-bottom: var(--space-4); }
    .report-title { font-size: var(--font-size-lg); font-weight: 600; }
    .report-meta {
      font-size: var(--font-size-sm);
      color: var(--text-secondary);
      margin: var(--space-1) 0 var(--space-3);
    }
    .report-meta span { margin-right: var(--space-4); }
    .report-body { white-space: pre-wrap; margin-bottom: var(--space-3); }
    .field-name {
      font-size: var(--font-size-sm);
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    /* Work outstanding carries the one hue; a settled report goes quiet. */
    .status-open { color: var(--pending); }
    .status-settled { color: var(--text-muted); }
    .triage { border-top: 1px solid var(--border-default); padding-top: var(--space-3); }
    .note { font-size: var(--font-size-sm); color: var(--text-secondary); min-height: 1.2em; }
    .error { color: var(--pending); }
  </style>
</head>
<body>
  <h1>Feedback</h1>

  <section>
    <h2>File a report</h2>
    <form id="new-report">
      <div class="row">
        <label>Kind <select name="kind" required></select></label>
        <label>Component <input name="component" list="components" /></label>
        <label>Severity <select name="severity"></select></label>
      </div>
      <datalist id="components"></datalist>
      <label>Title <input name="title" maxlength="200" required /></label>
      <label>What happened <textarea name="description" required></textarea></label>
      <label>Steps to reproduce <textarea name="steps_to_reproduce"></textarea></label>
      <div class="row">
        <label>Expected <textarea name="expected"></textarea></label>
        <label>Actual <textarea name="actual"></textarea></label>
      </div>
      <label>Environment <input name="environment" placeholder="versions, browser, OS" /></label>
      <label>Reporter <input name="reporter" /></label>
      <button type="submit">File report</button>
      <p class="note" id="form-note"></p>
    </form>
  </section>

  <section>
    <h2>Reports</h2>
    <div id="filters">
      <label>Status <select id="filter-status"></select></label>
      <label>Kind <select id="filter-kind"></select></label>
    </div>
    <p class="note" id="list-note">Loading…</p>
    <div id="reports"></div>
  </section>

  <script>
  (function () {
    var CONFIG = __FEEDBACK_CONFIG__;

    async function apiFetch(path, opts) {
      var resp = await fetch(CONFIG.cmsBase + path, Object.assign({
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin"
      }, opts || {}));
      var data = await resp.json().catch(function () { return {}; });
      if (!resp.ok) throw new Error(data.error || (resp.status + " " + resp.statusText));
      return data;
    }

    // Build an element; children are text or nodes. Text never goes via innerHTML.
    function el(tag, props, children) {
      var node = document.createElement(tag);
      Object.keys(props || {}).forEach(function (k) { node[k] = props[k]; });
      (children || []).forEach(function (c) {
        node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
      });
      return node;
    }

    function label(value) { return value.replace(/_/g, " "); }

    function fillSelect(select, values, blank) {
      select.textContent = "";
      if (blank !== undefined) select.appendChild(el("option", { value: "", textContent: blank }));
      values.forEach(function (v) {
        select.appendChild(el("option", { value: v, textContent: label(v) }));
      });
    }

    function textBlock(name, value) {
      if (!value) return null;
      return el("div", {}, [
        el("div", { className: "field-name", textContent: label(name) }),
        el("div", { className: "report-body", textContent: value })
      ]);
    }

    async function patch(id, body) {
      return apiFetch("/api/feedback/" + encodeURIComponent(id), {
        method: "PATCH",
        body: JSON.stringify(body)
      });
    }

    function renderReport(r) {
      var isOpen = CONFIG.openStatuses.indexOf(r.status) >= 0;
      var statusClass = isOpen ? "status-open" : "status-settled";
      var note = el("p", { className: "note" });

      var status = el("select");
      fillSelect(status, CONFIG.statuses);
      status.value = r.status;

      var severity = el("select");
      fillSelect(severity, CONFIG.severities, "none");
      severity.value = r.severity || "";

      var notes = el("textarea", { value: r.triage_notes || "" });
      var save = el("button", { type: "button", textContent: "Save triage" });

      save.addEventListener("click", async function () {
        save.disabled = true;
        note.className = "note";
        note.textContent = "Saving…";
        try {
          await patch(r.id, {
            status: status.value,
            severity: severity.value || null,
            triage_notes: notes.value || null
          });
          note.textContent = "Saved.";
          await loadReports();
        } catch (e) {
          note.className = "note error";
          note.textContent = "Save failed: " + e.message;
        } finally {
          save.disabled = false;
        }
      });

      var meta = el("div", { className: "report-meta" }, [
        el("span", { className: statusClass, textContent: label(r.status) }),
        el("span", { textContent: r.kind }),
        el("span", { textContent: r.severity ? r.severity + " severity" : "no severity" }),
        el("span", { textContent: r.component || "no component" }),
        el("span", { textContent: new Date(r.created_at).toLocaleString() }),
        el("span", { textContent: r.reporter || "" })
      ]);

      var detail = [
        textBlock("description", r.description),
        textBlock("steps to reproduce", r.steps_to_reproduce),
        textBlock("expected", r.expected),
        textBlock("actual", r.actual),
        textBlock("environment", r.environment)
      ].filter(Boolean);

      var triage = el("div", { className: "triage" }, [
        el("div", { className: "row" }, [
          el("label", {}, ["Status", status]),
          el("label", {}, ["Severity", severity])
        ]),
        el("label", {}, ["Triage notes", notes]),
        save,
        note
      ]);

      return el("article", { className: "report" }, [
        el("div", { className: "report-title", textContent: r.title }),
        meta
      ].concat(detail, [triage]));
    }

    async function loadReports() {
      var listNote = document.getElementById("list-note");
      var box = document.getElementById("reports");
      var params = new URLSearchParams({ limit: "100" });
      var status = document.getElementById("filter-status").value;
      var kind = document.getElementById("filter-kind").value;
      if (status) params.set("status", status);
      if (kind) params.set("kind", kind);

      try {
        var data = await apiFetch("/api/feedback?" + params.toString());
        box.textContent = "";
        data.reports.forEach(function (r) { box.appendChild(renderReport(r)); });
        listNote.className = "note";
        listNote.textContent = data.total
          ? data.total + " report" + (data.total === 1 ? "" : "s")
          : "No reports match.";
      } catch (e) {
        listNote.className = "note error";
        listNote.textContent = "Failed to load reports: " + e.message;
      }
    }

    function init() {
      var form = document.getElementById("new-report");
      var formNote = document.getElementById("form-note");

      fillSelect(form.elements.kind, CONFIG.kinds);
      fillSelect(form.elements.severity, CONFIG.severities, "none");
      CONFIG.components.forEach(function (c) {
        document.getElementById("components").appendChild(el("option", { value: c }));
      });

      // The filters default to open work: that is what someone opening this page wants.
      var statusFilter = document.getElementById("filter-status");
      fillSelect(statusFilter, CONFIG.statuses, "all statuses");
      statusFilter.value = "new";
      fillSelect(document.getElementById("filter-kind"), CONFIG.kinds, "all kinds");
      statusFilter.addEventListener("change", loadReports);
      document.getElementById("filter-kind").addEventListener("change", loadReports);

      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        var body = {};
        Array.prototype.forEach.call(form.elements, function (field) {
          if (field.name && field.value) body[field.name] = field.value;
        });
        formNote.className = "note";
        formNote.textContent = "Filing…";
        try {
          await apiFetch("/api/feedback", { method: "POST", body: JSON.stringify(body) });
          form.reset();
          formNote.textContent = "Filed.";
          await loadReports();
        } catch (e) {
          formNote.className = "note error";
          formNote.textContent = "Could not file report: " + e.message;
        }
      });

      loadReports();
    }

    init();
  })();
  </script>
</body>
</html>"""


def make_admin_routes(admin: FeedbackAdmin) -> list[Route]:
    """Return the routes for the feedback admin sub-application (``GET /shell``)."""

    async def shell_endpoint(request: Request) -> HTMLResponse | RedirectResponse:
        if admin.auth is not None:
            allowed = admin.auth(request)
            if hasattr(allowed, "__await__"):
                allowed = await allowed
            if not allowed:
                # Send visitors to sign in, then back to the shell (?next=).
                return RedirectResponse(
                    f"{admin.login_path}?next={request.url.path}",
                    status_code=302,
                )

        cms_base = admin.cms.mount_path.rstrip("/")
        config = {
            "cmsBase": cms_base,
            "components": admin.components,
            "kinds": [k.value for k in FeedbackKind],
            "openStatuses": sorted(OPEN_STATUSES),
            "severities": [s.value for s in FeedbackSeverity],
            "statuses": [s.value for s in FeedbackStatus],
        }
        # "<" is escaped so a component name can't close the script element.
        config_js = json.dumps(config).replace("<", "\\u003c")

        html = _SHELL_HTML.replace("__CMS_BASE__", cms_base).replace(_CONFIG_TOKEN, config_js)
        return HTMLResponse(html)

    return [Route("/shell", endpoint=shell_endpoint, methods=["GET"])]
