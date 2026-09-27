// @vitest-environment jsdom
/**
 * Tests for components/doodle-widget.js — the 'doodles' field widget.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createDoodleWidget, pointsToPathData } from '../components/doodle-widget.js'

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
