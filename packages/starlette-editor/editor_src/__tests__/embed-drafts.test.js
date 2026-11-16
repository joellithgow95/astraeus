// @vitest-environment jsdom
/**
 * The embed toolbar's draft picker: what it offers, what choosing does, and
 * that drafts drawn onto the page are edited like any other card without being
 * swept up by Discard or Publish.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { EditToolbar } from '../embed/toolbar.js'
import { getActiveChangesetId, setActiveChangesetId } from '../changeset-store.js'
import { LENS_ALL, LENS_PROD, changesetLens, getLens, setLens } from '../embed/drafts.js'

const CMS = 'https://cms.example.com'
const flush = () => new Promise((r) => setTimeout(r, 0))

const pm = (text) => ({ type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] })
const draft = (id, title) => ({
  id,
  doc_type: 'blog_post',
  published: false,
  has_draft: false,
  body: { title, description: `About ${title}`, publish_date: '2026-09-01', body_markdown: pm('Body') },
})
const live = (id, title) => ({ ...draft(id, title), published: true })

function mountPage({ withSlot = true } = {}) {
  document.body.innerHTML = `
    <div ${withSlot ? 'data-cms-list="blog_post"' : ''} id="feed">
      <article data-cms-id="live-1" data-cms-type="blog_post">
        <span data-cms-field="title">Live post</span>
      </article>
    </div>
    ${withSlot ? `<template data-cms-draft-template="blog_post">
      <article>
        <span data-cms-field="title" data-cms-fill="title"></span>
        <p data-cms-field="description" data-cms-fill="description"></p>
      </article>
    </template>` : ''}
  `
}

/** Fake CMS: documents by id, the open changesets, and what the list endpoint says is unpublished. */
function fakeCms({ docs = {}, changesets = [], failDrafts = false } = {}) {
  const fetchMock = vi.fn(async (url) => {
    const u = new URL(url)
    const json = (data) => ({ ok: true, json: async () => data, headers: new Headers() })
    if (u.pathname === '/api/changesets') return json({ changesets })
    const cs = u.pathname.match(/^\/api\/changesets\/([^/]+)$/)
    if (cs) return json(changesets.find((c) => c.id === cs[1]) ?? { documents: [] })
    if (u.pathname === '/api/documents') {
      if (failDrafts && u.searchParams.get('published') === 'false') return { ok: false, status: 500, json: async () => ({}) }
      if (u.searchParams.get('published') === 'false') return json({ documents: Object.values(docs).filter((d) => !d.published) })
      if (u.searchParams.get('has_draft') === 'true') return json({ documents: Object.values(docs).filter((d) => d.published && d.has_draft) })
      return json({ documents: [] })
    }
    const single = u.pathname.match(/^\/api\/documents\/([^/]+)$/)
    if (single) return docs[single[1]] ? json(docs[single[1]]) : { ok: false, status: 404, json: async () => ({}) }
    return { ok: false, status: 404, json: async () => ({}) }
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function newToolbar() {
  const toolbar = new EditToolbar({
    cmsBase: CMS,
    cmsElements: [...document.querySelectorAll('[data-cms-id]')],
  })
  toolbar.mount()
  return toolbar
}

const picker = () => document.querySelector('#astraeus-toolbar select')
const optionValues = () => [...picker().options].map((o) => o.value)
const feedIds = () => [...document.querySelectorAll('#feed [data-cms-id]')].map((el) => el.dataset.cmsId)

beforeEach(() => {
  localStorage.clear()
  mountPage()
  // The live redraw channel (live-preview.js) is tested on its own.
  vi.stubGlobal('WebSocket', class { close() {} })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('the Edit button and picker', () => {
  it('shows an Edit button and a picker on a listing that can show drafts', async () => {
    fakeCms()
    newToolbar()
    const bar = document.getElementById('astraeus-toolbar')
    expect(bar.textContent).toContain('Edit')
    expect(picker()).not.toBeNull()
  })

  it('has no picker on a page with nowhere to put drafts', async () => {
    mountPage({ withSlot: false })
    fakeCms()
    newToolbar()
    expect(picker()).toBeNull()
  })

  it('offers live + all drafts, live only, and each open changeset', async () => {
    fakeCms({
      changesets: [
        { id: 'cs-a', title: 'Oct 3', documents: [{ id: 'x' }, { id: 'y' }] },
        { id: 'cs-b', title: '', documents: [] },
      ],
    })
    newToolbar()
    await flush()

    expect(optionValues()).toEqual([LENS_ALL, LENS_PROD, changesetLens('cs-a'), changesetLens('cs-b')])
    const labels = [...picker().options].map((o) => o.textContent)
    expect(labels[2]).toBe('Live + 📋 Oct 3 · 2 docs')
    expect(labels[3]).toBe('Live + 📋 Untitled · 0 docs')
  })

  it('starts on all drafts', async () => {
    fakeCms()
    newToolbar()
    expect(picker().value).toBe(LENS_ALL)
  })

  it('starts on the remembered choice', async () => {
    setLens(LENS_PROD)
    fakeCms()
    newToolbar()
    expect(picker().value).toBe(LENS_PROD)
  })

  it('falls back to all drafts when the remembered changeset has been published or deleted', async () => {
    setLens(changesetLens('gone'))
    fakeCms({ changesets: [{ id: 'cs-a', title: 'Oct 3', documents: [] }] })
    const toolbar = newToolbar()
    await flush()

    expect(toolbar.lens).toBe(LENS_ALL)
    expect(getLens()).toBe(LENS_ALL)
    expect(picker().value).toBe(LENS_ALL)
  })

  it('still works if the changesets cannot be loaded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    newToolbar()
    await flush()
    expect(optionValues()).toEqual([LENS_ALL, LENS_PROD])
  })

  it('keeps a remembered changeset selectable before the list has loaded', async () => {
    setLens(changesetLens('cs-a'))
    fakeCms({ changesets: [{ id: 'cs-a', title: 'Oct 3', documents: [] }] })
    newToolbar()
    expect(picker().value).toBe(changesetLens('cs-a'))
  })
})

describe('choosing a lens', () => {
  it('remembers it', async () => {
    fakeCms()
    const toolbar = newToolbar()
    await toolbar._setLens(LENS_PROD)
    expect(getLens()).toBe(LENS_PROD)
  })

  it('makes a chosen changeset the one edits are saved into', async () => {
    fakeCms({ changesets: [{ id: 'cs-a', title: 'Oct 3', documents: [] }] })
    const toolbar = newToolbar()
    await flush()
    await toolbar._setLens(changesetLens('cs-a'))
    expect(getActiveChangesetId()).toBe('cs-a')
  })

  it('leaves the active changeset alone when choosing prod or all drafts', async () => {
    setActiveChangesetId('cs-working')
    fakeCms()
    const toolbar = newToolbar()
    await toolbar._setLens(LENS_PROD)
    await toolbar._setLens(LENS_ALL)
    expect(getActiveChangesetId()).toBe('cs-working')
  })

  it('updates the page at once, before Edit is pressed, without making drafts editable', async () => {
    setLens(LENS_PROD)
    fakeCms({ docs: { d1: draft('d1', 'Unreleased') } })
    const toolbar = newToolbar()
    await flush()
    expect(feedIds()).toEqual(['live-1'])

    await toolbar._setLens(LENS_ALL)
    expect(feedIds()).toEqual(['d1', 'live-1'])
    const title = document.querySelector('[data-cms-id="d1"] [data-cms-field="title"]')
    expect(title.textContent).toBe('Unreleased')
    expect(title.contentEditable).not.toBe('true')

    await toolbar._setLens(LENS_PROD)
    expect(feedIds()).toEqual(['live-1'])
  })
})

describe('loading the page', () => {
  it('draws the drafts the remembered lens names, before Edit', async () => {
    setLens(changesetLens('cs-a'))
    fakeCms({
      docs: { d1: draft('d1', 'Staged'), d2: draft('d2', 'Elsewhere') },
      changesets: [{ id: 'cs-a', title: 'Staging', documents: [{ id: 'd1', doc_type: 'blog_post' }] }],
    })
    const toolbar = newToolbar()
    await toolbar._draftSync
    expect(feedIds()).toEqual(['d1', 'live-1'])
  })

  it('draws nothing extra on live only', async () => {
    setLens(LENS_PROD)
    fakeCms({ docs: { d1: draft('d1', 'Unreleased') } })
    newToolbar()
    await flush()
    expect(feedIds()).toEqual(['live-1'])
  })

  it('does not draw a draft twice when Edit is pressed while the page is still loading drafts', async () => {
    fakeCms({ docs: { d1: draft('d1', 'Unreleased'), 'live-1': live('live-1', 'Live post') } })
    const toolbar = newToolbar() // starts the first sync
    await toolbar._startEditing()
    expect(feedIds()).toEqual(['d1', 'live-1'])
  })
})

describe('pressing Edit', () => {
  it('draws the unpublished posts into the feed and makes them editable', async () => {
    fakeCms({ docs: { d1: draft('d1', 'Unreleased'), 'live-1': live('live-1', 'Live post') } })
    const toolbar = newToolbar()
    await toolbar._startEditing()

    expect(feedIds()).toEqual(['d1', 'live-1'])
    const card = document.querySelector('[data-cms-id="d1"]')
    expect(card.querySelector('[data-cms-field="title"]').textContent).toBe('Unreleased')
    expect(card.querySelector('[data-cms-field="title"]').contentEditable).toBe('true')
    expect(toolbar.state).toBe('editing')
  })

  it('shows nothing extra on "live only"', async () => {
    setLens(LENS_PROD)
    fakeCms({ docs: { d1: draft('d1', 'Unreleased'), 'live-1': live('live-1', 'Live post') } })
    const toolbar = newToolbar()
    await toolbar._startEditing()
    expect(feedIds()).toEqual(['live-1'])
    expect(toolbar.state).toBe('editing')
  })

  it('keeps drawn drafts out of Discard and the single-document Publish', async () => {
    fakeCms({ docs: { d1: draft('d1', 'Unreleased'), 'live-1': live('live-1', 'Live post') } })
    const toolbar = newToolbar()
    await toolbar._startEditing()

    // Discard wipes pending edits; a draft you only looked at must not be swept up.
    expect(toolbar.activeElements.map((el) => el.dataset.cmsId)).toEqual(['live-1'])
    // But the toolbar knows about it.
    expect(toolbar.draftCards.map((el) => el.dataset.cmsId)).toEqual(['d1'])
  })

  it('still lets the live posts be edited when the drafts cannot be loaded, and says so', async () => {
    fakeCms({ docs: { 'live-1': live('live-1', 'Live post') }, failDrafts: true })
    const toolbar = newToolbar()
    await toolbar._startEditing()

    expect(toolbar.state).toBe('editing')
    expect(document.querySelector('[data-cms-id="live-1"] [data-cms-field="title"]').contentEditable).toBe('true')
    expect(document.getElementById('astraeus-toolbar').textContent).toContain('Could not load drafts')
  })

  it('still offers the picker while editing', async () => {
    fakeCms()
    const toolbar = newToolbar()
    await toolbar._startEditing()
    expect(picker()).not.toBeNull()
  })
})

describe('switching lens while editing', () => {
  it('adds the drafts a wider lens reveals, editable straight away', async () => {
    setLens(LENS_PROD)
    fakeCms({ docs: { d1: draft('d1', 'Unreleased'), 'live-1': live('live-1', 'Live post') } })
    const toolbar = newToolbar()
    await toolbar._startEditing()
    expect(feedIds()).toEqual(['live-1'])

    await toolbar._setLens(LENS_ALL)

    expect(feedIds()).toEqual(['d1', 'live-1'])
    expect(document.querySelector('[data-cms-id="d1"] [data-cms-field="title"]').contentEditable).toBe('true')
  })

  it('removes the drafts a narrower lens hides, and keeps the live posts', async () => {
    fakeCms({ docs: { d1: draft('d1', 'Unreleased'), 'live-1': live('live-1', 'Live post') } })
    const toolbar = newToolbar()
    await toolbar._startEditing()
    expect(feedIds()).toEqual(['d1', 'live-1'])

    await toolbar._setLens(LENS_PROD)

    expect(feedIds()).toEqual(['live-1'])
    expect(toolbar.draftCards).toEqual([])
  })

  it('narrows to one changeset', async () => {
    fakeCms({
      docs: { d1: draft('d1', 'In the set'), d2: draft('d2', 'Not in the set'), 'live-1': live('live-1', 'Live post') },
      changesets: [{ id: 'cs-a', title: 'Oct 3', documents: [{ id: 'd1', doc_type: 'blog_post' }] }],
    })
    const toolbar = newToolbar()
    await toolbar._startEditing()
    expect(feedIds().sort()).toEqual(['d1', 'd2', 'live-1'])

    await toolbar._setLens(changesetLens('cs-a'))

    expect(feedIds().sort()).toEqual(['d1', 'live-1'])
  })
})

describe('Publish targets the changeset drafts collect in', () => {
  /** Fake CMS for publishing: the default changeset, any changeset by id, and what was POSTed. */
  function publishCms({ defaultChangeset = null, byId = {} } = {}) {
    const posts = []
    const fetchMock = vi.fn(async (url, init = {}) => {
      const u = new URL(url)
      const json = (data) => ({ ok: true, json: async () => data, headers: new Headers() })
      if (init.method === 'POST') {
        posts.push(u.pathname)
        return json({})
      }
      if (u.pathname === '/api/changesets/default') return json({ changeset: defaultChangeset })
      const cs = u.pathname.match(/^\/api\/changesets\/([^/]+)$/)
      if (cs && byId[cs[1]]) return json(byId[cs[1]])
      return { ok: false, status: 404, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', fetchMock)
    return { posts, fetchMock }
  }

  const staging = (n) => ({
    id: 'cs-staging',
    title: 'Staging',
    documents: Array.from({ length: n }, (_, i) => ({ id: `d${i}` })),
  })

  let confirmSpy
  beforeEach(() => {
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  it('publishes the default changeset when this browser has picked none, and names it', async () => {
    const { posts } = publishCms({ defaultChangeset: staging(3) })
    const toolbar = newToolbar()
    toolbar.activeElements = [{ dataset: { cmsId: 'live-1' } }]

    await toolbar._publish()

    expect(posts).toEqual(['/api/changesets/cs-staging/publish'])
    const message = confirmSpy.mock.calls[0][0]
    expect(message).toContain('Staging')
    expect(message).toContain('3 documents')
    expect(toolbar.state).toBe('published')
  })

  it('says "1 document", not "1 documents"', async () => {
    publishCms({ defaultChangeset: staging(1) })
    const toolbar = newToolbar()
    await toolbar._publish()
    expect(confirmSpy.mock.calls[0][0]).toContain('(1 document)')
  })

  it('prefers the changeset this browser is working in over the default', async () => {
    setActiveChangesetId('cs-mine')
    const { posts } = publishCms({
      defaultChangeset: staging(3),
      byId: { 'cs-mine': { id: 'cs-mine', title: 'Big rewrite', documents: [{ id: 'x' }, { id: 'y' }] } },
    })
    const toolbar = newToolbar()

    await toolbar._publish()

    expect(posts).toEqual(['/api/changesets/cs-mine/publish'])
    expect(confirmSpy.mock.calls[0][0]).toContain('Big rewrite')
    expect(confirmSpy.mock.calls[0][0]).toContain('2 documents')
    expect(getActiveChangesetId()).toBeNull() // shipped, so start fresh
  })

  it('leaves a remembered changeset alone when it published the default instead', async () => {
    publishCms({ defaultChangeset: staging(2) })
    const toolbar = newToolbar()
    await toolbar._publish()
    expect(getActiveChangesetId()).toBeNull()
  })

  it('falls back to the one post on the page when there is no default changeset', async () => {
    const { posts } = publishCms({ defaultChangeset: null })
    const toolbar = newToolbar()
    toolbar.activeElements = [{ dataset: { cmsId: 'live-1' } }]

    await toolbar._publish()

    expect(posts).toEqual(['/api/documents/live-1/publish'])
    expect(confirmSpy.mock.calls[0][0]).toContain('your changes')
  })

  it('does not publish an empty default', async () => {
    const { posts } = publishCms({ defaultChangeset: staging(0) })
    const toolbar = newToolbar()
    toolbar.activeElements = [{ dataset: { cmsId: 'live-1' } }]

    await toolbar._publish()

    expect(posts).toEqual(['/api/documents/live-1/publish'])
  })

  it('still publishes the active changeset when its details cannot be read', async () => {
    setActiveChangesetId('cs-mine')
    const posts = []
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      if (init.method === 'POST') {
        posts.push(new URL(url).pathname)
        return { ok: true, json: async () => ({}), headers: new Headers() }
      }
      throw new Error('offline')
    }))
    const toolbar = newToolbar()

    await toolbar._publish()

    expect(posts).toEqual(['/api/changesets/cs-mine/publish'])
    expect(confirmSpy.mock.calls[0][0]).toContain('your changeset')
  })

  it('publishes nothing when the confirm is declined', async () => {
    confirmSpy.mockReturnValue(false)
    const { posts } = publishCms({ defaultChangeset: staging(3) })
    const toolbar = newToolbar()

    await toolbar._publish()

    expect(posts).toEqual([])
    expect(toolbar.state).not.toBe('published')
  })

  it('does nothing when there is no target at all', async () => {
    const { posts } = publishCms({ defaultChangeset: null })
    const toolbar = newToolbar()
    toolbar.activeElements = []

    await toolbar._publish()

    expect(posts).toEqual([])
    expect(confirmSpy).not.toHaveBeenCalled()
  })
})

describe('asking the site to draw a card', () => {
  const previews = () => {
    const seen = []
    document.addEventListener('astraeus:preview', (e) => seen.push({ id: e.target.dataset.cmsId, detail: e.detail }))
    return seen
  }

  it('sends each drawn draft to the site, and waits for it to draw', async () => {
    const seen = previews()
    let drawn = false
    document.addEventListener('astraeus:preview', (e) => {
      e.detail.waitUntil(new Promise((r) => setTimeout(() => { drawn = true; r() }, 5)))
    })
    fakeCms({ docs: { d1: draft('d1', 'Unreleased') } })
    const toolbar = newToolbar()
    await toolbar._draftSync

    expect(drawn).toBe(true)
    expect(seen.map((s) => s.id)).toEqual(['d1'])
    expect(seen[0].detail.doc.body.title).toBe('Unreleased')
    expect(seen[0].detail.cmsBase).toBe(CMS)
    expect(seen[0].detail.inLens('anything')).toBe(true) // all drafts
  })

  it('shows a live post with its unpublished edits while the lens includes it, and as built after', async () => {
    const seen = previews()
    const edited = { ...live('live-1', 'Live post'), has_draft: true, body: { ...live('live-1', 'x').body, title: 'Edited title' } }
    fakeCms({
      docs: { 'live-1': edited },
      changesets: [{ id: 'cs-a', title: 'Staging', documents: [{ id: 'live-1', doc_type: 'blog_post' }] }],
    })
    const toolbar = newToolbar()
    await toolbar._draftSync
    const card = document.querySelector('[data-cms-id="live-1"]')
    expect(seen.at(-1)).toMatchObject({ id: 'live-1' })
    expect(seen.at(-1).detail.doc.body.title).toBe('Edited title')
    expect(card.hasAttribute('data-cms-previewed')).toBe(true)

    await toolbar._setLens(LENS_PROD)
    expect(seen.at(-1).id).toBe('live-1')
    expect(seen.at(-1).detail.doc).toBeNull()
    expect(card.hasAttribute('data-cms-previewed')).toBe(false)
  })

  it('leaves out a live post whose edits are not in the chosen changeset', async () => {
    const seen = previews()
    fakeCms({
      docs: { 'live-1': { ...live('live-1', 'Live post'), has_draft: true } },
      changesets: [{ id: 'cs-a', title: 'Other', documents: [{ id: 'zzz', doc_type: 'link' }] }],
    })
    setLens(changesetLens('cs-a'))
    const toolbar = newToolbar()
    await toolbar._draftSync
    expect(seen.filter((s) => s.id === 'live-1')).toEqual([])
  })

  it('tells the site which documents the chosen changeset holds', async () => {
    const seen = previews()
    setLens(changesetLens('cs-a'))
    fakeCms({
      docs: { d1: draft('d1', 'Staged') },
      changesets: [{ id: 'cs-a', title: 'Staging', documents: [{ id: 'd1', doc_type: 'blog_post' }, { id: 'link-9', doc_type: 'link' }] }],
    })
    const toolbar = newToolbar()
    await toolbar._draftSync
    const { inLens } = seen.find((s) => s.id === 'd1').detail
    expect(inLens('link-9')).toBe(true)
    expect(inLens('link-other')).toBe(false)
  })

  it('shows live posts with their edits on a list with no card template, without drawing drafts', async () => {
    document.querySelector('template').remove()
    const seen = previews()
    fakeCms({ docs: { d1: draft('d1', 'Unreleased'), 'live-1': { ...live('live-1', 'Live post'), has_draft: true } } })
    const toolbar = newToolbar()
    await toolbar._draftSync
    expect(picker()).not.toBeNull()
    expect(feedIds()).toEqual(['live-1'])
    expect(seen.map((x) => x.id)).toEqual(['live-1'])
  })
})
