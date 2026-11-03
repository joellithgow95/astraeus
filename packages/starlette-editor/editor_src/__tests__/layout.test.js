// @vitest-environment happy-dom
/**
 * Tests for components/layout.js — resizable, collapsible columns.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { createLayout, loadLayout, clampWidth, LAYOUT_KEY, LIMITS } from '../components/layout.js'

function setup() {
  const app = document.createElement('div')
  const nav = document.createElement('aside')
  const docs = document.createElement('aside')
  const layout = createLayout(app, { nav, docs })
  return { app, nav, docs, layout }
}

describe('layout', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('clamps widths to the limits', () => {
    expect(clampWidth('nav', 10)).toBe(LIMITS.nav.min)
    expect(clampWidth('docs', 9999)).toBe(LIMITS.docs.max)
    expect(clampWidth('docs', 300.4)).toBe(300)
  })

  it('reads a stored layout and clamps it', () => {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ navCollapsed: true, docsWidth: 5000 }))
    expect(loadLayout()).toEqual({
      navCollapsed: true,
      docsCollapsed: false,
      navWidth: null,
      docsWidth: LIMITS.docs.max,
    })
  })

  it('survives a corrupt stored layout', () => {
    localStorage.setItem(LAYOUT_KEY, '{nope')
    expect(loadLayout().navCollapsed).toBe(false)
  })

  it('collapses navigation to a rail and remembers it', () => {
    const { app, layout } = setup()
    expect(app.classList.contains('nav-rail')).toBe(false)
    layout.navToggle.click()
    expect(app.classList.contains('nav-rail')).toBe(true)
    expect(layout.navToggle.getAttribute('aria-expanded')).toBe('false')
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY)).navCollapsed).toBe(true)

    // A fresh shell starts collapsed.
    expect(setup().app.classList.contains('nav-rail')).toBe(true)
  })

  it('hides the document list and brings it back from the header', () => {
    const { app, layout } = setup()
    layout.docsToggle.click()
    expect(app.classList.contains('docs-collapsed')).toBe(true)
    layout.docsReveal.click()
    expect(app.classList.contains('docs-collapsed')).toBe(false)
  })

  it('applies a stored width as a custom property', () => {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ navWidth: 300 }))
    const { app } = setup()
    expect(app.style.getPropertyValue('--sidebar-width')).toBe('300px')
  })

  it('resizes with the arrow keys and resets on double-click', () => {
    const { app, layout } = setup()
    const handle = layout.docsResizer
    expect(handle.getAttribute('role')).toBe('separator')

    // happy-dom reports a 0 width, so the step starts from the initial width.
    handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', shiftKey: true }))
    const expected = LIMITS.docs.initial + 64
    expect(app.style.getPropertyValue('--doc-list-width')).toBe(`${expected}px`)
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY)).docsWidth).toBe(expected)

    handle.dispatchEvent(new MouseEvent('dblclick'))
    expect(app.style.getPropertyValue('--doc-list-width')).toBe('')
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY)).docsWidth).toBe(null)
  })
})
