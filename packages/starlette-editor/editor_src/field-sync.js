/**
 * field-sync.js — Live plain fields for the shell (ADR 027).
 *
 * Every field is live. Rich text streams over the collab socket as steps;
 * everything else (title, tagline, tags, image, dates, refs, slices, slug) is
 * last-writer-wins per field: a field saves itself shortly after the person
 * stops changing it, and a change made anywhere else (another tab, another
 * person, Claude over MCP) arrives on the document-events socket and shows up
 * in the form. There is no Save button for an existing document.
 *
 * Sending: a timer compares the form with the values last saved. When a field
 * differs and has held still for one tick, its new value goes out in a PATCH
 * of only the changed fields, tagged with this tab's client id so the echo is
 * ignored. A refused save (a required field left empty, an invalid value) is
 * shown, never dropped: the field stays unsaved and is retried when it changes.
 *
 * Receiving: a remote value replaces the form's value unless this tab has an
 * unsaved change to the same field, in which case this tab's save, coming
 * later, wins. A field the person is typing in is never rewritten under them.
 */

import { state, setState } from './state.js'
import { patchDocument } from './api.js'
import { clearDraft, draftKey } from './draft-buffer.js'

/** How often the form is compared with the saved values (ms). */
export const TICK_MS = 600

/** This tab's id, sent with every save so its own broadcast can be ignored. */
export const CLIENT_ID = (globalThis.crypto?.randomUUID?.() ?? `tab-${Math.random().toString(36).slice(2)}`)

let _timer = null
let _lastDiff = null
let _inflight = null
let _pendingRender = false

/**
 * The fields that save through this module: every form field except rich
 * text with a live collab connection, which saves as steps.
 *
 * @param {string} name
 * @returns {boolean}
 */
function isPlainField(name) {
  const meta = (state.schema?.[state.activeType]?.field_meta || {})[name] || {}
  return !(meta.field_type === 'rich_text' && state.collabConnections?.[name])
}

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

/**
 * Plain fields whose form value differs from the last saved one.
 *
 * @returns {Record<string, *>}
 */
export function unsavedFields() {
  const saved = state.savedFields || {}
  const diff = {}
  for (const [name, value] of Object.entries(state.formData || {})) {
    if (!isPlainField(name)) continue
    if (!same(value, saved[name])) diff[name] = value
  }
  return diff
}

/**
 * Remember *formData* as what the server holds. Called when a document loads.
 *
 * @param {object} formData
 */
export function markSaved(formData) {
  state.savedFields = JSON.parse(JSON.stringify(formData || {}))
  state.fieldSaveError = null
}

/** Show the save status in the header without a full re-render. */
function paintStatus() {
  const el = document.getElementById('save-status')
  if (!el) return
  let text = 'Saved'
  let cls = 'save-status'
  if (state.fieldSaveError) {
    text = `Not saved: ${state.fieldSaveError}`
    cls += ' save-status--error'
  } else if (_inflight || Object.keys(unsavedFields()).length) {
    text = 'Saving…'
  }
  el.textContent = text
  el.className = cls
  el.title = state.fieldSaveError || ''
  const dot = document.getElementById('dirty-dot')
  if (dot) dot.classList.toggle('is-visible', text !== 'Saved')
}

/**
 * Save every unsaved plain field of the active document now.
 *
 * @returns {Promise<boolean>} true when nothing is left unsaved
 */
export async function flushFields() {
  if (_inflight) await _inflight
  const docId = state.activeDocId
  if (!docId) return true
  const diff = unsavedFields()
  if (!Object.keys(diff).length) {
    state.isDirty = false
    paintStatus()
    return true
  }

  const { __slug, ...body } = diff
  const patch = {}
  if (Object.keys(body).length) patch.body = body
  if (__slug !== undefined) patch.slug = __slug || ''

  _inflight = (async () => {
    try {
      const { data, headers } = await patchDocument(docId, patch, {
        activeChangesetId: state.activeChangesetId || undefined,
        clientId: CLIENT_ID,
      })
      if (state.activeDocId !== docId) return true
      state.savedFields = { ...state.savedFields, ...diff }
      state.fieldSaveError = null
      state.activeDoc = { ...(state.activeDoc || {}), ...data }
      const newCsId = headers?.get?.('x-changeset-id')
      if (newCsId) {
        setState({
          activeChangesetId: newCsId,
          activeChangesetTitle: headers.get('x-changeset-title') || 'Untitled',
        }, false)
      }
      const left = Object.keys(unsavedFields()).length
      state.isDirty = left > 0
      if (!left) clearDraft(draftKey(docId, state.activeType))
      return left === 0
    } catch (err) {
      state.fieldSaveError = describe(err)
      return false
    } finally {
      _inflight = null
      paintStatus()
    }
  })()
  paintStatus()
  return _inflight
}

