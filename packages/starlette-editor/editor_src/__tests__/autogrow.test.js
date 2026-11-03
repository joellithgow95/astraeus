// @vitest-environment happy-dom
/**
 * Tests for components/autogrow.js and the text widgets that use it.
 */

import { describe, it, expect } from 'vitest'
import { autoGrow, singleLine, resizeTextarea } from '../components/autogrow.js'
import { buildFieldGroup, fieldWidget } from '../components/fields.js'

function render(name, prop, meta, value, onChange = () => {}) {
  const state = { formData: { [name]: value }, schema: {}, activeType: 't' }
  const group = buildFieldGroup(name, prop, meta, state, onChange, () => null, () => null)
  document.body.appendChild(group)
  return group
}

describe('autogrow', () => {
  it('fits the height to the content', () => {
    const ta = autoGrow(document.createElement('textarea'))
    document.body.appendChild(ta)
    Object.defineProperty(ta, 'scrollHeight', { value: 120, configurable: true })
    resizeTextarea(ta)
    expect(ta.style.height).toBe('120px')
    expect(ta.style.overflowY).toBe('hidden')
  })

  it('caps the height and scrolls past it', () => {
    const ta = autoGrow(document.createElement('textarea'))
    document.body.appendChild(ta)
    Object.defineProperty(ta, 'scrollHeight', { value: 50000, configurable: true })
    resizeTextarea(ta)
    expect(parseInt(ta.style.height, 10)).toBeLessThan(50000)
    expect(ta.style.overflowY).toBe('auto')
  })

  it('leaves an ordinary textarea alone', () => {
    const ta = document.createElement('textarea')
    document.body.appendChild(ta)
    resizeTextarea(ta)
    expect(ta.style.height).toBe('')
  })

  it('a single-line box ignores Enter and flattens pasted line breaks', () => {
    const ta = singleLine(document.createElement('textarea'))
    document.body.appendChild(ta)
    const enter = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true })
    ta.dispatchEvent(enter)
    expect(enter.defaultPrevented).toBe(true)

    ta.value = 'one\ntwo\r\nthree'
    ta.dispatchEvent(new Event('input'))
    expect(ta.value).toBe('one two three')
  })
})

describe('text widgets', () => {
  it('a long-text field grows', () => {
    expect(fieldWidget('definition', { type: 'string' }, {})).toBe('textarea')
    // Optional fields come through as anyOf [string, null].
    expect(fieldWidget('personal_notes', { anyOf: [{ type: 'string' }, { type: 'null' }] }, {})).toBe('textarea')
    const group = render('definition', { type: 'string' }, {}, 'A long definition')
    const ta = group.querySelector('textarea#field-definition')
    expect(ta.hasAttribute('data-autogrow')).toBe(true)
    expect(ta.value).toBe('A long definition')
  })

  it('a short text field wraps instead of scrolling, and reports a flat string', () => {
    const seen = []
    const group = render('title', { type: 'string', maxLength: 200 }, {}, 'Hello', (_, v) => seen.push(v))
    const ta = group.querySelector('#field-title')
    expect(ta.tagName).toBe('TEXTAREA')
    expect(ta.hasAttribute('data-single-line')).toBe(true)
    expect(ta.getAttribute('maxlength')).toBe('200')

    ta.value = 'Hello\nworld'
    ta.dispatchEvent(new Event('input'))
    expect(seen.at(-1)).toBe('Hello world')
  })

  it('a URL field stays an input', () => {
    const group = render('url', { type: 'string' }, { field_type: 'url' }, 'https://x.test')
    const input = group.querySelector('#field-url')
    expect(input.tagName).toBe('INPUT')
    expect(input.type).toBe('url')
  })
})
