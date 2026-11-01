// @vitest-environment happy-dom
/**
 * Tests for components/links-input.js — the LinksField card list.
 */

import { describe, it, expect, vi } from 'vitest'

vi.mock('../api.js', () => ({
  fetchTags: vi.fn(async () => [{ tag: 'developer-blogs', count: 4 }]),
}))

import { fetchTags } from '../api.js'
import { buildLinksInput, hostOf, linksValue, today } from '../components/links-input.js'
import { fieldWidget } from '../components/fields.js'

const SIMON = {
  url: 'https://simonwillison.net/',
  title: "Simon Willison's Weblog",
  description: 'Developer blog',
  date_added: '2026-01-17',
  tags: ['developer-blogs'],
  collections: ['random-developer-blogs'],
}

function mount(value, meta = {}) {
  const onFieldChange = vi.fn()
  const root = buildLinksInput('links', meta, value, 'blog_post', onFieldChange)
  document.body.innerHTML = ''
  document.body.appendChild(root)
  const cards = () => [...root.querySelectorAll('.links-input__card')]
  const titles = () => cards().map(c => c.querySelector('.links-input__title').textContent)
  const last = () => onFieldChange.mock.calls.at(-1)[1]
  return { root, onFieldChange, cards, titles, last }
}

describe('dispatch', () => {
  it('tags and links field types get their own widgets', () => {
    expect(fieldWidget('tags', { type: 'array' }, { field_type: 'tags' })).toBe('tags')
    expect(fieldWidget('links', { type: 'array' }, { field_type: 'links' })).toBe('links')
  })
})

describe('helpers', () => {
  it('hostOf strips www and survives a bad URL', () => {
    expect(hostOf('https://www.example.com/a')).toBe('example.com')
    expect(hostOf('not a url')).toBe('')
  })

  it('today is YYYY-MM-DD', () => {
    expect(today()).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('linksValue drops blank keys and cards without a URL, keeps unknown keys', () => {
    expect(linksValue([
      { ...SIMON, description: '' },
      { url: '', title: 'unfinished' },
      { url: 'https://x.test', tags: [] },
    ])).toEqual([
      { url: SIMON.url, title: SIMON.title, date_added: SIMON.date_added, tags: SIMON.tags, collections: SIMON.collections },
      { url: 'https://x.test' },
    ])
  })
})

describe('buildLinksInput', () => {
  it('renders a card per link with its title and host', () => {
    const { titles, root } = mount([SIMON, { url: 'https://rebecca-powell.com/' }])
    expect(titles()).toEqual(["Simon Willison's Weblog", 'rebecca-powell.com'])
    expect(root.querySelector('.links-input__host').textContent).toBe('simonwillison.net')
  })

  it('fetches link tag suggestions once, from the nested path', () => {
    fetchTags.mockClear()
    mount([SIMON, SIMON])
    expect(fetchTags).toHaveBeenCalledTimes(1)
    expect(fetchTags).toHaveBeenCalledWith('blog_post', 'links.tags')
  })

  it('editing a title keeps keys the card does not show', () => {
    const { root, last } = mount([SIMON])
    const title = root.querySelector('#field-links-0-title')
    title.value = 'Simon W.'
    title.dispatchEvent(new Event('input'))
    expect(last()).toEqual([{ ...SIMON, title: 'Simon W.' }])
    expect(root.querySelector('.links-input__title').textContent).toBe('Simon W.')
  })

  it('pasting a URL adds a link dated today', () => {
    const { root, cards, last } = mount([])
    const paste = root.querySelector('.links-input__paste')
    paste.value = 'https://julienrenau.com/'
    paste.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(cards()).toHaveLength(1)
    expect(last()).toEqual([{ url: 'https://julienrenau.com/', date_added: today() }])
    expect(paste.value).toBe('')
  })

  it('a blank new card shows but is not saved until it has a URL', () => {
    const { root, cards, last } = mount([SIMON])
    root.querySelector('.links-input__add .btn').click()
    expect(cards()).toHaveLength(2)
    expect(last()).toEqual([SIMON])
  })

  it('moves and removes cards', () => {
    const b = { url: 'https://b.test', title: 'B' }
    const { root, titles, last } = mount([SIMON, b])
    root.querySelectorAll('.links-input__card')[1].querySelector('[aria-label="Move up"]').click()
    expect(titles()).toEqual(['B', "Simon Willison's Weblog"])
    expect(last().map(l => l.url)).toEqual(['https://b.test', SIMON.url])
    root.querySelector('[aria-label="Remove link"]').click()
    expect(last()).toEqual([SIMON])
  })

  it('marks required_keys inputs required', () => {
    const { root } = mount([SIMON], { required_keys: ['title', 'date_added'] })
    expect(root.querySelector('#field-links-0-title').hasAttribute('required')).toBe(true)
    expect(root.querySelector('#field-links-0-date_added').hasAttribute('required')).toBe(true)
    expect(root.querySelector('#field-links-0-description').hasAttribute('required')).toBe(false)
  })

  it('link tags edit as chips', () => {
    const { root, last } = mount([SIMON])
    root.querySelector('#field-links-0-tags').closest('.tags-input').querySelector('.tag-chip__remove').click()
    expect(last()).toEqual([{ ...SIMON, tags: undefined }].map(({ tags, ...rest }) => rest))
  })
})
