// @vitest-environment jsdom
/**
 * Tests for components/floating-panel.js — the anchored-panel chrome
 * extracted from the tags editor (ADR 020). The doodle widget (mode:
 * 'overlay') is the second consumer, exercised via edit-mode.js's own tests.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { openFloatingPanel } from '../components/floating-panel.js'

describe('openFloatingPanel', () => {
  let anchor

  beforeEach(() => {
    document.body.innerHTML = ''
    anchor = document.createElement('button')
    anchor.textContent = 'Edit'
    document.body.appendChild(anchor)
  })

  it('appends a panel positioned under the anchor', () => {
    const { container } = openFloatingPanel(anchor, { className: 'my-panel' })
    expect(container.className).toBe('my-panel')
    expect(document.body.contains(container)).toBe(true)
  })

  it('replaces an existing panel of the same class instead of stacking', () => {
    const first = openFloatingPanel(anchor, { className: 'my-panel' })
    const second = openFloatingPanel(anchor, { className: 'my-panel' })
    expect(document.querySelectorAll('.my-panel').length).toBe(1)
    expect(document.body.contains(first.container)).toBe(false)
    expect(document.body.contains(second.container)).toBe(true)
  })

  it('does not touch a panel with a different class', () => {
    openFloatingPanel(anchor, { className: 'panel-a' })
    openFloatingPanel(anchor, { className: 'panel-b' })
    expect(document.querySelectorAll('.panel-a').length).toBe(1)
    expect(document.querySelectorAll('.panel-b').length).toBe(1)
  })

  it('close() removes the panel', () => {
    const { container, close } = openFloatingPanel(anchor, { className: 'my-panel' })
    close()
    expect(document.body.contains(container)).toBe(false)
  })

  it('closes on Escape', () => {
    const { container } = openFloatingPanel(anchor, { className: 'my-panel' })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(document.body.contains(container)).toBe(false)
  })

  it('closes on an outside click but not a click inside the panel or on the anchor', async () => {
    const { container } = openFloatingPanel(anchor, { className: 'my-panel' })
    // The outside-click listener is deferred so the opening click doesn't
    // immediately close it — wait a tick before dispatching test clicks.
    await new Promise((r) => setTimeout(r, 0))

    container.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(document.body.contains(container)).toBe(true)

    anchor.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(document.body.contains(container)).toBe(true)

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(document.body.contains(container)).toBe(false)
  })
})
