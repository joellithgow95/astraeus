// @vitest-environment jsdom
/**
 * Tests for components/doodle-widget.js — the 'doodles' field widget.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createDoodleWidget, pointsToPathData, boundingBoxOf, strokesToDoodle } from '../components/doodle-widget.js'

describe('pointsToPathData', () => {
  it('returns empty string for no points', () => {
    expect(pointsToPathData([])).toBe('')
  })

  it('renders a single point as a zero-length line', () => {
    expect(pointsToPathData([{ x: 5, y: 5 }])).toBe('M 5 5 L 5 5')
  })

  it('starts with M at the first point and ends with L at the last', () => {
    const d = pointsToPathData([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 20, y: 0 }])
    expect(d.startsWith('M 0 0')).toBe(true)
    expect(d).toMatch(/L 20 0$/)
  })

  it('decimates points closer than the minimum distance', () => {
    // Every point is 1px apart (below the 3px threshold) — should collapse
    // to far fewer than the 10 points supplied.
    const points = Array.from({ length: 10 }, (_, i) => ({ x: i, y: 0 }))
    const d = pointsToPathData(points)
    const commandCount = d.split(' ').filter(t => t === 'M' || t === 'Q' || t === 'L').length
    expect(commandCount).toBeLessThan(10)
  })
})

// Minimal 2d context stub — jsdom/happy-dom don't implement canvas rendering,
// and the widget only needs these calls to not throw, not to actually paint.
function stubCanvasContext() {
  const ctx2d = {
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    strokeStyle: '',
    lineWidth: 0,
    lineCap: '',
    lineJoin: '',
  }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx2d)
  return ctx2d
}

function draw(canvas, points) {
  canvas.dispatchEvent(new PointerEvent('pointerdown', { clientX: points[0].x, clientY: points[0].y }))
  for (const p of points.slice(1)) {
    canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: p.x, clientY: p.y }))
  }
  canvas.dispatchEvent(new PointerEvent('pointerup'))
}

describe('createDoodleWidget', () => {
  let container
  let onSave

  beforeEach(() => {
    document.body.innerHTML = ''
    container = document.createElement('div')
    document.body.appendChild(container)
    onSave = vi.fn()
    stubCanvasContext()
  })

  it('shows an empty state when there are no doodles', () => {
    createDoodleWidget([], { container, onSave })
    expect(container.textContent).toContain('No doodles yet.')
  })

  it('treats a null/undefined value as an empty list', () => {
    createDoodleWidget(null, { container, onSave })
    expect(container.textContent).toContain('No doodles yet.')
  })

  it('renders one row per existing doodle', () => {
    const existing = [
      { id: 'dd_1', path_data: 'M0 0 L1 1', viewbox: '0 0 20 20' },
      { id: 'dd_2', path_data: 'M2 2 L3 3', viewbox: '0 0 20 20' },
    ]
    createDoodleWidget(existing, { container, onSave })
    expect(container.querySelectorAll('.__doodle-row').length).toBe(2)
  })

  it('removing an existing doodle calls onSave with it excluded', () => {
    const existing = [
      { id: 'dd_1', path_data: 'M0 0 L1 1', viewbox: '0 0 20 20' },
      { id: 'dd_2', path_data: 'M2 2 L3 3', viewbox: '0 0 20 20' },
    ]
    createDoodleWidget(existing, { container, onSave })

    container.querySelector('.__doodle-remove').click()

    expect(onSave).toHaveBeenCalledOnce()
    const saved = onSave.mock.calls[0][0]
    expect(saved).toHaveLength(1)
    expect(saved[0].id).toBe('dd_2')
    // Original array passed in is untouched (widget owns its own copy).
    expect(existing).toHaveLength(2)
  })

  it('"+ Draw new doodle" mounts a canvas', () => {
    createDoodleWidget([], { container, onSave })
    container.querySelector('button').click() // only button present: the draw trigger
    expect(container.querySelector('.__doodle-canvas')).not.toBeNull()
  })

  it('drawing a stroke and saving appends a new doodle and calls onSave', () => {
    createDoodleWidget([], { container, onSave })
    const drawBtn = [...container.querySelectorAll('button')].find(b => b.textContent.includes('Draw'))
    drawBtn.click()

    const canvas = container.querySelector('.__doodle-canvas')
    draw(canvas, [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 20, y: 0 }])

    const saveBtn = [...container.querySelectorAll('button')].find(b => b.textContent === 'Save doodle')
    saveBtn.click()

    expect(onSave).toHaveBeenCalledOnce()
    const saved = onSave.mock.calls[0][0]
    expect(saved).toHaveLength(1)
    expect(saved[0].path_data).toBe(pointsToPathData([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 20, y: 0 }]))
    expect(saved[0].viewbox).toBe('0 0 220 220')
    expect(saved[0].placement.base.mode).toBe('absolute')
    // Canvas closes after a successful save.
    expect(container.querySelector('.__doodle-canvas-wrap')).toBeNull()
    // List re-renders to show the newly added doodle.
    expect(container.querySelectorAll('.__doodle-row').length).toBe(1)
  })

  it('saving with no strokes drawn does not add a doodle or call onSave', () => {
    createDoodleWidget([], { container, onSave })
    const drawBtn = [...container.querySelectorAll('button')].find(b => b.textContent.includes('Draw'))
    drawBtn.click()

    const saveBtn = [...container.querySelectorAll('button')].find(b => b.textContent === 'Save doodle')
    saveBtn.click()

    expect(onSave).not.toHaveBeenCalled()
    expect(container.querySelectorAll('.__doodle-row').length).toBe(0)
  })

  it('Cancel closes the canvas without saving', () => {
    createDoodleWidget([], { container, onSave })
    const drawBtn = [...container.querySelectorAll('button')].find(b => b.textContent.includes('Draw'))
    drawBtn.click()

    const canvas = container.querySelector('.__doodle-canvas')
    draw(canvas, [{ x: 0, y: 0 }, { x: 10, y: 0 }])

    const cancelBtn = [...container.querySelectorAll('button')].find(b => b.textContent === 'Cancel')
    cancelBtn.click()

    expect(onSave).not.toHaveBeenCalled()
    expect(container.querySelector('.__doodle-canvas-wrap')).toBeNull()
  })
})

describe('boundingBoxOf', () => {
  it('spans every point across every stroke', () => {
    const box = boundingBoxOf([
      [{ x: 10, y: 20 }, { x: 30, y: 5 }],
      [{ x: 0, y: 50 }, { x: 40, y: 15 }],
    ])
    expect(box).toEqual({ minX: 0, minY: 5, maxX: 40, maxY: 50, width: 40, height: 45 })
  })

  it('collapses to a point for a single-point stroke', () => {
    const box = boundingBoxOf([[{ x: 7, y: 7 }]])
    expect(box).toEqual({ minX: 7, minY: 7, maxX: 7, maxY: 7, width: 0, height: 0 })
  })
})

describe('strokesToDoodle', () => {
  const anchorRect = { width: 400, height: 200 }

  it('computes placement as a percentage of the anchor, from where the stroke was actually drawn', () => {
    // Canvas is anchor + 70px margin on each side (MARGIN in the module).
    // A stroke drawn at canvas (70, 70) sits exactly at the anchor's own
    // top-left corner (0%, 0%) once the margin is subtracted back out.
    const doodle = strokesToDoodle([[{ x: 70, y: 70 }, { x: 90, y: 70 }]], anchorRect)
    // EDGE_PAD (4px) shifts the origin slightly before the margin subtraction.
    expect(doodle.placement.base.mode).toBe('absolute')
    expect(doodle.placement.base.top.unit).toBe('%')
    expect(doodle.placement.base.left.unit).toBe('%')
    // (70 - 4 - 70) / 400 * 100 = -1%, (70 - 4 - 70) / 200 * 100 = -2%
    expect(doodle.placement.base.left.value).toBeCloseTo(-1, 5)
    expect(doodle.placement.base.top.value).toBeCloseTo(-2, 5)
  })

  it('produces a viewbox tightly cropped to the drawn strokes, not the whole canvas', () => {
    const doodle = strokesToDoodle([[{ x: 100, y: 100 }, { x: 120, y: 110 }]], anchorRect)
    // width 20 + height 10, padded by EDGE_PAD (4px) on each side.
    expect(doodle.viewbox).toBe('0 0 28 18')
  })

  it('rebases path_data into the cropped viewbox’s own coordinate space', () => {
    const doodle = strokesToDoodle([[{ x: 100, y: 100 }, { x: 120, y: 100 }]], anchorRect)
    // origin = (100 - 4, 100 - 4) = (96, 96) — first point rebases to (4, 4).
    expect(doodle.path_data.startsWith('M 4 4')).toBe(true)
  })

  it('gives every doodle a unique id', () => {
    const a = strokesToDoodle([[{ x: 0, y: 0 }, { x: 1, y: 1 }]], anchorRect)
    const b = strokesToDoodle([[{ x: 0, y: 0 }, { x: 1, y: 1 }]], anchorRect)
    expect(a.id).not.toBe(b.id)
  })
})

describe('createDoodleWidget: on-page draw mode (a real anchorEl is provided)', () => {
  let container, anchor, handleEl, overlayEl, onSave

  beforeEach(() => {
    document.body.innerHTML = ''
    container = document.createElement('div')
    document.body.appendChild(container)

    // Mirrors DoodleOverlay's real structure: the field marker (what
    // edit-mode.js passes as ctx.anchorEl) and the decorative .doodle-overlay
    // are both direct children of the CSS anchor — the marker's
    // parentElement, not its offsetParent (see the comment on the "+ Draw
    // new doodle" handler in the module under test for why).
    anchor = document.createElement('article')
    handleEl = document.createElement('div')
    overlayEl = document.createElement('div')
    overlayEl.className = 'doodle-overlay'
    anchor.appendChild(overlayEl)
    anchor.appendChild(handleEl)
    document.body.appendChild(anchor)

    onSave = vi.fn()
    stubCanvasContext()

    // jsdom doesn't compute real layout — stub rects so the placement math
    // has something real to work with. Anchor: 400x200 at (100, 50). The
    // on-page canvas is a child of the anchor with `inset: -70px`, so it's
    // asserted separately below at the geometry that CSS would produce.
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function () {
      if (this === anchor) return { left: 100, top: 50, width: 400, height: 200, right: 500, bottom: 250 }
      if (this.classList?.contains('__doodle-onpage-canvas')) {
        return { left: 30, top: -20, width: 540, height: 340, right: 570, bottom: 320 }
      }
      return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }
    })
  })

  it('closes the floating panel and draws on the anchor itself, not in the container', () => {
    const close = vi.fn()
    createDoodleWidget([], { container, anchorEl: handleEl, close, onSave })

    const drawBtn = [...container.querySelectorAll('button')].find(b => b.textContent.includes('Draw'))
    drawBtn.click()

    expect(close).toHaveBeenCalledOnce()
    expect(container.querySelector('.__doodle-onpage-canvas')).toBeNull()
    expect(anchor.querySelector('.__doodle-onpage-canvas')).not.toBeNull()
  })

  it('sizes the canvas in explicit px — CSS auto-sizing has two different footguns here: <canvas> is a replaced element (ignores inset, keeps its 300x150 default), and adding width/height:100% alongside inset over-constrains the box and cuts right/bottom short instead', () => {
    createDoodleWidget([], { container, anchorEl: handleEl, close: vi.fn(), onSave })
    container.querySelector('button').click()

    const canvas = anchor.querySelector('.__doodle-onpage-canvas')
    // Mocked anchor rect is 400x200 (see beforeEach) — MARGIN (70) on every side.
    expect(canvas.style.top).toBe('-70px')
    expect(canvas.style.left).toBe('-70px')
    expect(canvas.style.width).toBe('540px')
    expect(canvas.style.height).toBe('340px')
    expect(canvas.width).toBe(540)
    expect(canvas.height).toBe(340)
  })

  it('mounts the toolbar fixed to the viewport, not inside the anchor or the panel', () => {
    createDoodleWidget([], { container, anchorEl: handleEl, close: vi.fn(), onSave })
    container.querySelector('button').click()

    const toolbar = document.querySelector('.__doodle-onpage-toolbar')
    expect(toolbar).not.toBeNull()
    expect(toolbar.parentElement).toBe(document.body)
    expect(getComputedStyle(toolbar).position).toBe('fixed')
  })

  it('drawing and saving computes a real placement from where you drew, and calls onSave', () => {
    createDoodleWidget([], { container, anchorEl: handleEl, close: vi.fn(), onSave })
    container.querySelector('button').click()

    const canvas = anchor.querySelector('.__doodle-onpage-canvas')
    draw(canvas, [{ x: 100, y: 100 }, { x: 150, y: 120 }])

    const saveBtn = [...document.querySelectorAll('.__doodle-onpage-toolbar button')].find(b => b.textContent === 'Save doodle')
    saveBtn.click()

    expect(onSave).toHaveBeenCalledOnce()
    const saved = onSave.mock.calls[0][0]
    expect(saved).toHaveLength(1)
    // Not the old fixed placeholder (20%, 20%) — a real computed position.
    expect(saved[0].placement.base.top.value).not.toBe(20)
    expect(saved[0].placement.base.left.value).not.toBe(20)
    // Canvas and toolbar are cleaned up after a save.
    expect(document.querySelector('.__doodle-onpage-canvas')).toBeNull()
    expect(document.querySelector('.__doodle-onpage-toolbar')).toBeNull()
  })

  it('renders the new doodle into the live .doodle-overlay immediately, without waiting for publish/reload', () => {
    createDoodleWidget([], { container, anchorEl: handleEl, close: vi.fn(), onSave })
    expect(overlayEl.querySelectorAll('.doodle-overlay__item')).toHaveLength(0)

    container.querySelector('button').click()
    const canvas = anchor.querySelector('.__doodle-onpage-canvas')
    draw(canvas, [{ x: 100, y: 100 }, { x: 150, y: 120 }])
    ;[...document.querySelectorAll('.__doodle-onpage-toolbar button')].find(b => b.textContent === 'Save doodle').click()

    const rendered = overlayEl.querySelectorAll('.doodle-overlay__item')
    expect(rendered).toHaveLength(1)
    const saved = onSave.mock.calls[0][0][0]
    expect(rendered[0].getAttribute('data-doodle-id')).toBe(saved.id)
    expect(rendered[0].getAttribute('viewBox')).toBe(saved.viewbox)
    expect(rendered[0].querySelector('path').getAttribute('d')).toBe(saved.path_data)
    expect(rendered[0].style.position).toBe('absolute')
  })

  it('removes a doodle from the live overlay immediately when removed from the list', () => {
    const existing = [{ id: 'dd_1', path_data: 'M0 0 L1 1', viewbox: '0 0 20 20', placement: { base: { mode: 'absolute', top: { value: 10, unit: '%' }, left: { value: 10, unit: '%' } } } }]
    createDoodleWidget(existing, { container, anchorEl: handleEl, close: vi.fn(), onSave })

    // Initial sync (pending-draft case) already rendered it on widget creation.
    expect(overlayEl.querySelectorAll('.doodle-overlay__item')).toHaveLength(1)

    container.querySelector('.__doodle-remove').click()

    expect(overlayEl.querySelectorAll('.doodle-overlay__item')).toHaveLength(0)
  })

  it('syncs the live overlay on widget creation, before any edit — a pending draft doodle from an earlier session is visible immediately', () => {
    const existing = [{ id: 'dd_pending', path_data: 'M0 0 L1 1', viewbox: '0 0 20 20', placement: { base: { mode: 'absolute', top: { value: 5, unit: '%' }, left: { value: 5, unit: '%' } } } }]
    createDoodleWidget(existing, { container, anchorEl: handleEl, close: vi.fn(), onSave })

    const rendered = overlayEl.querySelectorAll('.doodle-overlay__item')
    expect(rendered).toHaveLength(1)
    expect(rendered[0].getAttribute('data-doodle-id')).toBe('dd_pending')
    // Sync-on-create must never itself call onSave — nothing changed yet.
    expect(onSave).not.toHaveBeenCalled()
  })

  it('Cancel tears down the canvas and toolbar without calling onSave', () => {
    createDoodleWidget([], { container, anchorEl: handleEl, close: vi.fn(), onSave })
    container.querySelector('button').click()

    const canvas = anchor.querySelector('.__doodle-onpage-canvas')
    draw(canvas, [{ x: 10, y: 10 }, { x: 20, y: 20 }])

    const cancelBtn = [...document.querySelectorAll('.__doodle-onpage-toolbar button')].find(b => b.textContent === 'Cancel')
    cancelBtn.click()

    expect(onSave).not.toHaveBeenCalled()
    expect(document.querySelector('.__doodle-onpage-canvas')).toBeNull()
    expect(document.querySelector('.__doodle-onpage-toolbar')).toBeNull()
  })

  it('falls back to the panel canvas when anchorEl has no parentElement (no live page context)', () => {
    const detachedHandle = document.createElement('div') // never appended — no parentElement
    createDoodleWidget([], { container, anchorEl: detachedHandle, close: vi.fn(), onSave })
    container.querySelector('button').click()

    expect(container.querySelector('.__doodle-canvas')).not.toBeNull()
    expect(document.querySelector('.__doodle-onpage-canvas')).toBeNull()
  })
})
