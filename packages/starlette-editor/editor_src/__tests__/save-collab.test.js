// @vitest-environment happy-dom

/**
 * Save in the shell must never send a rich-text field that streams over collab.
 *
 * 2026-10-26: four drafts lost every body edit. The server logged ~3,000 steps,
 * and the step log replays cleanly onto the stored drafts, which were exactly
 * what the editor had loaded when the documents were opened. Save sent
 * state.formData, whose body field was still that loaded copy (the collab
 * field was only skipped when *flushing* the editor, not removed), so each
 * Save wrote it over the edits.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  showConfirm: vi.fn(),
  patchDocument: vi.fn(),
  fetchDocuments: vi.fn(),
  fetchDocument: vi.fn(),
}))

vi.mock('../components/confirm.js', () => ({ showConfirm: mocks.showConfirm }))
vi.mock('../components/toast.js', () => ({ showToast: vi.fn() }))
vi.mock('../prosemirror/mount.js', () => ({ destroyPmInstances: vi.fn() }))
vi.mock('../prosemirror/markdown.js', () => ({ pmDocToMarkdown: vi.fn(() => 'md') }))
vi.mock('../changeset-store.js', () => ({
  setActiveChangesetId: vi.fn(),
  getActiveChangesetId: vi.fn(() => null),
}))
vi.mock('../api.js', () => ({
  fetchDocuments: mocks.fetchDocuments,
  fetchDocument: mocks.fetchDocument,
  createDocument: vi.fn(),
  patchDocument: mocks.patchDocument,
  setDraftPublishState: vi.fn(),
  setDraftDeleted: vi.fn(),
  addDocToChangeset: vi.fn(),
}))

import { saveDocument, selectDoc, hasUnsavedBodyEdits } from '../standard/actions.js'
import { state } from '../state.js'

const LOADED = { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: 'loaded' }] }] }
const TYPED = { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: 'typed since' }] }] }

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  mocks.patchDocument.mockResolvedValue({
    data: { id: 'doc-1', body: { title: 'T2' }, slug: 's' },
    headers: { get: () => null },
  })
  mocks.fetchDocuments.mockResolvedValue({ documents: [], total: 0 })
  mocks.fetchDocument.mockResolvedValue({ id: 'doc-2', body: {}, slug: 's' })
  state.schema = { post: { field_meta: { body_markdown: { field_type: 'rich_text' } } } }
  state.activeType = 'post'
  state.activeDocId = 'doc-1'
  state.isSaving = false
  state.isDirty = true
  state.formData = { title: 'T2', body_markdown: LOADED, __slug: 's' }
  state.pmInstances = { body_markdown: { state: { doc: { toJSON: () => TYPED } } } }
  state.collabConnections = { body_markdown: { hasUnconfirmedSteps: () => false } }
})

describe('saveDocument with a live collab field', () => {
  it('does not send the body the editor loaded', async () => {
    await saveDocument()

    const [, patch] = mocks.patchDocument.mock.calls[0]
    expect(patch.body).toEqual({ title: 'T2' })
    expect(patch.body).not.toHaveProperty('body_markdown')
  })

  it('still sends a rich-text field that has no collab connection', async () => {
    state.collabConnections = {}

    await saveDocument()

    const [, patch] = mocks.patchDocument.mock.calls[0]
    expect(patch.body.body_markdown).toEqual(TYPED)
  })
})

describe('unconfirmed body edits', () => {
  it('count as unsaved, so switching documents asks first', async () => {
    state.isDirty = false
    state.collabConnections = { body_markdown: { hasUnconfirmedSteps: () => true } }
    mocks.showConfirm.mockResolvedValue(false)

    expect(hasUnsavedBodyEdits()).toBe(true)
    await selectDoc('doc-2')

    expect(mocks.showConfirm).toHaveBeenCalledOnce()
    expect(state.activeDocId).toBe('doc-1')
  })

  it('do not prompt once confirmed', async () => {
    state.isDirty = false
    await selectDoc('doc-2')
    expect(mocks.showConfirm).not.toHaveBeenCalled()
  })
})
