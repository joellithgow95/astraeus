// @vitest-environment happy-dom
/**
 * Tests for components/tags-input.js — the TagsField chip input.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api.js', () => ({
  fetchTags: vi.fn(async () => [
    { tag: 'ai', count: 12 },
    { tag: 'claude-code', count: 7 },
    { tag: 'tools', count: 5 },
    { tag: 'design', count: 3 },
  ]),
}))

import { fetchTags } from '../api.js'
import { buildTagsInput, normalizeTag, rankSuggestions } from '../components/tags-input.js'

const flush = () => new Promise(r => setTimeout(r, 0))

function mount(value = [], extra = {}) {
  const onChange = vi.fn()
  const root = buildTagsInput({ id: 'field-tags', value, onChange, docType: 'blog_post', ...extra })
  document.body.innerHTML = ''
  document.body.appendChild(root)
  const input = root.querySelector('input')
  const chips = () => [...root.querySelectorAll('.tag-chip__label')].map(c => c.textContent)
  const options = () => [...root.querySelectorAll('.tags-input__option-tag')].map(c => c.textContent)
  const type = text => { input.value = text; input.dispatchEvent(new Event('input')) }
  const key = k => input.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }))
  return { root, input, onChange, chips, options, type, key }
}

describe('normalizeTag', () => {
  it('lowercases and hyphenates', () => {
    expect(normalizeTag('Claude Code')).toBe('claude-code')
    expect(normalizeTag('  Game_Design ')).toBe('game-design')
  })

  it('drops punctuation but keeps dots and letters', () => {
    expect(normalizeTag('astro.js')).toBe('astro.js')
    expect(normalizeTag('#ai!')).toBe('ai')
    expect(normalizeTag('café')).toBe('café')
  })

  it('collapses and trims hyphens', () => {
    expect(normalizeTag('--a  --  b--')).toBe('a-b')
    expect(normalizeTag('   ')).toBe('')
  })
})

describe('rankSuggestions', () => {
  const known = [
    { tag: 'ai', count: 12 },
    { tag: 'claude-code', count: 7 },
    { tag: 'ai-collaboration', count: 2 },
    { tag: 'open-ai', count: 1 },
  ]

  it('puts prefix matches before matches inside the tag', () => {
    expect(rankSuggestions(known, 'ai', []).map(k => k.tag)).toEqual(['ai', 'ai-collaboration', 'open-ai'])
  })

  it('leaves out tags already chosen', () => {
    expect(rankSuggestions(known, 'ai', ['ai']).map(k => k.tag)).toEqual(['ai-collaboration', 'open-ai'])
  })

  it('offers the most used tags when nothing is typed', () => {
    expect(rankSuggestions(known, '', []).map(k => k.tag)[0]).toBe('ai')
  })
})

describe('buildTagsInput', () => {
  beforeEach(() => fetchTags.mockClear())

  it('shows stored tags as chips, unchanged', () => {
    const { chips } = mount(['ai', 'Cloud Run'])
    expect(chips()).toEqual(['ai', 'Cloud Run'])
  })

  it('treats a non-list value as no tags', () => {
    const { chips } = mount('')
    expect(chips()).toEqual([])
  })

  it('adds a normalised tag on Enter', () => {
    const { type, key, chips, onChange } = mount(['ai'])
    type('Game Design')
    key('Enter')
    expect(chips()).toEqual(['ai', 'game-design'])
    expect(onChange).toHaveBeenLastCalledWith(['ai', 'game-design'])
  })

  it('splits a pasted comma list into chips', () => {
    const { type, chips } = mount()
    type('one, Two Words,')
    expect(chips()).toEqual(['one', 'two-words'])
  })

  it('ignores a duplicate', () => {
    const { type, key, chips, onChange } = mount(['ai'])
    type('AI')
    key('Enter')
    expect(chips()).toEqual(['ai'])
    expect(onChange).not.toHaveBeenCalled()
  })

  it('removes a chip with its × button', () => {
    const { root, chips, onChange } = mount(['ai', 'tools'])
    root.querySelector('.tag-chip__remove').click()
    expect(chips()).toEqual(['tools'])
    expect(onChange).toHaveBeenLastCalledWith(['tools'])
  })

  it('removes the last chip on Backspace in an empty input', () => {
    const { key, chips } = mount(['ai', 'tools'])
    key('Backspace')
    expect(chips()).toEqual(['ai'])
  })

  it('suggests tags in use on the type and picks one with the arrow keys', async () => {
    const { input, type, key, options, chips } = mount(['ai'])
    await flush()
    expect(fetchTags).toHaveBeenCalledWith('blog_post', 'tags')
    input.focus()
    type('t')
    expect(options()).toEqual(['tools', 'Add “t”'])
    key('ArrowDown')
    key('Enter')
    expect(chips()).toEqual(['ai', 'tools'])
  })

  it('offers to add a tag nobody has used yet', async () => {
    const { input, type, options } = mount()
    await flush()
    input.focus()
    type('Civic Engineering')
    expect(options()).toContain('Add “civic-engineering”')
  })

  it('keeps typed text when focus leaves', () => {
    const { input, type, chips } = mount()
    input.focus()
    type('half typed')
    input.blur()
    expect(chips()).toEqual(['half-typed'])
  })

  it('uses suggestions handed in instead of fetching', async () => {
    const { input, type, options } = mount([], { known: Promise.resolve([{ tag: 'portfolio', count: 3 }]) })
    await flush()
    input.focus()
    type('po')
    expect(options()[0]).toBe('portfolio')
    expect(fetchTags).not.toHaveBeenCalled()
  })
})
