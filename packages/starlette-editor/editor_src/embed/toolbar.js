/**
 * EditToolbar — floating live-editing toolbar for the Astraeus embed script.
 *
 * States:
 *   viewing    — "✏️ Edit" button + the draft picker
 *   editing    — "✓ Saved" indicator + Discard + Publish + the draft picker
 *   saving     — "⏳ Saving..." indicator + Discard + Publish
 *   publishing — spinner
 *   published  — "✓ Published — rebuilding site"
 */
import { getActiveChangesetId, setActiveChangesetId } from '../changeset-store.js'
import {
  LENS_ALL,
  LENS_PROD,
  changesetLens,
  changesetOfLens,
  findDraftSlots,
  getLens,
  setLens,
  syncDraftCards,
} from './drafts.js'

export class EditToolbar {
  constructor({ cmsBase, cmsElements, reloadUrl = null }) {
    this.cmsBase = cmsBase
    this.cmsElements = cmsElements  // all [data-cms-id] elements on page
    this.reloadUrl = reloadUrl      // optional dev-server reload endpoint
    this.state = 'viewing'          // 'viewing' | 'editing' | 'saving' | 'publishing' | 'published'
    this.activeElements = []        // live [data-cms-id] cards on the page: what Discard and the Publish fallback act on
    this.draftCards = []            // cards drawn for unpublished posts. Not in activeElements: Discard would wipe
                                    // a draft the user only looked at, and Publish would ship one nobody chose
    this.lens = getLens()           // which unpublished posts to show: 'all' | 'prod' | 'cs:<id>'
    this.changesets = []            // open changesets, for the picker
    this._draftError = null         // message shown when the drafts could not be loaded
    this.el = null                  // the toolbar DOM element
    this.changesetPanel = null      // set externally by index.js after ChangesetPanel is created
    this._chatPanel = null          // set via setChatPanel()
    this._chatBtn = null            // ref to the 💬 button for badge updates
    this._unread = 0                // unread chat message count
    this._saveError = null          // message shown when a save/connection fails
  }

  mount() {
    this.el = document.createElement('div')
    this.el.id = 'astraeus-toolbar'
    // A solid dark pill, so the buttons sit on a consistent surface and stay
    // legible on any host background. (The old styling used white text on
    // translucent-white buttons — invisible on a light site.) Values are
    // literal, not the shared design tokens: injecting tokens.css would define
    // :root vars that collide with the host page's own tokens.
    this.el.style.cssText = `
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 2147483000;
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 6px;
      max-width: min(560px, calc(100vw - 48px));
      padding: 6px;
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 14px;
      box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      padding-bottom: calc(6px + env(safe-area-inset-bottom));
    `
    document.body.appendChild(this.el)
    this._render()
    this._loadChangesets()
    // Show the drafts the picker names straight away, before Edit: a lens is a
    // preview first. Read-only until Edit makes them editable.
    if (this.lens !== LENS_PROD) this._syncDraftCards({ activate: false })
  }

