// @vitest-environment jsdom
/**
 * LivePreview: what a message on /api/events redraws on a page showing drafts.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { LivePreview } from '../embed/live-preview.js'

const wait = (ms = 300) => new Promise((r) => setTimeout(r, ms))

function setup({ current = { cmsBase: 'https://cms.example.com', context: { lens: 'all', inLens: () => true } } } = {}) {
  document.body.innerHTML = `
    <div data-cms-list="blog_post">
      <article data-cms-id="d1" data-cms-type="blog_post" data-cms-draft-card><span data-cms-fill="title">Old</span></article>
      <article data-cms-id="live-1" data-cms-type="blog_post" data-cms-previewed></article>
      <article data-cms-id="live-2" data-cms-type="blog_post"></article>
    </div>`
  const calls = { preview: [], refill: [], load: [], resync: 0 }
  const live = new LivePreview({
    cmsBase: 'https://cms.example.com',
    current: () => current,
    preview: vi.fn(async (card, doc, options) => { calls.preview.push({ id: card.dataset.cmsId, doc, changed: options.changed }) }),
    refill: vi.fn((card, doc) => { calls.refill.push({ id: card.dataset.cmsId, doc }) }),
    load: vi.fn(async (id) => { calls.load.push(id); return { id, body: { title: `New ${id}` } } }),
    resync: vi.fn(async () => { calls.resync++ }),
    WebSocketImpl: null,
  })
  return { live, calls }
}

beforeEach(() => { document.body.innerHTML = '' })
afterEach(() => { vi.restoreAllMocks() })

describe('LivePreview', () => {
  it('redraws a draft card whose fields changed, refilling its text', async () => {
    const { live, calls } = setup()
    live.handle({ event: 'document.fields', document_id: 'd1', document_type: 'blog_post', fields: { title: 'New' } })
    await wait()
    expect(calls.load).toEqual(['d1'])
    expect(calls.refill.map((c) => c.id)).toEqual(['d1'])
    expect(calls.preview).toEqual([{ id: 'd1', doc: { id: 'd1', body: { title: 'New d1' } }, changed: ['d1'] }])
  })

  it('redraws a live card shown with its edits, without refilling the built text', async () => {
    const { live, calls } = setup()
    live.handle({ event: 'document.updated', document_id: 'live-1', document_type: 'blog_post' })
    await wait()
    expect(calls.refill).toEqual([])
    expect(calls.preview.map((c) => c.id)).toEqual(['live-1'])
  })

  it('ignores a live card that is shown as built', async () => {
    const { live, calls } = setup()
    live.handle({ event: 'document.fields', document_id: 'live-2', document_type: 'blog_post' })
    await wait()
    expect(calls.preview).toEqual([])
  })

  it('redraws every drawn card when a document they may show changes', async () => {
    const { live, calls } = setup()
    live.handle({ event: 'document.fields', document_id: 'link-9', document_type: 'link' })
    await wait()
    expect(calls.preview.map((c) => c.id).sort()).toEqual(['d1', 'live-1'])
    expect(calls.preview.every((c) => c.changed[0] === 'link-9')).toBe(true)
  })

  it('ignores another post that is not on the page', async () => {
    const { live, calls } = setup()
    live.handle({ event: 'document.fields', document_id: 'elsewhere', document_type: 'blog_post' })
    await wait()
    expect(calls.preview).toEqual([])
  })

  it('draws the lens again when a document is created, deleted or published, once for a burst', async () => {
    const { live, calls } = setup()
    live.handle({ event: 'document.created', document_id: 'n1', document_type: 'blog_post' })
    live.handle({ event: 'document.published', document_id: 'n2', document_type: 'blog_post' })
    await wait()
    expect(calls.resync).toBe(1)
  })

  it('does nothing while the page shows the live site only', async () => {
    const { live, calls } = setup({ current: null })
    live.handle({ event: 'document.fields', document_id: 'd1', document_type: 'blog_post' })
    live.handle({ event: 'document.created', document_id: 'n1', document_type: 'blog_post' })
    await wait()
    expect(calls.preview).toEqual([])
    expect(calls.resync).toBe(0)
  })

  it('debounces a burst of edits to one document into one redraw', async () => {
    const { live, calls } = setup()
    for (let i = 0; i < 5; i++) live.handle({ event: 'document.fields', document_id: 'd1', document_type: 'blog_post' })
    await wait()
    expect(calls.load).toEqual(['d1'])
  })
})
