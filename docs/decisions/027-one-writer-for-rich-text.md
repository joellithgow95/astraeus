# ADR 027 — Rich text has one writer: the collab authority

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

For a plain field (a title, a tag list, an image) the CRUD path is fine: the value is small and is swapped
whole. For a rich-text field it is a *lost update* waiting to happen. A whole-field PATCH is built from
whatever its writer last read; anything typed after that read is rolled back when it lands. On
2026-10-26 this lost every body edit in four drafts: Save sent the body as it was when the post was
opened, over about 3,000 steps the authority had accepted.

PR #54 made the two paths meet (a PATCH to a rich-text field is folded into the open session as a step,
so editors see it) and stopped Save sending rich text. That made the overwrite visible, but it was still
an overwrite. And it left no good way to do what the CMS exists for: a person writing a post while a
Claude session, over MCP, scaffolds a section of the same post, and the person watches it appear.

Google Docs and Figma both have one write path for document content. The Docs API has no "replace the
document" call; it takes operations (*insert this text at this index*), optionally pinned to a revision.
Figma plugins edit through the live document, the same way the app does. Both keep ordinary APIs for
metadata and files.

## Decision

**A rich-text field has one writer: its collab authority.** Every change to one goes through the authority
as steps, whoever makes it.

1. **Add with an operation, not a rewrite.** `POST /api/documents/{id}/insert` takes markdown (or
   ProseMirror block nodes) and a place: the end or start of the field, or the end of the section under a
   heading. The authority builds the insert against the *live* document, applies it as one step, logs it
   and broadcasts it (`origin` `mcp`/`insert`), so an open editor shows it and rebases its user's pending
   typing over it. No version is needed, because nothing is read first. If no editor has the field open
   the authority is loaded for the insert and let go after. The MCP tool is `insert_into_document`.
2. **A whole-field replace must say what it replaces.** A PATCH that sets a rich-text field may carry
   `base_version` (the `draft_version` its writer read). If the field has moved on since, it is refused with
   409 rather than applied. While an editor has the field open, `base_version` is required: the field is
   moving under the writer, so a replace without one is always a guess. With no editor open and no
   `base_version`, it is still accepted, so scripts, seeds and migrations keep working.
3. **Reads return the draft.** MCP `get_document` reads `?draft=true` (what the editor shows) and its
   `draft_version`; `published=True` reads the live version.
4. **Structured fields stay CRUD.** Titles, tags, images, dates and refs are small values swapped whole;
   last writer wins per field, and an editor's Save writes them. They may move to the authority later, but
   nothing loses typed text today because they don't.
5. **Lifecycle actions stay commands** (publish, discard, changesets, delete), and each one that changes a
   rich-text field tells the authority (`discard-draft` is folded in as a step, since #54).

### Rule of thumb for code and agents

| You want to… | Use |
|---|---|
| Add a section, paragraph or note to a body | `insert` endpoint / `insert_into_document` |
| Edit text a person is typing in | the editor (WebSocket), never a PATCH |
| Rewrite a whole body on purpose | PATCH with `base_version` from a draft read |
| Change a title, tags, image, dates, refs | PATCH / `update_document`, no version needed |
| Publish, discard, move between changesets | the lifecycle endpoints |

## Consequences

**Positive**

- The use case works: a Claude session can write into a post the person has open, and they see it land.
- A stale whole-body write fails loudly (409 with the current version and a hint) instead of rolling back
  someone's typing.
- Every rich-text change, from any writer, is a step in the log, so the body's history replays (#54).

**Negative / accepted**

- Only *adding* is an operation so far. Changing or deleting text a person may be editing still means a
  replace with `base_version`, which will often be refused while they type. That is the safe failure;
  operations for replace-this-paragraph can follow when they are needed.
- The insert endpoint needs the `collab-verify` extra (prosemirror and mistune); without it, it answers 501.
- The check between reading `base_version` and folding the write is not atomic with the WebSocket. A
  keystroke that lands in that window is rolled back by the replace, but as a broadcast step the editor
  shows, not silently.
- The markdown converter (`starlette_cms.markdown_pm`) covers the editor's schema: headings, paragraphs,
  bold, italic, code, links, images, lists, quotes, rules and code blocks. Anything else (tables, HTML)
  arrives as raw text. starlette-chat still has its own converter, which drops links; moving it onto this
  one needs its diff to handle leaf blocks first.
