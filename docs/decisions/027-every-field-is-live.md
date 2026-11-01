# ADR 027 — Every field is live: rich text through the authority, plain fields last-writer-wins

**Status:** Proposed (to be accepted when it merges). Adds to [ADR 021](021-editing-transport-and-durability.md)
and [ADR 024](024-collab-sync-engine.md); supersedes nothing.
**Date:** 2026-11-01

---

## Context

A document can be written two ways:

1. **The collab path.** An editor sends small edits (*steps*) over the WebSocket
   `/api/documents/{id}/collab`. The field's `CollabAuthority` applies them in order, against the document
   as it is now, and sends them to every other editor of the field. With `verify_collab` on it applies
   each step to its own copy, so the stored document is the one the steps produce.
2. **The CRUD path.** `PATCH /api/documents/{id}` merges a JSON object over the draft body. The MCP
   `update_document` tool, scripts, gateways and (until this change) the shell's Save button use it.

Only rich-text fields were on the collab path. Every other field (title, tagline, tags, image) was a form
input saved by PATCH, so two people editing a post did not see each other's title until they reloaded.
That was never the intent; it is how the editor happened to be built.

For a rich-text field the CRUD path is a *lost update* waiting to happen. A whole-field PATCH is built from
whatever its writer last read; anything typed after that read is rolled back when it lands. On
2026-10-26 this lost every body edit in four drafts: Save sent the body as it was when the post was
opened, over about 3,000 steps the authority had accepted.

And there was no good way to do what the CMS exists for: a person writing a post while a Claude session,
over MCP, scaffolds or fixes part of the same post, and the person watches it land.

### How others do it

- **Google Docs** has one write path for content. Its API (`batchUpdate`) takes a list of typed
  operations (insert text, delete a range, replace all), applied together and optionally pinned to a
  revision. There is no "replace the document" call.
- **Figma** keeps the document on a server per file. Object properties sync as last-writer-wins per
  property; text inside a text layer merges finer. Plugins edit through the same live document.
- **Anthropic's text-editor tool**, which models are trained to use on files, edits by anchor:
  `str_replace` takes an exact `old_str` that must occur exactly once. Models quote text they have just
  read reliably and count positions badly; requiring a unique quote makes a stale edit fail loudly
  instead of landing in the wrong place.
- MCP guidance favours a few composable tools with clear errors over one tool per verb.

## Decision

**Every field is live.** Each field has one way in, and every editor sees every change as it happens.

1. **Rich-text fields go through the collab authority, as steps.** The editor sends steps over the
   WebSocket. Anything else (an MCP client, a script) sends **anchored edits** to
   `POST /api/documents/{id}/edit`, applied in order and all or nothing:

   ```json
   {"find": "exact text", "replace": "markdown"}      // "" deletes it
   {"section": "Heading", "replace": "markdown"}      // the heading and its section
   {"insert": "markdown", "after_heading": "Setup"}  // at the end of that section
   {"insert": "markdown", "at": "end"}                // or "start"
   ```

   Each anchor is resolved against the authority's *live* document when the edit is applied, so the edit
   lands where its anchor is now. A `find` must occur exactly once; if a person has just retyped that
   text, the batch is refused (422, naming the edit) and nothing changes. The result goes out as ordinary
   steps (`origin` `mcp`), so open editors show it and rebase their user's pending typing over it. If no
   editor has the field open, the authority is loaded for the edit and let go after. The MCP tool is
   `edit_document_body`: one tool for adding, rewriting and deleting body text.

2. **A whole-field replace must say what it replaces.** A PATCH that sets a rich-text field may carry
   `base_version` (the `draft_version` its writer read). If the field has moved on since, it is refused with
   409. While an editor has the field open, `base_version` is required. With no editor open and no
   `base_version`, it is still accepted, so scripts, seeds and migrations keep working. This is the last
   resort, not the way to edit.

3. **Plain fields (title, tagline, tags, image, dates, refs) are live, last-writer-wins per field.** They
   are small values swapped whole, so they do not need step merging. The editor sends a field's new value
   over the document's live connection as the person types; the server stores it and broadcasts it, and
   every open editor updates that input. A PATCH from MCP or a script does the same broadcast. Two people
   typing in the same title at the same instant: the later write wins, as in Figma and Notion properties.
   With every field live, the editor's Save button has nothing left to do and goes; a new document
   still has Create. The broadcast is a `document.fields` event on the in-process event bus
   (`/api/events`), tagged with the writer's `X-Client-Id` so a tab ignores its own echo, and never
   part of a webhook payload, since webhooks go to outside URLs. A save the server refuses (a required
   field left empty) is shown in the header and retried when the field changes, never dropped.

4. **Reads return the draft.** MCP `get_document` reads `?draft=true` (what the editor shows) and its
   `draft_version`; `published=True` reads the live version.

5. **Lifecycle actions stay commands** (publish, discard, changesets, delete), and each one that changes a
   field tells the editors (`discard-draft` is folded into open sessions as a step).

### Rule of thumb for code and agents

| You want to… | Use |
|---|---|
| Add, rewrite or delete text or sections in a body | `/edit` / `edit_document_body` |
| Edit text a person is typing in | the editor (WebSocket), or anchored edits; never a PATCH |
| Rewrite a whole body on purpose | PATCH with `base_version` from a draft read |
| Change a title, tags, image, dates, refs | PATCH / `update_document` (broadcast live) |
| Publish, discard, move between changesets | the lifecycle endpoints |

## Consequences

**Positive**

- The use case works: a Claude session can add to, rewrite or trim a post the person has open, and they see
  it land with their typing intact.
- One MCP tool for body edits instead of one per verb.
- A stale write fails loudly (422 or 409, with what to do next) instead of rolling back someone's typing.
- Every rich-text change, from any writer, is a step in the log, so the body's history replays.

**Negative / accepted**

- A `find` must sit inside one paragraph or heading, or match whole top-level blocks separated by blank
  lines. Text that spans part of one block and part of the next, or sits inside a list, is reached by
  quoting the whole block or by `section`.
- Replacing part of a line with markdown keeps to that line; more than one paragraph needs `find` to be
  the whole block.
- `/edit` needs the `collab-verify` extra (prosemirror and mistune); without it, it answers 501.
- The check between reading `base_version` and folding a PATCH is not atomic with the WebSocket. A
  keystroke in that window is rolled back by the replace, but as a broadcast step the editor shows, not
  silently.
- Last-writer-wins on plain fields can drop one of two simultaneous edits to the same short field. Both
  people see the result at once, so neither is surprised later.
- A remote change to a field the person is typing in waits until they leave the form, and one to a
  field they have changed and not yet saved is overridden by their save; that is last-writer-wins.
- The inline editor on the published site (the embed) saves plain fields but does not yet receive other
  writers' changes live; it shows them on reload.
- The markdown converter (`starlette_cms.markdown_pm`) covers the editor's schema: headings, paragraphs,
  bold, italic, code, links, images, lists, quotes, rules and code blocks. Anything else (tables, HTML)
  arrives as raw text. starlette-chat still has its own converter, which drops links; moving it onto this
  one needs its diff to handle leaf blocks first.