  _render() {
    // Clear and re-render toolbar content based on this.state
    this.el.innerHTML = ''

    if (this.state === 'viewing') {
      const btn = this._makeButton('✏️ Edit', 'primary', () => this._startEditing())
      this.el.appendChild(btn)
      const picker = this._makeLensPicker()
      if (picker) this.el.appendChild(picker)
    }

    if (this.state === 'editing' || this.state === 'saving') {
      // Leave edit mode without discarding or publishing — the draft is kept.
      const closeBtn = this._makeButton('✕ Close', 'ghost', () => this._stopEditing())
      closeBtn.title = 'Stop editing — your draft is kept and can be resumed later'
      // A failed save takes over the indicator — the whole point is that it is
      // impossible to miss, unlike the silent failure this replaces.
      const failure = this._saveError ?? this._draftError
      const saveIndicator = failure
        ? this._makeIndicator(failure, '#e04b45')
        : this._makeIndicator(this.state === 'saving' ? '⏳ Saving...' : '✓ Saved')
      const discardBtn = this._makeButton('Discard draft', 'danger', () => this._discardDraft())
      const publishBtn = this._makeButton('Publish', 'primary', () => this._publish())
      this.el.appendChild(closeBtn)
      this.el.appendChild(saveIndicator)
      this.el.appendChild(discardBtn)
      this.el.appendChild(publishBtn)
      const picker = this._makeLensPicker()
      if (picker) this.el.appendChild(picker)
      if (this.changesetPanel) {
        const csBtn = this._makeButton('📋 Changesets', 'ghost', () => {
          this._clearOfToolbar(this.changesetPanel.el)
          this.changesetPanel.toggle()
        })
        this.el.appendChild(csBtn)
      }
      if (this._chatPanel) {
        this._chatBtn = this._makeButton('💬 Chat', 'ghost', () => {
          this._clearOfToolbar(this._chatPanel._el)
          this._unread = 0
          this._updateChatBadge()
          this._chatPanel.toggle()
        })
        this.el.appendChild(this._chatBtn)
      }
    }

    if (this.state === 'publishing') {
      this.el.appendChild(this._makeIndicator('Publishing...'))
    }

    if (this.state === 'published') {
      this.el.appendChild(this._makeIndicator('✓ Published — site rebuilding (~30s)'))
    }
  }

  // ── Which drafts to show ───────────────────────────────────────────────────

  /** The pick list: live only, every draft, or one open changeset. */
  _lensOptions() {
    const count = (cs) => {
      const n = (cs.documents ?? []).length
      return `${n} doc${n === 1 ? '' : 's'}`
    }
    const options = [
      { value: LENS_ALL, label: 'Live + all drafts' },
      { value: LENS_PROD, label: 'Live only (prod)' },
      ...this.changesets.map((cs) => ({
        value: changesetLens(cs.id),
        label: `Live + 📋 ${cs.title || 'Untitled'} · ${count(cs)}`,
      })),
    ]
    // A remembered changeset before the list has loaded: keep the select from going blank.
    if (!options.some((o) => o.value === this.lens)) {
      options.push({ value: this.lens, label: 'Live + 📋 changeset' })
    }
    return options
  }

  /**
   * The picker, or null on a page with no list to put drafts in (a post's own
   * page has nothing for it to do).
   */
  _makeLensPicker() {
    if (!findDraftSlots().length) return null

    const select = document.createElement('select')
    select.setAttribute('aria-label', 'Which drafts to show')
    select.title = 'Which unpublished posts to show alongside the live ones'
    select.style.cssText = `
      padding: 5px 10px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 16px; /* under 16px, iOS zooms the page when a control takes focus (ADR 022) */
      font-weight: 500;
      font-family: inherit;
      line-height: 1.2;
      max-width: 220px;
      background: transparent;
      color: #e8e8e8;
      border: 1px solid #3a3a3a;
    `
    for (const { value, label } of this._lensOptions()) {
      const option = document.createElement('option')
      option.value = value
      option.textContent = label
      option.style.cssText = 'background:#1a1a1a;color:#e8e8e8;'
      select.appendChild(option)
    }
    select.value = this.lens
    select.addEventListener('change', () => this._setLens(select.value))
    return select
  }

  async _loadChangesets() {
    if (!findDraftSlots().length) return
    try {
      const res = await fetch(`${this.cmsBase}/api/changesets?status=open&include_documents=true`, {
        credentials: 'include',
      })
      if (!res.ok) return
      this.changesets = (await res.json()).changesets ?? []
    } catch {
      return // the picker still offers live-only and all drafts
    }
    // A remembered changeset that has since been published or deleted.
    const csId = changesetOfLens(this.lens)
    if (csId && !this.changesets.some((cs) => cs.id === csId)) {
      this.lens = LENS_ALL
      setLens(this.lens)
    }
    this._render()
  }