/**
 * A short reason a save was refused, from an ApiError's body.
 *
 * @param {*} err
 * @returns {string}
 */
function describe(err) {
  const detail = err?.data?.detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    const where = Array.isArray(first.loc) ? first.loc.filter(p => p !== 'body').join('.') : ''
    return `${where ? `${where}: ` : ''}${first.msg || 'invalid value'}`
  }
  return err?.data?.error || err?.message || 'the server refused the change'
}

/** One tick: save the fields that differ and have held still since the last tick. */
function tick() {
  if (!state.activeDocId || _inflight) return
  const diff = unsavedFields()
  const key = JSON.stringify(diff)
  if (!Object.keys(diff).length) {
    _lastDiff = null
    return
  }
  // A refused value is retried only once it changes.
  if (state.fieldSaveError && key === state.fieldSaveErrorFor) return
  if (key === _lastDiff) {
    _lastDiff = null
    flushFields().then(ok => {
      if (!ok) state.fieldSaveErrorFor = key
    })
  } else {
    _lastDiff = key
    paintStatus()
  }
}

/** Start the save timer. Idempotent. */
export function startFieldSync() {
  if (_timer) return
  _timer = setInterval(tick, TICK_MS)
  document.addEventListener('focusout', () => {
    // A remote change that arrived while the person was in the form waits for
    // them to leave it, so a re-render never moves their cursor.
    if (!_pendingRender) return
    setTimeout(() => {
      if (!formHasFocus()) {
        _pendingRender = false
        setState({})
      }
    }, 0)
  })
}

/** Stop the save timer (tests). */
export function stopFieldSync() {
  clearInterval(_timer)
  _timer = null
  _lastDiff = null
}

function formHasFocus() {
  const form = document.getElementById('form-area')
  return !!(form && document.activeElement && form.contains(document.activeElement))
}

/**
 * Apply a `document.fields` event from the CMS to the open document.
 *
 * @param {{document_id: string, fields?: object, slug?: string, client_id?: string|null}} msg
 * @returns {string[]} the fields that changed in the form
 */
export function applyRemoteFields(msg) {
  if (!msg || msg.document_id !== state.activeDocId) return []
  if (msg.client_id && msg.client_id === CLIENT_ID) return []

  const incoming = { ...(msg.fields || {}) }
  if (msg.slug !== undefined) incoming.__slug = msg.slug
  const saved = { ...(state.savedFields || {}) }
  const form = { ...state.formData }
  const changed = []
  for (const [name, value] of Object.entries(incoming)) {
    const localUnsaved = !same(form[name], saved[name])
    saved[name] = value
    // This tab's own unsaved change to the field goes out later and wins.
    if (localUnsaved || same(form[name], value)) continue
    form[name] = value
    changed.push(name)
  }
  state.savedFields = saved
  if (!changed.length) return []
  state.formData = form

  // A plain input the person is not in can take the value directly; anything
  // else re-renders, or waits until they leave the form.
  // Every field is visited (map, not some), so plain inputs update even when
  // another field needs a re-render.
  const needsRender = changed.map(name => {
    const input = document.getElementById(name === '__slug' ? 'field-slug' : `field-${name}`)
    if (!input || !('value' in input) || input.type === 'checkbox') return true
    if (input === document.activeElement) return true
    input.value = form[name] ?? ''
    return false
  }).some(Boolean)
  if (needsRender) {
    if (formHasFocus()) _pendingRender = true
    else setState({})
  }
  paintStatus()
  return changed
}
