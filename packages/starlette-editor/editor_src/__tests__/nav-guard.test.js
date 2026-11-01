// @vitest-environment happy-dom

/**
 * Tests for the unsaved-edit guards on selectType and selectDoc, and for how
 * they interact with the local draft buffer.
 *
 * Both actions replace state.formData wholesale, so a dirty document has to
 * prompt before they run — otherwise the edits are gone with no way back.
 * draft-buffer.js is deliberately not mocked: the point of these tests is the
 * wiring between the two.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  showConfirm: vi.fn(),
  fetchDocuments: vi.fn(),
  fetchDocument: vi.fn(),
  destroyPmInstances: vi.fn(),
  patchDocument: vi.fn(),
}))

vi.mock('../components/confirm.js', () => ({ showConfirm: mocks.showConfirm }))
vi.mock('../components/toast.js', () => ({ showToast: vi.fn() }))
vi.mock('../prosemirror/mount.js', () => ({ destroyPmInstances: mocks.destroyPmInstances }))
vi.mock('../prosemirror/markdown.js', () => ({ pmDocToMarkdown: vi.fn() }))
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

import { selectType, selectDoc, saveDocument, onFieldChange } from '../standard/actions.js'
import { draftKey, writeDraft, loadDraft } from '../draft-buffer.js'
import { state } from '../state.js'

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  state.pmInstances = {}
  state.collabConnections = {}
  mocks.patchDocument.mockResolvedValue({
    data: { id: 'doc-1', body: { title: 'saved' }, slug: 'saved' },
    headers: { get: () => null },
  })
  mocks.fetchDocuments.mockResolvedValue({ documents: [], total: 0 })
  mocks.fetchDocument.mockResolvedValue({ id: 'doc-2', body: { title: 'saved' }, slug: 'saved' })

  state.activeType = 'post'
  state.activeDocId = 'doc-1'
  state.formData = { title: 'unsaved work' }
  state.savedFields = { title: 'unsaved work' }
  state.fieldSaveError = null
  state.isDirty = false
})

/** The edits are unsaved and the server will not take them (offline, refused). */
function unsavable() {
  state.isDirty = true
  state.savedFields = { title: 'saved' }
  mocks.patchDocument.mockRejectedValue(new Error('offline'))
}

describe('selectType', () => {
  it('does not prompt when the document is clean', async () => {
    await selectType('page')

    expect(mocks.showConfirm).not.toHaveBeenCalled()
    expect(state.activeType).toBe('page')
  })

  it('keeps the edits when the user cancels', async () => {
    unsavable()
    mocks.showConfirm.mockResolvedValue(false)

    await selectType('page')

    expect(mocks.showConfirm).toHaveBeenCalledOnce()
    expect(state.activeType).toBe('post')
    expect(state.formData).toEqual({ title: 'unsaved work' })
    expect(state.isDirty).toBe(true)
    expect(mocks.destroyPmInstances).not.toHaveBeenCalled()
  })

  it('discards the edits when the user confirms', async () => {
    unsavable()
    mocks.showConfirm.mockResolvedValue(true)

    await selectType('page')

    expect(state.activeType).toBe('page')
    expect(state.formData).toEqual({})
    expect(state.isDirty).toBe(false)
  })
})

describe('selectDoc', () => {
  it('does not prompt when the document is clean', async () => {
    await selectDoc('doc-2')

    expect(mocks.showConfirm).not.toHaveBeenCalled()
    expect(state.activeDocId).toBe('doc-2')
  })

  it('keeps the edits when the user cancels', async () => {
    unsavable()
    mocks.showConfirm.mockResolvedValue(false)

    await selectDoc('doc-2')

    expect(mocks.showConfirm).toHaveBeenCalledOnce()
    expect(state.activeDocId).toBe('doc-1')
    expect(state.formData).toEqual({ title: 'unsaved work' })
    expect(state.isDirty).toBe(true)
    expect(mocks.fetchDocument).not.toHaveBeenCalled()
  })

  it('discards the edits when the user confirms', async () => {
    unsavable()
    mocks.showConfirm.mockResolvedValue(true)

    await selectDoc('doc-2')

    expect(state.activeDocId).toBe('doc-2')
    expect(state.formData).toEqual({ title: 'saved', __slug: 'saved' })
    expect(state.isDirty).toBe(false)
  })

  it('saves unsaved fields instead of asking, when the server takes them', async () => {
    state.isDirty = true
    state.savedFields = { title: 'saved' }

    await selectDoc('doc-2')

    expect(mocks.showConfirm).not.toHaveBeenCalled()
    const [id, patch] = mocks.patchDocument.mock.calls[0]
    expect(id).toBe('doc-1')
    expect(patch.body).toEqual({ title: 'unsaved work' })
    expect(state.activeDocId).toBe('doc-2')
  })

  it('does not prompt when reselecting the active document', async () => {
    state.isDirty = true

    await selectDoc('doc-1')

    expect(mocks.showConfirm).not.toHaveBeenCalled()
    expect(state.formData).toEqual({ title: 'unsaved work' })
  })
})

describe('draft buffer integration', () => {
  it('drops the buffer when the user confirms a discard', async () => {
    const key = draftKey('doc-1', 'post')
    writeDraft(key, { title: 'unsaved work' })
    unsavable()
    mocks.showConfirm.mockResolvedValue(true)

    await selectDoc('doc-2')

    expect(loadDraft(key)).toBeNull()
  })

  it('keeps the buffer when the user cancels a discard', async () => {
    const key = draftKey('doc-1', 'post')
    writeDraft(key, { title: 'unsaved work' })
    unsavable()
    mocks.showConfirm.mockResolvedValue(false)

    await selectDoc('doc-2')

    expect(loadDraft(key).formData).toEqual({ title: 'unsaved work' })
  })

  it('offers a surviving buffer and restores it on accept', async () => {
    writeDraft(draftKey('doc-2', 'post'), { title: 'recovered' })
    mocks.showConfirm.mockResolvedValue(true)

    await selectDoc('doc-2')

    expect(state.formData).toEqual({ title: 'recovered' })
    expect(state.isDirty).toBe(true)
  })

  it('drops the buffer and keeps the server copy when restore is declined', async () => {
    const key = draftKey('doc-2', 'post')
    writeDraft(key, { title: 'recovered' })
    mocks.showConfirm.mockResolvedValue(false)

    await selectDoc('doc-2')

    expect(state.formData).toEqual({ title: 'saved', __slug: 'saved' })
    expect(state.isDirty).toBe(false)
    expect(loadDraft(key)).toBeNull()
  })

  it('does not prompt when no buffer survived', async () => {
    await selectDoc('doc-2')

    expect(mocks.showConfirm).not.toHaveBeenCalled()
    expect(state.formData).toEqual({ title: 'saved', __slug: 'saved' })
  })

  it('clears the buffer after a successful save', async () => {
    const key = draftKey('doc-1', 'post')
    onFieldChange('title', 'edited')
    writeDraft(key, state.formData)

    await saveDocument()

    expect(loadDraft(key)).toBeNull()
  })
})