  /**
   * Change which drafts show. Picking a changeset also makes it the one edits
   * are saved into, so what you see is what you are working in. The page
   * updates at once either way; while editing, the new cards are editable too.
   */
  async _setLens(lens) {
    this.lens = lens
    setLens(lens)
    const csId = changesetOfLens(lens)
    if (csId) setActiveChangesetId(csId)
    const editing = this.state === 'editing' || this.state === 'saving'
    await this._syncDraftCards({ activate: editing })
  }

  /**
   * Draw cards for the unpublished posts the lens asks for; optionally make them
   * editable. Runs one at a time: the first sync starts on page load, and an
   * Edit or a lens change during it would otherwise draw the same draft twice.
   */
  _syncDraftCards({ activate }) {
    const run = () => this._syncDraftCardsNow({ activate })
    this._draftSync = (this._draftSync ?? Promise.resolve()).then(run, run)
    return this._draftSync
  }

  async _syncDraftCardsNow({ activate }) {
    if (!findDraftSlots().length) return
    try {
      const { added } = await syncDraftCards({ cmsBase: this.cmsBase, lens: this.lens })
      this._draftError = null
      this.draftCards = [...document.querySelectorAll('[data-cms-draft-card]')]
      if (activate) await this._activateCards(added)
    } catch {
      this._draftError = '⚠ Could not load drafts'
    }
    this._render()
  }

  async _activateCards(elements) {
    const { activateEditMode } = await import('./edit-mode.js')
    for (const el of elements) {
      await activateEditMode(el, { cmsBase: this.cmsBase, toolbar: this })
    }
  }

  /**
   * Seat a panel just above the toolbar. Both are fixed to the bottom corner,
   * and the toolbar sits above them in the stack, so a panel left where it
   * mounts has its bottom edge (its input, its buttons) under the toolbar. The
   * toolbar wraps to two rows on a phone, so its height is read, not assumed.
   */
  _clearOfToolbar(panelEl) {
    if (!panelEl || !this.el) return
    const gap = 8
    panelEl.style.bottom = `${24 + this.el.offsetHeight + gap}px`
    panelEl.style.zIndex = '2147482900' // just under the toolbar, over the host's own layers
    panelEl.style.maxHeight = `calc(100dvh - ${24 + this.el.offsetHeight + gap + 16}px)`
  }

  _makeButton(label, variant, onClick) {
    const btn = document.createElement('button')
    btn.textContent = label
    // On the dark pill: primary is filled light, ghost is outlined light,
    // danger carries the one accent hue (matches the tokenised palette).
    const styles = {
      primary: 'background:#f0f0f0;color:#0d0d0d;border:1px solid #f0f0f0;',
      ghost: 'background:transparent;color:#e8e8e8;border:1px solid #3a3a3a;',
      danger: 'background:transparent;color:#e04b45;border:1px solid rgba(224,75,69,0.5);',
    }
    btn.style.cssText = `
      padding: 8px 14px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      font-family: inherit;
      line-height: 1;
      white-space: nowrap;
      ${styles[variant] || styles.primary}
    `
    btn.addEventListener('click', onClick)
    return btn
  }

  _makeIndicator(text, color = '#a3a3a3') {
    const el = document.createElement('div')
    el.textContent = text
    // Sits directly on the pill — no separate background.
    el.style.cssText = `
      padding: 8px 10px;
      color: ${color};
      font-size: 13px;
      white-space: nowrap;
    `
    return el
  }

  /** Show a save/connection failure in the toolbar until the next success. */
  setSaveError(message) {
    this._saveError = message
    this._render()
  }

  /** Clear a previously shown save error (called after a successful save). */
  clearSaveError() {
    if (!this._saveError) return
    this._saveError = null
    this._render()
  }

  setState(newState) {
    this.state = newState
    this._render()
  }

  async _startEditing() {
    // Activate every annotated document on the page, so a listing edits as a
    // whole rather than only its first entry. Each edit joins one changeset
    // (see edit-mode patchField), so the session publishes together.
    const { activateEditMode, suppressAnchorNavigationWhileEditing } = await import('./edit-mode.js')
    // Listing cards wrap the title in the card's link; keep clicks-to-edit from
    // navigating away.
    suppressAnchorNavigationWhileEditing()
    this.activeElements = [...this.cmsElements]
    // Unpublished posts have no card in the built page: draw them first, so
    // they are edited like the rest. A failure leaves the live cards editable.
    await this._syncDraftCards({ activate: false })
    this._loadChangesets()
    for (const el of [...this.activeElements, ...this.draftCards]) {
      await activateEditMode(el, { cmsBase: this.cmsBase, toolbar: this })
    }
    this.setState('editing')
  }

