# Editor widget inventory

Reference for the shared widget layer ([ADR 020](decisions/020-shared-widget-layer.md)), built
to the touch constraints in [ADR 022](decisions/022-touch-as-base-case.md).

Every control the shell needs, where it lives today, and what it has to become. The shell is the
reference implementation: a widget is done when the shell uses it and `embed/` can adopt it
unchanged apart from mounting.

**Status key:** `shared` — already in `components/` or `prosemirror/`. `shell-only` — lives in
`standard/`, needs promoting. `duplicated` — exists separately in both surfaces. `missing` —
does not exist yet.

---

## Field widgets

Dispatched by `fieldWidget()` in `standard/fields.js:23`. The `field_type` from
`cms:field_meta` is authoritative; legacy heuristics below it are a compatibility path for
fields with no annotation.

| Widget | `field_type` | Status | Touch work needed |
|---|---|---|---|
| `prosemirror` | `rich_text` | `duplicated` — `prosemirror/mount.js` (shell) vs `embed/prosemirror-embed.js` | Toolbar overflow at 44px targets; selection handles; keyboard-occlusion on focus |
| `block_canvas` | `block_list`, `block` | `shared`, shell-only consumer | Replace drag-and-drop with numbered rows + move controls |
| `image_picker` | `image` | `shared`, both consume | Modal → full-screen sheet; astraeus-media iframe is the weak point |
| `select` | `select` | `shell-only` | 16px floor; native select is correct on mobile, keep it |
| `number` | `number` | `shell-only` | 16px floor; `inputmode="decimal"` |
| `boolean` | `boolean` | `shell-only` | Toggle target to 44px |
| `json` | `json` | `shell-only` | Monospace textarea; 16px floor makes it wide — needs horizontal scroll, not wrap |
| `input` | `document_ref`, short string | `shell-only` | `document_ref` renders as a plain text input today; wants a picker |
| `textarea` | long string | `shell-only` | 16px floor; autogrow |

**Notes**

- `document_ref` being a bare text input is a known gap (`fields.js:34`, comment: "rendered as
  plain text input for now"). Promoting it to a picker reuses the changeset panel's document-row
  rendering — worth doing after the panel merge, not before.
- `block` (single nested block) reuses `block_canvas` with one card (`fields.js:28`). The
  numbered-row treatment should suppress the number when there is exactly one.
- All of these except `prosemirror` are structured fields, so all of them persist over PATCH and
  depend on the local draft buffer ([ADR 021](decisions/021-editing-transport-and-durability.md) §3).

---

## Chrome and containers

| Widget | Today | Status | Becomes |
|---|---|---|---|
| Type list | `.sidebar-types`, collapses to a 48px icon rail at 900px | `shell-only` | Route in the navigation stack |
| Document list | `.sidebar-docs`, **`display: none` below 640px** (`editor.css:1406`) | `shell-only` | Route in the stack, with search |
| Pending view | — | `missing` | Default small-screen landing: dirty + staged documents, publish/discard inline |
| Editor header | `.editor-header`, 48px, title + publish toggle + Save + Delete | `shell-only` | Title bar; actions move to a bottom action bar inside the safe area |
| Bottom action bar | — | `missing` | Save / publish / overflow, `env(safe-area-inset-bottom)` |
| Toast | `components/toast.js` | `shared`, shell-only consumer | Position above the action bar and the safe area |
| Confirm | `components/confirm.js` — **dead, zero importers** | `shared` | Real consumer: navigation guards (ADR 021 §4) and destructive actions. Replaces `window.confirm` in `editor-toolbar.js:_runAction` |
| Bottom sheet | — | `missing` | Container for chat, changesets, pickers on small screens |
| Doc meta panel | `components/meta-panel.js` | `shared`, shell-only consumer | Collapsed by default; copy-to-clipboard targets to 44px |
| Picker modal | `.picker-modal`, `min(900px, 90vw)` iframe | `shell-only` | Full-screen sheet below 640px |
| Editor toolbar pill | `standard/editor-toolbar.js`, fixed 36px, `mousedown` drag, localStorage geometry | `shell-only` | Merges into the bottom action bar on small screens; the drag grip is pointer-fine only |
| Chat panel | `embed/chat-panel.js`, fixed 360×540 | **imported backwards by `index.js:17`** | Promote to `components/`, sheet on small screens |
| Changeset panel | `standard/changeset-panel-shell.js` (1,184) + `embed/changeset-panel.js` (614) | `duplicated` | One component — see below |

---

## The changeset panel merge

Neither implementation is a superset. The merged component is the union, with the embed's
two-step publish as the publish flow.

**Shell only, keep:**
- Rename changeset
- Move a document between changesets ("Move to…" dropdown)
- Remove a document from a changeset
- Discard a draft
- Staged delete, with the "Will delete" badge
- Orphaned drafts section — drafts belonging to no changeset
- Inline diff viewer ("View changes")
- Clear active changeset

**Embed only, keep:**
- **Review & Publish confirm modal** — diff review, then Confirm/Cancel. This becomes the publish
  flow for both surfaces; a single-tap publish is wrong on touch, where the button is bigger and
  the user is less careful
- "Unpublished drafts" section with an add-to-changeset dropdown

**Both, reconcile:**
- List changesets; set active; publish; schedule; delete; create new
- Diff badges — `NEW` / `+n −n` / `No changes`
- Conflict warning — "⚠ Also in: …" when a document belongs to more than one changeset

**Drop from both:**
- `mousedown`-only drag on the title bar and `mousedown`-only resize handle. On desktop the panel
  docks; on small screens it is a sheet. Free positioning is not worth two pointer paths.
- Hardcoded Catppuccin hex throughout both files — replaced by tokens (ADR 020 §4).

**Behaviour that must survive the merge:** the shell panel reads `activeChangesetId` from
`changeset-store.js` (the one module both surfaces already share), and the shell adopts a
server-created changeset from the `x-changeset-id` response header on PATCH
(`standard/actions.js`). The merged component keeps `changeset-store.js` as the single source of
active-changeset state for both surfaces.

---

## Cross-cutting requirements

Every widget in this inventory:

- Minimum interactive target 44×44px; tighten under `@media (hover: hover) and (pointer: fine)`
- Text inputs at 16px or larger
- No hover-only affordance without a non-hover equivalent
- No `mousedown` handler without a pointer-event or explicit-control equivalent
- Tokens from the shared stylesheet — no hardcoded hex, no inline `cssText` colour
- Visible keyboard focus
- Respects `prefers-reduced-motion`

---

## Open questions

- ~~**ProseMirror toolbar overflow.**~~ Resolved in FU-1: it scrolls horizontally and stays one
  row at full size. Wrapping orphaned a separator and one button onto a second row; an overflow
  menu hides formatting behind a tap while the keyboard is already up.
- **`document_ref` picker.** Depends on the merged panel's document-row rendering; sequence it
  after the merge.
- **astraeus-media admin inside the image picker.** Iframing a separate Bootstrap app is the worst
  remaining small-screen surface. Token adoption improves the look but not the interaction model;
  a native picker view against the astraeus-media API is the real fix, and is out of scope for this
  round.
