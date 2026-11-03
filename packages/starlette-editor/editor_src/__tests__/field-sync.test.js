// @vitest-environment happy-dom

/**
 * Live plain fields (ADR 027): an existing document saves each changed field
 * on its own, and a field changed elsewhere shows up in the form.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ patchDocument: vi.fn(), render: vi.fn() }))

vi.mock('../api.js', () => ({ patchDocument: mocks.patchDocument }))

import {
  CLIENT_ID,
  TICK_MS,
  applyRemoteFields,
  flushFields,
  markSaved,
  startFieldSync,
  stopFieldSync,
  unsavedFields,
} from '../field-sync.js'
import { state, setRenderFn } from '../state.js'

const BODY = { type: 'doc', content: [] }

beforeEach(() => {
  vi.clearAllMocks()
  setRenderFn(mocks.render)
  document.body.innerHTML = `
    <span id="dirty-dot"></span><span id="save-status"></span>
    <div id="form-area">
      <input id="field-title" value="Old"><input id="field-slug" value="old">
    </div>`
  state.schema = { post: { field_meta: { body_markdown: { field_type: 'rich_text' } } } }
  state.activeType = 'post'
  state.activeDocId = 'doc-1'
  state.activeDoc = { id: 'doc-1' }
  state.collabConnections = { body_markdown: {} }
  state.formData = { title: 'Old', tags: ['a'], body_markdown: BODY, __slug: 'old' }
  markSaved(state.formData)
  mocks.patchDocument.mockResolvedValue({ data: { id: 'doc-1' }, headers: { get: () => null } })
})

afterEach(() => {
  stopFieldSync()
  vi.useRealTimers()
})

describe('saving', () => {
  it('sends only the changed plain fields, tagged with this tab', async () => {
    state.formData = { ...state.formData, title: 'New', __slug: 'new', body_markdown: { x: 1 } }

    expect(await flushFields()).toBe(true)

    const [id, patch, opts] = mocks.patchDocument.mock.calls[0]
    expect(id).toBe('doc-1')
    expect(patch).toEqual({ body: { title: 'New' }, slug: 'new' })
    expect(opts.clientId).toBe(CLIENT_ID)
    expect(unsavedFields()).toEqual({})
    expect(document.getElementById('save-status').textContent).toBe('Saved just now')
  })

  it('shows a refused save and keeps the field unsaved', async () => {
    mocks.patchDocument.mockRejectedValue({
      data: { error: 'Validation failed', detail: [{ loc: ['title'], msg: 'Field required' }] },
    })
    state.formData = { ...state.formData, title: '' }

    expect(await flushFields()).toBe(false)

    const status = document.getElementById('save-status')
    expect(status.textContent).toBe('Not saved: title: Field required')
    expect(status.className).toContain('save-status--error')
    expect(unsavedFields()).toEqual({ title: '' })
  })

  it('saves a field once it has held still for a tick', async () => {
    vi.useFakeTimers()
    startFieldSync()
    state.formData = { ...state.formData, title: 'N' }
    await vi.advanceTimersByTimeAsync(TICK_MS)
    expect(mocks.patchDocument).not.toHaveBeenCalled()

    state.formData = { ...state.formData, title: 'New' }
    await vi.advanceTimersByTimeAsync(TICK_MS)
    expect(mocks.patchDocument).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(TICK_MS)
    expect(mocks.patchDocument).toHaveBeenCalledOnce()
    expect(mocks.patchDocument.mock.calls[0][1]).toEqual({ body: { title: 'New' } })
  })
})

describe('receiving', () => {
  it('shows a field changed elsewhere', () => {
    const changed = applyRemoteFields({
      document_id: 'doc-1',
      fields: { title: 'From Claude' },
      client_id: 'someone-else',
    })

    expect(changed).toEqual(['title'])
    expect(state.formData.title).toBe('From Claude')
    expect(document.getElementById('field-title').value).toBe('From Claude')
    expect(unsavedFields()).toEqual({})
    expect(mocks.render).not.toHaveBeenCalled()
  })

  it('ignores its own echo and other documents', () => {
    expect(applyRemoteFields({ document_id: 'doc-1', fields: { title: 'x' }, client_id: CLIENT_ID })).toEqual([])
    expect(applyRemoteFields({ document_id: 'doc-2', fields: { title: 'x' } })).toEqual([])
    expect(state.formData.title).toBe('Old')
  })

  it('keeps an unsaved local change to the same field, which is sent later and wins', () => {
    state.formData = { ...state.formData, title: 'Mine' }

    applyRemoteFields({ document_id: 'doc-1', fields: { title: 'Theirs' } })

    expect(state.formData.title).toBe('Mine')
    expect(unsavedFields()).toEqual({ title: 'Mine' })
  })

  it('never rewrites the input the person is typing in', () => {
    const input = document.getElementById('field-title')
    input.focus()

    applyRemoteFields({ document_id: 'doc-1', fields: { title: 'Theirs' } })

    expect(input.value).toBe('Old')
    expect(mocks.render).not.toHaveBeenCalled()
  })

  it('re-renders for a field with no plain input', () => {
    applyRemoteFields({ document_id: 'doc-1', fields: { tags: ['a', 'b'] }, slug: 'new' })

    expect(state.formData.tags).toEqual(['a', 'b'])
    expect(document.getElementById('field-slug').value).toBe('new')
    expect(mocks.render).toHaveBeenCalledOnce()
  })
})