  _stopEditing() {
    // Leave edit mode while keeping the draft. The draft lives server-side and
    // the changeset id persists in changeset-store (localStorage), so
    // re-entering edit mode later resumes exactly where you left off. A reload
    // is the simplest reliable teardown of the in-page edit affordances
    // (contentEditable, ProseMirror mounts, dashed outlines, "Open" links) —
    // the same mechanism discard/publish already use, minus the mutation.
    window.location.reload()
  }

  async _discardDraft() {
    if (!this.activeElements.length) return
    // Discard every edited document's draft, not just one — the session
    // spanned all of them.
    await Promise.all(
      this.activeElements.map(el =>
        fetch(`${this.cmsBase}/api/documents/${el.dataset.cmsId}/discard-draft`, {
          method: 'POST',
          credentials: 'include',
        }),
      ),
    )
    // Reload to get the published state
    window.location.reload()
  }

  /**
   * The changeset Publish will ship, and what is in it, for the confirm.
   *
   * The one you are working in, else the CMS's default (Staging). The default is
   * what lets a phone, or any browser that has not picked a changeset, publish
   * what Claude drafted: with none remembered, Publish used to ship a single
   * post. Reads only; null when there is nothing to name.
   *
   * @returns {Promise<{id: string, title: string|null, count: number|null}|null>}
   */
  async _publishTarget() {
    const activeId = getActiveChangesetId()
    const url = activeId
      ? `${this.cmsBase}/api/changesets/${activeId}`
      : `${this.cmsBase}/api/changesets/default`
    let changeset = null
    try {
      const res = await fetch(url, { credentials: 'include' })
      if (res.ok) {
        const data = await res.json()
        changeset = activeId ? data : data?.changeset
      }
    } catch { /* fall through: the active id alone is still publishable */ }

    const count = changeset?.documents ? changeset.documents.length : null
    if (activeId) return { id: activeId, title: changeset?.title ?? null, count }
    // An empty default has nothing to ship; let the single-post fallback apply.
    if (!changeset?.id || count === 0) return null
    return { id: changeset.id, title: changeset.title ?? null, count }
  }

  async _publish() {
    // A session's edits are grouped into one changeset — publish the whole
    // changeset so everything ships at once and fires a single rebuild.
    // Publish directly (not via the shell's Review & Publish drawer, whose
    // styles live in editor.css and aren't loaded on the host site).
    const target = await this._publishTarget()
    const changesetId = target?.id ?? null

    const url = changesetId
      ? `${this.cmsBase}/api/changesets/${changesetId}/publish`
      : this.activeElements[0]
        ? `${this.cmsBase}/api/documents/${this.activeElements[0].dataset.cmsId}/publish`
        : null
    if (!url) return

    // Say what is about to go live: "Staging" is a bag, not a post.
    const name = target?.title ? `“${target.title}”` : changesetId ? 'your changeset' : 'your changes'
    const size = target?.count != null ? ` (${target.count} document${target.count === 1 ? '' : 's'})` : ''
    if (!window.confirm(`Publish ${name}${size}? The site will rebuild — it takes about 30 seconds to go live.`)) {
      return
    }

    this.setState('publishing')
    let ok = false
    let reason = ''
    try {
      const res = await fetch(url, { method: 'POST', credentials: 'include' })
      ok = res.ok
      // The server refuses to publish an invalid draft and says which one.
      if (!ok) reason = (await res.json().catch(() => null))?.error ?? ''
    } catch { ok = false }

    if (!ok) {
      // Back to editing so the drafts aren't lost and they can retry.
      this.setState('editing')
      window.alert(`Publish failed — nothing was published.${reason ? `\n\n${reason}` : ''}`)
      return
    }

    if (changesetId && changesetId === getActiveChangesetId()) setActiveChangesetId(null) // session shipped; start fresh
    this.setState('published')
    // In local dev, ping the Astro dev server to trigger a full-page reload
    if (this.reloadUrl) {
      try { await fetch(this.reloadUrl, { method: 'POST' }) } catch { /* ignore */ }
    }
  }

