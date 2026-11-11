/**
 * Astraeus embed script — inline editing for static sites.
 *
 * Derive cmsBase from the script's own src attribute so no config is needed.
 * Checks authentication before doing anything visible.
 *
 * IMPORTANT: document.currentScript is only available during synchronous
 * script execution (not after any await). Capture it before the first await.
 */
import { registerFieldWidget } from '../field-widgets.js'
import { createDoodleWidget } from '../components/doodle-widget.js'

// Capture synchronously before any await — document.currentScript is null after
const scriptEl = document.currentScript
const scriptUrl = scriptEl ? new URL(scriptEl.src) : null
const cmsBase = scriptUrl ? scriptUrl.origin : null
const mediaBase = scriptEl?.dataset?.cmsMediaBase || null
const reloadUrl = scriptEl?.dataset?.reloadUrl || null

// Registry-dispatched field widgets (ADR 020) — one registration per field
// type, shared with the admin form (fields.js registers it too, separately,
// once that surface adopts doodles — see phase 7 of the doodles plan).
registerFieldWidget('doodles', createDoodleWidget)

// Expose config globally so components/image-picker.js and edit-mode.js can read it
if (cmsBase) {
  window.__EDITOR_CONFIG__ = { cmsBase, mediaBase }
}

if (cmsBase) {
  ;(async () => {
    // Check authentication — exit silently if not logged in
    let authenticated = false
    try {
      const res = await fetch(`${cmsBase}/api/auth/me`, { credentials: 'include' })
      const data = await res.json()
      authenticated = data.authenticated === true
    } catch {
      return  // CMS unreachable — exit silently
    }
    if (!authenticated) return

    // Find all CMS-annotated elements on the page. A listing with a card
    // template still gets the toolbar with none: its drafts are drawn on Edit.
    const cmsElements = Array.from(document.querySelectorAll('[data-cms-id]'))
    const { findDraftSlots } = await import('./drafts.js')
    if (cmsElements.length === 0 && findDraftSlots().length === 0) return

    // The panels and publish drawer are written against design tokens and
    // editor.css classes the host never loads; supply them, scoped to the
    // editor's own elements (see styles.js).
    const { injectChromeStyles } = await import('./styles.js')
    injectChromeStyles()

    // Boot the toolbar
    const { EditToolbar } = await import('./toolbar.js')
    const toolbar = new EditToolbar({ cmsBase, cmsElements, reloadUrl })
    toolbar.mount()

    // Boot the changeset panel alongside the toolbar. Same component the shell
    // mounts (ADR 020 §3) — only the mounting differs. No onNavigate: on the
    // published site a document is a URL, and the panel hides "Go to" rather
    // than offering an action that does nothing.
    const { ChangesetPanel } = await import('../components/changeset-panel.js')
    const changesetPanel = new ChangesetPanel({ variant: 'embed' })
    changesetPanel.mount()
    toolbar.changesetPanel = changesetPanel

    // Boot the AI chat panel (bottom-left, peer of the changeset panel)
    const { ChatPanel } = await import('../components/chat-panel.js')
    const apiKey = scriptEl?.dataset?.cmsApiKey ?? null
    // The document the chat acts on. A single-document page has an obvious
    // answer; on a listing, "the first card" is a guess that edited the wrong
    // post, so start with none and follow the card the user last clicked or
    // typed into. With none selected the assistant asks which one.
    const docIds = new Set(cmsElements.map(el => el.dataset.cmsId))
    let currentDocId = docIds.size === 1 ? [...docIds][0] : null
    for (const eventName of ['pointerdown', 'focusin']) {
      document.addEventListener(eventName, (e) => {
        const card = e.target.closest?.('[data-cms-id]')
        if (card) currentDocId = card.dataset.cmsId
      }, true)
    }

    // collab is lazily initialised by edit-mode.js; stubs for getDocContext until then
    let collab = null
    const chatPanel = new ChatPanel(cmsBase, null, {
      getDocContext: () => ({
        doc_id: currentDocId,
        version: collab?.currentVersion?.() ?? 0,
        draft_body: collab?.currentDoc?.() ?? null,
        selection: collab?.currentSelection?.() ?? null,
        active_changeset_id: changesetPanel.activeChangesetId ?? null,
      }),
      apiKey,
    })
    chatPanel.mount()
    toolbar.setChatPanel(chatPanel)
  })()
}
