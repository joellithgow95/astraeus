// @vitest-environment happy-dom
/**
 * Tests for components/document-ref-list.js — the ListField(DocumentRef) picker.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const SONGS = [
  { id: 's1', slug: 'harvest-moon', body: { track: 'Harvest Moon' } },
  { id: 's2', slug: 'heart-of-gold', body: { track: 'Heart of Gold' } },
  { id: 's3', slug: 'old-man', body: { track: 'Old Man' } },
]

vi.mock('../api.js', () => ({
  fetchDocumentsByIds: vi.fn(async ids => SONGS.filter(s => ids.includes(s.id))),
  searchDocuments: vi.fn(async (_type, q) =>
    SONGS.filter(s => !q || s.body.track.toLowerCase().includes(q.toLowerCase()))),
}))

import { fetchDocumentsByIds, searchDocuments } from '../api.js'
import { buildDocumentRefList, moveItem } from '../components/document-ref-list.js'
import { fieldWidget } from '../components/fields.js'

const META = { field_type: 'document_ref_list', ref_block_type: 'song', ref_label_field: 'track' }
const flush = () => new Promise(r => setTimeout(r, 0))

async function mount(value, meta = META) {
  const onFieldChange = vi.fn()
  const root = buildDocumentRefList('songs', meta, value, onFieldChange)
  document.body.innerHTML = ''
  document.body.appendChild(root)
  await flush()
  const titles = () => [...root.querySelectorAll('.ref-list__title')].map(t => t.textContent)
  const input = root.querySelector('.ref-list__search')
  const options = () => [...root.querySelectorAll('.ref-list__menu .tags-input__option')]
  const last = () => onFieldChange.mock.calls.at(-1)[1]
  return { root, onFieldChange, titles, input, options, last }
}

beforeEach(() => { vi.clearAllMocks() })

describe('dispatch', () => {
  it('document_ref_list gets its own widget', () => {
    expect(fieldWidget('songs', { type: 'array' }, { field_type: 'document_ref_list' })).toBe('document_ref_list')
  })
})

describe('moveItem', () => {
  it('swaps neighbours and ignores moves off either end', () => {
    expect(moveItem(['a', 'b', 'c'], 0, 1)).toEqual(['b', 'a', 'c'])
    expect(moveItem(['a', 'b', 'c'], 2, -1)).toEqual(['a', 'c', 'b'])
    expect(moveItem(['a', 'b'], 0, -1)).toEqual(['a', 'b'])
  })
})

describe('buildDocumentRefList', () => {
  it('shows chosen documents in stored order, by their label field, from one lookup', async () => {
    const { titles } = await mount(['s3', 's1'])
    expect(titles()).toEqual(['Old Man', 'Harvest Moon'])
    expect(fetchDocumentsByIds).toHaveBeenCalledTimes(1)
    expect(fetchDocumentsByIds).toHaveBeenCalledWith(['s3', 's1'])
  })

  it('keeps an id whose document is gone, marked missing', async () => {
    const { root, titles, onFieldChange } = await mount(['s1', 'gone'])
    expect(titles()).toEqual(['Harvest Moon', 'gone'])
    expect(root.querySelectorAll('.ref-list__row.is-missing')).toHaveLength(1)
    expect(onFieldChange).not.toHaveBeenCalled()
  })

  it('does not call ids missing when the lookup failed', async () => {
    fetchDocumentsByIds.mockRejectedValueOnce(new Error('offline'))
    const { root, titles } = await mount(['s1'])
    expect(titles()).toEqual(['s1'])
    expect(root.querySelector('.is-missing')).toBeNull()
  })

  it('moves and removes rows', async () => {
    const { root, titles, last } = await mount(['s1', 's2', 's3'])
    root.querySelectorAll('.ref-list__row')[0].querySelector('[title="Move down"]').click()
    expect(titles()).toEqual(['Heart of Gold', 'Harvest Moon', 'Old Man'])
    expect(last()).toEqual(['s2', 's1', 's3'])

    root.querySelectorAll('.ref-list__remove')[2].click()
    expect(last()).toEqual(['s2', 's1'])
  })

  it('searches the target type and adds the picked document', async () => {
    vi.useFakeTimers()
    try {
      const onFieldChange = vi.fn()
      const root = buildDocumentRefList('songs', META, ['s1'], onFieldChange)
      document.body.innerHTML = ''
      document.body.appendChild(root)
      const input = root.querySelector('.ref-list__search')
      input.focus()
      input.value = 'o'
      input.dispatchEvent(new Event('input'))
      await vi.runAllTimersAsync()

      expect(searchDocuments).toHaveBeenLastCalledWith('song', 'o', { limit: 9 })
      const opts = [...root.querySelectorAll('.ref-list__menu .tags-input__option')]
      // Harvest Moon is already chosen, so it is not offered again.
      expect(opts.map(o => o.querySelector('.tags-input__option-tag').textContent)).toEqual(['Heart of Gold', 'Old Man'])

      opts[1].dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
      expect(onFieldChange.mock.calls.at(-1)[1]).toEqual(['s1', 's3'])
      expect([...root.querySelectorAll('.ref-list__title')].map(t => t.textContent)).toEqual(['Harvest Moon', 'Old Man'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('Enter adds the first match', async () => {
    vi.useFakeTimers()
    try {
      const onFieldChange = vi.fn()
      const root = buildDocumentRefList('songs', META, [], onFieldChange)
      document.body.appendChild(root)
      const input = root.querySelector('.ref-list__search')
      input.focus()
      input.value = 'gold'
      input.dispatchEvent(new Event('input'))
      await vi.runAllTimersAsync()
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
      expect(onFieldChange.mock.calls.at(-1)[1]).toEqual(['s2'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('an untyped list takes pasted ids and does not search', async () => {
    const { input, last } = await mount([], { field_type: 'document_ref_list' })
    input.focus()
    input.value = ' abc123 '
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(last()).toEqual(['abc123'])
    expect(searchDocuments).not.toHaveBeenCalled()
  })

  it('never adds the same document twice', async () => {
    const { input, last, onFieldChange } = await mount([], { field_type: 'document_ref_list' })
    for (const v of ['x', 'x']) {
      input.value = v
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    }
    expect(onFieldChange).toHaveBeenCalledTimes(1)
    expect(last()).toEqual(['x'])
  })
})

describe('inside a block card', () => {
  it('a picked slice searches its target type and writes the block field', async () => {
    const { buildBlockCanvas } = await import('../components/block-canvas.js')
    const { state } = await import('../state.js')
    state.formData = { data: [{ block_type: 'picked_slice', source: 'spotify', refs: ['s1'] }] }
    const prop = { anyOf: [{ type: 'array', items: { oneOf: [{ $ref: '#/$defs/PickedSlice' }] } }, { type: 'null' }] }
    const typeSchema = {
      $defs: {
        PickedSlice: {
          properties: {
            block_type: { const: 'picked_slice' },
            source: { type: 'string' },
            refs: { anyOf: [{ type: 'array', items: { type: 'string' } }, { type: 'null' }], 'cms:field_meta': META },
          },
        },
      },
    }
    const canvas = buildBlockCanvas('data', prop, {}, typeSchema, state.formData.data, () => {})
    document.body.innerHTML = ''
    document.body.appendChild(canvas)
    await flush()

    expect([...canvas.querySelectorAll('.ref-list__title')].map(t => t.textContent)).toEqual(['Harvest Moon'])
    canvas.querySelector('.ref-list__remove').click()
    expect(state.formData.data[0].refs).toEqual([])
    expect(state.formData.data[0].source).toBe('spotify')
  })
})