  // ── Chat panel wiring ──────────────────────────────────────────────────────

  /**
   * Wire a ChatPanel to the toolbar.
   * Also back-references the toolbar on the panel so it can call _onChatMessage().
   * Triggers a re-render so the 💬 button appears.
   * @param {import('../components/chat-panel.js').ChatPanel} chatPanel
   */
  setChatPanel(chatPanel) {
    this._chatPanel = chatPanel
    chatPanel.setToolbar(this)
    this._render()
  }

  /**
   * Called by ChatPanel when a 'token' event arrives while the panel is closed.
   * Increments the unread count and updates the badge on the 💬 button.
   */
  _onChatMessage() {
    if (!this._chatPanel?.isOpen) {
      this._unread = (this._unread || 0) + 1
      this._updateChatBadge()
    }
  }

  /**
   * Reflect the current unread count on the 💬 button label.
   */
  _updateChatBadge() {
    if (!this._chatBtn) return
    const count = this._unread || 0
    this._chatBtn.textContent = count > 0 ? `💬 Chat (${count})` : '💬 Chat'
  }

  // ── Peer presence ──────────────────────────────────────────────────────────

  /**
   * Rebuild the peer-presence strip from the current peers Map.
   * Inserts/updates a #astraeus-peer-presence container in the toolbar.
   *
   * @param {Map<string, {client_id: string, display: string, type?: string, client_type?: string}>} peersMap
   */
  updatePeers(peersMap) {
    // Inject CSS once
    if (!document.getElementById('astraeus-peer-styles')) {
      const style = document.createElement('style')
      style.id = 'astraeus-peer-styles'
      style.textContent = `
        .peer-indicator { font-size: 11px; margin: 0 3px; opacity: 0.85; }
        .peer-indicator.human { color: #a6e3a1; }
        .peer-indicator.ai { color: #cba6f7; }
        .peer-indicator.ai.pulsing { animation: ai-pulse 0.8s ease-in-out infinite; }
        @keyframes ai-pulse { 0%,100% { opacity: 0.85; } 50% { opacity: 0.3; } }
      `
      document.head.appendChild(style)
    }

    if (!this.el) return

    // Find or create the peer-presence container
    let peerSection = this.el.querySelector('#astraeus-peer-presence')
    if (!peerSection) {
      peerSection = document.createElement('div')
      peerSection.id = 'astraeus-peer-presence'
      peerSection.style.cssText = 'display:flex;flex-wrap:wrap;align-items:center;gap:2px;'
      this.el.appendChild(peerSection)
    }

    peerSection.innerHTML = ''
    for (const [clientId, peer] of peersMap) {
      const peerType = peer.type ?? peer.client_type ?? 'human'
      const span = document.createElement('span')
      span.className = `peer-indicator ${peerType}`
      span.dataset.clientId = clientId
      span.textContent = peerType === 'ai' ? `◆ ${peer.display}` : `● ${peer.display}`
      peerSection.appendChild(span)
    }
  }

  /**
   * Toggle the "pulsing" animation on an AI peer's indicator span.
   *
   * @param {string} clientId  The server-assigned client_id of the AI peer.
   * @param {boolean} isEditing  True to start pulsing; false to stop.
   */
  setAiEditing(clientId, isEditing) {
    const section = this.el?.querySelector('#astraeus-peer-presence')
    if (!section) return
    const span = section.querySelector(`[data-client-id="${clientId}"]`)
    if (!span) return
    if (isEditing) {
      span.classList.add('pulsing')
    } else {
      span.classList.remove('pulsing')
    }
  }
}
