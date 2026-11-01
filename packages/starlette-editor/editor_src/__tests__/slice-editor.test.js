// @vitest-environment happy-dom
/**
 * Tests for the slice editor: block cards read their fields' cms:field_meta
 * (date pickers, a TagField's suggestions, labels, help), say what they hold
 * when collapsed, stay open across a re-render, and a reference list can
 * create a link document from a pasted URL.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const LINKS = [
  { id: 'l1', slug: 'existing', body: { title: 'Existing', url: 'https://example.com/existing' } },
]

vi.mock('../api.js', () => ({
  fetchDocumentsByIds: vi.fn(async ids => LINKS.filter(l => ids.includes(l.id))),
  searchDocuments: vi.fn(async (_type, q) =>
    LINKS.filter(l => !q || l.body.url.includes(q) || l.body.title.toLowerCase().includes(q.toLowerCase()))),
  fetchTags: vi.fn(async () => [{ tag: 'developer-blog', count: 4 }, { tag: 'inspo', count: 2 }]),
  createDocument: vi.fn(async (docType, body) => ({ id: 'new1', slug: '', doc_type: docType, body })),
  addDocToChangeset: vi.fn(async () => ({})),
}))

import { addDocToChangeset, createDocument, fetchTags } from '../api.js'
import { addBlock, blockSummary, buildBlockCanvas, moveBlock, removeBlock } from '../components/block-canvas.js'
import { buildDocumentRefList, slugify, titleFromUrl, today, urlCreatePlan } from '../components/document-ref-list.js'
import { buildFieldGroup, fieldWidget } from '../components/fields.js'
import { buildTagInput } from '../components/tags-input.js'
import { state } from '../state.js'

const flush = () => new Promise(r => setTimeout(r, 0))
const noop = () => {}

const RANGE = {
  description: 'Spotify likes whose liked_at falls between from_date and to_date (both included).',
  properties: {
    block_type: { const: 'music_range', type: 'string' },
    from_date: { type: 'string', 'cms:field_meta': { field_type: 'date', help_text: 'First day' } },
    to_date: { type: 'string', 'cms:field_meta': { field_type: 'date' } },
  },
  required: ['from_date', 'to_date'],
}
const BY_TAG = {
  description: 'Every link with this tag.',
  properties: {
    block_type: { const: 'links_by_tag', type: 'string' },
    tag: { type: 'string', 'cms:field_meta': { field_type: 'tag', suggest_from: 'link', suggest_field: 'tags' } },
  },
  required: ['tag'],
}
const BY_TAGS = {
  description: 'Every link with any of these tags.',
  properties: {
    block_type: { const: 'links_by_tags', type: 'string' },
    tags: { type: 'array', items: { type: 'string' },
      'cms:field_meta': { field_type: 'tags', suggest_from: 'link', suggest_field: 'tags' } },
  },
  required: ['tags'],
}
const PICKS = {
  properties: {
    block_type: { const: 'link_picks', type: 'string' },
    refs: { anyOf: [{ type: 'array', items: { type: 'string' } }, { type: 'null' }],
      'cms:field_meta': { field_type: 'document_ref_list', ref_block_type: 'link', label: 'Links' } },
  },
}
const POST_SCHEMA = { $defs: { MusicRange: RANGE, LinksByTag: BY_TAG, LinksByTags: BY_TAGS, LinkPicks: PICKS } }
const DATA_PROP = {
  anyOf: [{
    type: 'array',
    items: { oneOf: [{ $ref: '#/$defs/MusicRange' }, { $ref: '#/$defs/LinksByTag' }, { $ref: '#/$defs/LinksByTags' }, { $ref: '#/$defs/LinkPicks' }] },
  }, { type: 'null' }],
}
const LINK_SCHEMA = {
  properties: {
    title: { type: 'string' },
    url: { type: 'string', 'cms:field_meta': { format: 'url', max_length: 2048 } },
    description: { anyOf: [{ type: 'string' }, { type: 'null' }] },
    date_added: { type: 'string' },
    tags: { anyOf: [{ type: 'array' }, { type: 'null' }], 'cms:field_meta': { field_type: 'tags' } },
  },
  required: ['title', 'url', 'date_added'],
}

function mountCanvas(value) {
  state.formData = { data: value }
  const canvas = buildBlockCanvas('data', DATA_PROP, {}, POST_SCHEMA, value, noop)
  document.body.innerHTML = ''
  document.body.appendChild(canvas)
  return canvas
}

beforeEach(() => {
  vi.clearAllMocks()
  state.formData = {}
  state.activeDocId = `doc-${Math.random()}`
  state.activeChangesetId = null
  state.schema = { link: { schema: LINK_SCHEMA } }
})

describe('fieldWidget', () => {
  it('picks the date and tag widgets from field_type', () => {
    expect(fieldWidget('from_date', { type: 'string' }, { field_type: 'date' })).toBe('date')
    expect(fieldWidget('tag', { type: 'string' }, { field_type: 'tag' })).toBe('tag')
  })
})

describe('top-level date field', () => {
  it('is a native date input that writes YYYY-MM-DD, and null when cleared', () => {
    state.formData = { date_added: '2026-10-28' }
    state.activeType = 'link'
    const onChange = vi.fn()
    const group = buildFieldGroup('date_added', { type: 'string' }, { field_type: 'date' }, state, onChange, noop, noop)
    const input = group.querySelector('input')
    expect(input.type).toBe('date')
    expect(input.value).toBe('2026-10-28')
    input.value = ''
    input.dispatchEvent(new Event('input'))
    expect(onChange).toHaveBeenLastCalledWith('date_added', null)
  })
})

describe('blockSummary', () => {
  it('reads a range as from – to', () => {
    expect(blockSummary({ from_date: '2026-10-01', to_date: '2026-10-31' }, RANGE)).toBe('2026-10-01 – 2026-10-31')
  })

  it('treats date-shaped text as a date even without field_type', () => {
    const legacy = { properties: { from_date: { type: 'string' }, to_date: { type: 'string' } } }
    expect(blockSummary({ from_date: '2026-10-01', to_date: '2026-10-31' }, legacy)).toBe('2026-10-01 – 2026-10-31')
  })

  it('counts picked references by the field label', () => {
    expect(blockSummary({ refs: ['a', 'b', 'c'] }, PICKS)).toBe('Links: 3')
  })

  it('lists the tags of a several-tag slice', () => {
    expect(blockSummary({ tags: ['art', 'design'] }, BY_TAGS)).toBe('art, design')
    expect(blockSummary({ tags: [] }, BY_TAGS)).toBe('')
  })

  it('shows a tag as written, and nothing for an empty block', () => {
    expect(blockSummary({ tag: 'developer-blog' }, BY_TAG)).toBe('developer-blog')
    expect(blockSummary({ tag: '' }, BY_TAG)).toBe('')
    expect(blockSummary({ refs: [] }, PICKS)).toBe('')
  })
})

describe('slice cards', () => {
  it('give a range two date pickers, its help text and the block description', () => {
    const canvas = mountCanvas([{ block_type: 'music_range', from_date: '2026-10-01', to_date: '2026-10-31' }])
    const dates = canvas.querySelectorAll('input[type=date]')
    expect([...dates].map(d => d.value)).toEqual(['2026-10-01', '2026-10-31'])
    expect(canvas.textContent).toContain('First day')
    expect(canvas.querySelector('.block-card__description').textContent).toContain('liked_at')
    expect(canvas.querySelectorAll('.field-label--required')).toHaveLength(2)
  })

  it('update the collapsed summary as a date changes', () => {
    const canvas = mountCanvas([{ block_type: 'music_range', from_date: '2026-10-01', to_date: '2026-10-31' }])
    const to = canvas.querySelectorAll('input[type=date]')[1]
    to.value = '2026-11-30'
    to.dispatchEvent(new Event('input'))
    expect(state.formData.data[0].to_date).toBe('2026-11-30')
    expect(canvas.querySelector('.block-card__summary').textContent).toBe('2026-10-01 – 2026-11-30')
  })

  it('offer a tag slice the tags used on the source type', async () => {
    mountCanvas([{ block_type: 'links_by_tag', tag: '' }])
    expect(fetchTags).toHaveBeenCalledWith('link', 'tags')
  })

  it('offer a several-tag slice the tags used on the source type', async () => {
    fetchTags.mockClear()
    const canvas = mountCanvas([{ block_type: 'links_by_tags', tags: ['art'] }])
    expect(fetchTags).toHaveBeenCalledWith('link', 'tags')
    expect([...canvas.querySelectorAll('.tag-chip__label')].map(c => c.textContent)).toEqual(['art'])
  })

  it('open a newly added block, and keep it open across re-renders', () => {
    addBlock('data', { registeredType: 'music_range', schema: RANGE }, noop)
    let canvas = mountCanvas(state.formData.data)
    expect(canvas.querySelector('.block-card').classList.contains('is-open')).toBe(true)
    addBlock('data', { registeredType: 'links_by_tag', schema: BY_TAG }, noop)
    canvas = mountCanvas(state.formData.data)
    const open = [...canvas.querySelectorAll('.block-card')].map(c => c.classList.contains('is-open'))
    expect(open).toEqual([true, true])
  })

  it('carry the open state with a moved block and drop it with a removed one', () => {
    state.formData = { data: [{ block_type: 'music_range' }, { block_type: 'links_by_tag', tag: '' }] }
    let canvas = mountCanvas(state.formData.data)
    canvas.querySelectorAll('.block-card__header')[0].click()  // open the first
    moveBlock('data', 0, 1, noop)
    canvas = mountCanvas(state.formData.data)
    expect([...canvas.querySelectorAll('.block-card')].map(c => c.classList.contains('is-open'))).toEqual([false, true])
    removeBlock('data', 1, noop)
    canvas = mountCanvas(state.formData.data)
    expect(canvas.querySelector('.block-card').classList.contains('is-open')).toBe(false)
  })

  it('list each block type with its description in the add menu', () => {
    const canvas = mountCanvas([])
    const items = [...canvas.querySelectorAll('.block-type-picker__item')]
    expect(items[0].querySelector('.block-type-picker__name').textContent).toBe('Music Range')
    expect(items[1].querySelector('.block-type-picker__description').textContent).toBe('Every link with this tag.')
  })
})

describe('buildTagInput (one tag)', () => {
  it('holds a single tag as a string and hides the box once chosen', () => {
    const onChange = vi.fn()
    const root = buildTagInput({ id: 't', value: '', onChange, meta: { suggest_from: 'link' } })
    const input = root.querySelector('input')
    input.value = 'Developer Blog'
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(onChange).toHaveBeenLastCalledWith('developer-blog')
    expect(input.hidden).toBe(true)
    root.querySelector('.tag-chip__remove').click()
    expect(onChange).toHaveBeenLastCalledWith('')
    expect(input.hidden).toBe(false)
  })
})

describe('creating a link from a pasted URL', () => {
  it('plans the form from the target schema: URL, title, required date', () => {
    const plan = urlCreatePlan(LINK_SCHEMA)
    expect(plan.urlField).toBe('url')
    expect(plan.fields.map(f => [f.name, f.kind])).toEqual([['title', 'text'], ['url', 'url'], ['date_added', 'date']])
  })

  it('has no plan for a type without a URL field, or with a required field a form cannot fill', () => {
    expect(urlCreatePlan({ properties: { title: { type: 'string' } } })).toBeNull()
    const withJson = structuredClone(LINK_SCHEMA)
    withJson.properties.meta = { type: 'object' }
    withJson.required.push('meta')
    expect(urlCreatePlan(withJson)).toBeNull()
  })

  it('makes a title from the URL', () => {
    expect(titleFromUrl('https://www.example.com/blog/my-first-post/')).toBe('example.com — my first post')
    expect(titleFromUrl('https://example.com')).toBe('example.com')
    expect(titleFromUrl('https://example.com/notes.html')).toBe('example.com — notes')
  })

  it('slugs a title', () => {
    expect(slugify('example.com — Café notes!')).toBe('example-com-cafe-notes')
  })

  it('formats today in local time', () => {
    expect(today(new Date(2026, 0, 5, 23, 30))).toBe('2026-01-05')
  })

  async function mountLinks(ids = []) {
    const onFieldChange = vi.fn()
    const root = buildDocumentRefList('refs', { ref_block_type: 'link' }, ids, onFieldChange)
    document.body.innerHTML = ''
    document.body.appendChild(root)
    await flush()
    const input = root.querySelector('.ref-list__search')
    input.focus()
    return { root, input, onFieldChange }
  }

  async function paste(input, url) {
    input.value = url
    input.dispatchEvent(new Event('input'))
    await new Promise(r => setTimeout(r, 250))  // past the search debounce
  }

  it('offers to create one when no document holds the URL', async () => {
    const { root, input } = await mountLinks()
    await paste(input, 'https://new.example.org/a-post')
    const options = [...root.querySelectorAll('.tags-input__option')].map(o => o.textContent)
    expect(options.at(-1)).toContain('Create link for this URL')
  })

  it('does not offer it when a link already has that URL', async () => {
    const { root, input } = await mountLinks()
    await paste(input, 'https://example.com/existing')
    const options = [...root.querySelectorAll('.tags-input__option')].map(o => o.textContent)
    expect(options.join()).not.toContain('Create')
    expect(options[0]).toContain('Existing')
  })

  it('creates the link from the prefilled form and adds it to the list', async () => {
    state.activeChangesetId = 'cs1'
    const { root, input, onFieldChange } = await mountLinks()
    await paste(input, 'https://new.example.org/a-post')
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    const form = root.querySelector('.ref-list__create')
    expect(form.hidden).toBe(false)
    const fields = Object.fromEntries([...form.querySelectorAll('input')].map(i => [i.id.split('-new-')[1], i.value]))
    expect(fields).toEqual({ title: 'new.example.org — a post', url: 'https://new.example.org/a-post', date_added: today() })

    form.querySelector('.btn--primary').click()
    await flush()
    expect(createDocument).toHaveBeenCalledWith('link', {
      title: 'new.example.org — a post', url: 'https://new.example.org/a-post', date_added: today(),
    }, 'new-example-org-a-post')
    expect(addDocToChangeset).toHaveBeenCalledWith('cs1', 'new1')
    expect(onFieldChange).toHaveBeenLastCalledWith('refs', ['new1'])
    expect(root.querySelector('.ref-list__title').textContent).toBe('new.example.org — a post')
    expect(form.hidden).toBe(true)
  })

  it('shows the CMS error and keeps the form when the create fails', async () => {
    createDocument.mockRejectedValueOnce(new Error('date_added: must be a date as YYYY-MM-DD'))
    const { root, input, onFieldChange } = await mountLinks()
    await paste(input, 'https://new.example.org/a-post')
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    root.querySelector('.ref-list__create .btn--primary').click()
    await flush()
    expect(root.querySelector('.ref-list__create-error').textContent).toContain('YYYY-MM-DD')
    expect(root.querySelector('.ref-list__create').hidden).toBe(false)
    expect(onFieldChange).not.toHaveBeenCalled()
  })

  it('is not offered for a type that cannot be made from a URL', async () => {
    state.schema = { song: { schema: { properties: { title: { type: 'string' } } } } }
    const root = buildDocumentRefList('refs', { ref_block_type: 'song' }, [], vi.fn())
    document.body.innerHTML = ''
    document.body.appendChild(root)
    const input = root.querySelector('.ref-list__search')
    input.focus()
    await paste(input, 'https://new.example.org/a-post')
    expect(root.textContent).not.toContain('Create')
  })
})
