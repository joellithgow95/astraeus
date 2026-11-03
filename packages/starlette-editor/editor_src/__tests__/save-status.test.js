// @vitest-environment happy-dom

/**
 * The header's save status and the "Unpublished changes" pill (save-status.js).
 *
 * 2026-11-03: an edit to a published post's body saved as a draft, but nothing
 * in the shell said so: no time it was saved, no hint the live site still
 * showed the old text, no way to publish it from the document.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'

import {
  draftState,
  formatSavedAgo,
  noteSaved,
  paintDraftState,
  reportBody,
  reportFields,
  resetSaveStatus,
  summarize,
} from '../save-status.js'
import { state } from '../state.js'

const NOW = new Date('2026-11-03T17:00:00Z')
const ago = secs => new Date(NOW - secs * 1000)
const quiet = { error: null, pending: false }
const online = { connected: true, pending: false, error: null }

describe('formatSavedAgo', () => {
  it('reads as a person would say it', () => {
    expect(formatSavedAgo(ago(3), NOW)).toBe('just now')
    expect(formatSavedAgo(ago(42), NOW)).toBe('42 s ago')
    expect(formatSavedAgo(ago(180), NOW)).toBe('3 min ago')
    expect(formatSavedAgo(ago(7200), NOW)).toMatch(/^at /)
  })
})

describe('summarize', () => {
  it('says when the last save landed', () => {
    const s = summarize({ bodies: { body: online }, fields: quiet, lastSavedAt: ago(120), now: NOW })
    expect(s).toMatchObject({ kind: 'saved', text: 'Saved 2 min ago' })
    expect(s.title).toContain('Last saved')
  })

  it('is saving while the body has unconfirmed steps or a field is on its way', () => {
    expect(summarize({ bodies: { body: { ...online, pending: true } }, fields: quiet, lastSavedAt: null }).kind)
      .toBe('saving')
    expect(summarize({ bodies: {}, fields: { error: null, pending: true }, lastSavedAt: null }).text)
      .toBe('Saving…')
  })

  it('is offline, not "saving", when the socket is down with typed text', () => {
    const s = summarize({ bodies: { body: { connected: false, pending: true } }, fields: quiet, lastSavedAt: null })
    expect(s.kind).toBe('offline')
    expect(s.text).toContain('kept in this browser')
  })

  it('stays saved while reconnecting with nothing pending', () => {
    const s = summarize({ bodies: { body: { connected: false, pending: false } }, fields: quiet, lastSavedAt: ago(5), now: NOW })
    expect(s).toMatchObject({ kind: 'saved', text: 'Saved just now · reconnecting…' })
  })

  it('shows connecting before the body socket first opens', () => {
    const s = summarize({ bodies: { body: { connected: false, connecting: true } }, fields: quiet, lastSavedAt: null })
    expect(s.text).toBe('Connecting…')
  })

  it('puts an error above everything else', () => {
    const body = summarize({
      bodies: { body: { connected: false, pending: true, error: '⚠ Body not saved — session expired' } },
      fields: { error: null, pending: true },
      lastSavedAt: null,
    })
    expect(body).toMatchObject({ kind: 'error', text: 'Body not saved — session expired' })
    const field = summarize({ bodies: { body: online }, fields: { error: 'title: Field required', pending: false }, lastSavedAt: null })
    expect(field.text).toBe('Not saved: title: Field required')
  })
})

describe('draftState', () => {
  const live = { id: 'd', published: true, has_draft: true }

  it('offers to publish a published document with saved edits', () => {
    const info = draftState(live, { changesetTitle: 'Staging' })
    expect(info).toMatchObject({ text: 'Unpublished changes', action: 'Publish changes' })
    expect(info.title).toContain('live site still shows the published version')
    expect(info.title).toContain('"Staging"')
  })

  it('says nothing when there is nothing to publish', () => {
    expect(draftState(null)).toBeNull()
    expect(draftState({ ...live, has_draft: false })).toBeNull()
    expect(draftState({ ...live, published: false })).toBeNull()  // the Draft toggle covers it
    expect(draftState({ ...live, draft_deleted: true })).toBeNull()
    expect(draftState({ ...live, draft_published: false })).toBeNull()
  })
})

describe('painting', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <span id="dirty-dot"></span><span id="save-status"></span>
      <div id="draft-state" style="display:none"><span data-draft-label></span><button>x</button></div>`
    state.activeDoc = { id: 'd', published: true, has_draft: false }
    state.activeChangesetTitle = null
    resetSaveStatus()
  })

  it('shows the worst state across the body and the plain fields', () => {
    reportBody('body', { connected: true, pending: true })
    expect(document.getElementById('save-status').textContent).toBe('Saving…')
    expect(document.getElementById('dirty-dot').classList.contains('is-saving')).toBe(true)

    reportBody('body', { pending: false })
    reportFields({ error: 'slug taken' })
    const el = document.getElementById('save-status')
    expect(el.textContent).toBe('Not saved: slug taken')
    expect(el.className).toContain('save-status--error')
  })

  it('a confirmed save on a published document shows the pill', () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    reportBody('body', online)
    noteSaved(NOW)

    expect(document.getElementById('save-status').textContent).toBe('Saved just now')
    expect(state.activeDoc.has_draft).toBe(true)
    const wrap = document.getElementById('draft-state')
    expect(wrap.style.display).toBe('')
    expect(wrap.querySelector('button').textContent).toBe('Publish changes')

    vi.setSystemTime(new Date(+NOW + 5 * 60_000))
    vi.advanceTimersByTime(15_000)
    expect(document.getElementById('save-status').textContent).toBe('Saved 5 min ago')
    vi.useRealTimers()
  })

  it('hides the pill once the draft is published', () => {
    state.activeDoc = { id: 'd', published: true, has_draft: true }
    paintDraftState()
    expect(document.getElementById('draft-state').style.display).toBe('')
    state.activeDoc = { ...state.activeDoc, has_draft: false }
    paintDraftState()
    expect(document.getElementById('draft-state').style.display).toBe('none')
  })
})
