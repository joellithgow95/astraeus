/**
 * save-status.js — The shell header's answer to "is my work saved, and is it live?"
 *
 * A document is written two ways (ADR 027): rich-text fields stream over a
 * collab socket as steps, and plain fields save themselves through PATCH
 * (field-sync.js). Each reports here, and the header shows one status for the
 * whole document, worst first:
 *
 *   Not saved: <reason>     a refused save, or a body the server could not store
 *   Offline — …             the socket is down with typed text not yet confirmed
 *   Saving…                 something is on its way
 *   Saved just now / 3 min ago / at 10:42
 *
 * Saving is not publishing. A published document's saved edits are a draft
 * the live site does not show, so the header also says so and offers to
 * publish them (`#draft-state`, painted by paintDraftState).
 */

import { state } from './state.js'

/** @typedef {{ error?: string|null, connected?: boolean, connecting?: boolean, pending?: boolean }} BodyReport */

/** @type {Record<string, BodyReport>} rich-text field → its connection's state */
let _bodies = {}
/** @type {{ error: string|null, pending: boolean }} the plain fields' state */
let _fields = { error: null, pending: false }
/** @type {Date|null} when the server last confirmed something this tab sent */
let _lastSavedAt = null
let _ticker = null

/**
 * Record a rich-text field's connection state and repaint.
 *
 * @param {string} field
 * @param {BodyReport} report  merged into what is known about the field
 */
export function reportBody(field, report) {
  _bodies[field] = { connected: true, pending: false, error: null, ..._bodies[field], ...report }
  paintSaveStatus()
}

/** Forget a rich-text field (its editor was destroyed). */
export function forgetBody(field) {
  delete _bodies[field]
  paintSaveStatus()
}

/**
 * Record the plain fields' state and repaint.
 *
 * @param {{ error?: string|null, pending?: boolean }} report
 */
export function reportFields(report) {
  _fields = { ..._fields, ...report }
  paintSaveStatus()
}

/**
 * The server confirmed an edit from this tab: stamp the time and, on a
 * published document, note that it now has a draft the live site lacks.
 *
 * @param {Date} [at]
 */
export function noteSaved(at = new Date()) {
  _lastSavedAt = at
  if (state.activeDoc && !state.activeDoc.has_draft) {
    state.activeDoc = { ...state.activeDoc, has_draft: true }
  }
  paintSaveStatus()
  paintDraftState()
}

/** A different document was opened: start over. */
export function resetSaveStatus() {
  _bodies = {}
  _fields = { error: null, pending: false }
  _lastSavedAt = null
  paintSaveStatus()
}

/**
 * "just now", "40 s ago", "3 min ago", or "at 10:42".
 *
 * @param {Date} at
 * @param {Date} now
 */
export function formatSavedAgo(at, now = new Date()) {
  const secs = Math.max(0, Math.round((now - at) / 1000))
  if (secs < 10) return 'just now'
  if (secs < 60) return `${secs} s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins} min ago`
  return `at ${at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
}

/**
 * The one status for the document. Pure, for tests.
 *
 * @param {{ bodies: Record<string, BodyReport>, fields: {error: string|null, pending: boolean},
 *           lastSavedAt: Date|null, now?: Date }} input
 * @returns {{ kind: 'error'|'offline'|'saving'|'saved', text: string, title: string }}
 */
export function summarize({ bodies, fields, lastSavedAt, now = new Date() }) {
  const reports = Object.values(bodies || {})
  const savedAt = lastSavedAt
    ? `Last saved ${lastSavedAt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}.`
    : ''

  const bodyError = reports.find(r => r.error)?.error
  const error = bodyError || fields?.error
  if (error) {
    return {
      kind: 'error',
      // Body errors arrive with their own "⚠ Body not saved — …" wording.
      text: bodyError ? bodyError.replace(/^⚠\s*/, '') : `Not saved: ${error}`,
      title: [error, savedAt].filter(Boolean).join(' '),
    }
  }
  if (reports.some(r => !r.connected && !r.connecting && r.pending)) {
    return {
      kind: 'offline',
      text: 'Offline — your latest edits are kept in this browser until it reconnects',
      title: ['Reconnecting to the server. Typing is safe; it saves once the connection is back.', savedAt]
        .filter(Boolean).join(' '),
    }
  }
  if (fields?.pending || reports.some(r => r.pending)) {
    return { kind: 'saving', text: 'Saving…', title: savedAt }
  }
  if (reports.some(r => r.connecting)) {
    return { kind: 'saving', text: 'Connecting…', title: 'Opening the live connection for the body.' }
  }
  const reconnecting = reports.some(r => !r.connected)
  const base = lastSavedAt ? `Saved ${formatSavedAgo(lastSavedAt, now)}` : 'Saved'
  return {
    kind: 'saved',
    text: reconnecting ? `${base} · reconnecting…` : base,
    title: [
      savedAt || 'Everything you see is saved.',
      'Edits save as you type; there is no Save button.',
    ].join(' '),
  }
}

/** Paint `#save-status` and the dirty dot from what has been reported. */
export function paintSaveStatus() {
  const el = document.getElementById('save-status')
  if (!el) return
  const { kind, text, title } = summarize({ bodies: _bodies, fields: _fields, lastSavedAt: _lastSavedAt })
  el.textContent = text
  el.className = `save-status save-status--${kind}`
  el.title = title
  el.dataset.kind = kind
  const dot = document.getElementById('dirty-dot')
  if (dot) {
    dot.classList.toggle('is-visible', kind !== 'saved')
    dot.classList.toggle('is-saving', kind === 'saving')
  }
  // "3 min ago" goes stale: repaint while there is a time on screen.
  if (_lastSavedAt && !_ticker) {
    _ticker = setInterval(() => {
      if (!document.getElementById('save-status')) {
        clearInterval(_ticker)
        _ticker = null
        return
      }
      paintSaveStatus()
    }, 15000)
  }
}

/**
 * What the header says about publishing. Pure, for tests.
 *
 * @param {object|null} doc  the active document
 * @param {{ changesetTitle?: string|null }} [opts]
 * @returns {null|{ text: string, title: string, action: string }}
 */
export function draftState(doc, { changesetTitle = null } = {}) {
  if (!doc || !doc.published || !doc.has_draft || doc.draft_deleted) return null
  if (doc.draft_published === false) return null  // already staged to unpublish
  const where = changesetTitle ? `"${changesetTitle}"` : 'its changeset (normally Staging)'
  return {
    text: 'Unpublished changes',
    title:
      'Your edits are saved as a draft. The live site still shows the published version ' +
      `until you publish: here for this document alone, or with ${where} from 📋.`,
    action: 'Publish changes',
  }
}

/** Show or hide `#draft-state` (the pill and its Publish changes button). */
export function paintDraftState() {
  const wrap = document.getElementById('draft-state')
  if (!wrap) return
  const info = draftState(state.activeDoc, { changesetTitle: state.activeChangesetTitle })
  wrap.style.display = info ? '' : 'none'
  if (!info) return
  const label = wrap.querySelector('[data-draft-label]')
  if (label) {
    label.textContent = info.text
    label.title = info.title
  }
  const btn = wrap.querySelector('button')
  if (btn && !btn.disabled) btn.textContent = info.action
}
