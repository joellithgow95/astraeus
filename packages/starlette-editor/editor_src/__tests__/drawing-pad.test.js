// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import {
  smoothPath, strokeToPaths, applyStyle, styleOf, toValue, isClosed, createDrawingPad,
} from '../components/drawing-pad.js'

const line = (n = 10) => Array.from({ length: n }, (_, i) => ({ x: 30 + i * 2, y: 10 + i * 6 }))

describe('smoothPath', () => {
  it('returns empty for a stroke too short to draw', () => {
    expect(smoothPath([], false)).toBe('')
    expect(smoothPath([{ x: 1, y: 1 }, { x: 1.2, y: 1.1 }], false)).toBe('')
  })
  it('closes with Z only when asked', () => {
    expect(smoothPath(line(), true)).toMatch(/Z$/)
    expect(smoothPath(line(), false)).not.toMatch(/Z/)
  })
  it('only writes path characters the CMS accepts', () => {
    expect(smoothPath(line(), true)).toMatch(/^[MQLZ0-9.\s-]+$/)
  })
})

describe('strokeToPaths', () => {
  it('fold makes one symmetric path', () => {
    const [d] = strokeToPaths(line(), 'fold', true)
    expect(strokeToPaths(line(), 'fold', true)).toHaveLength(1)
    expect(d).toContain('70') // 100 - 30: the mirrored start
  })
  it('mirror makes two paths, off makes one', () => {
    expect(strokeToPaths(line(), 'mirror', false)).toHaveLength(2)
    expect(strokeToPaths(line(), 'off', false)).toHaveLength(1)
  })
})

describe('applyStyle', () => {
  const style = { fill: '#e9c46a', stroke: '#3f6e5a', width: 2 }
  it('gives every path the one style, and open paths no fill', () => {
    const out = applyStyle([{ d: 'M 1 1 L 5 5 Z', fill: 'red' }, { d: 'M 1 1 L 5 5' }], style)
    expect(out[0]).toEqual({ d: 'M 1 1 L 5 5 Z', stroke: '#3f6e5a', stroke_width: 2, fill: '#e9c46a' })
    expect(out[1].fill).toBe('none')
    expect(out[1].stroke).toBe('#3f6e5a')
  })
  it('no outline means width 0', () => {
    expect(applyStyle([{ d: 'M 1 1 L 2 2 Z' }], { ...style, stroke: 'none' })[0].stroke_width).toBe(0)
  })
})

describe('styleOf', () => {
  it('reads the style back from stored paths', () => {
    const paths = applyStyle([{ d: 'M 1 1 L 2 2 Z' }], { fill: '#111111', stroke: '#222222', width: 3 })
    expect(styleOf(paths)).toEqual({ fill: '#111111', stroke: '#222222', width: 3 })
  })
  it('falls back for an empty drawing', () => {
    expect(styleOf([], { fill: 'a', stroke: 'b', width: 1 })).toEqual({ fill: 'a', stroke: 'b', width: 1 })
  })
})

describe('toValue', () => {
  it('is null when empty and carries symmetry unless off', () => {
    expect(toValue([], 'fold')).toBeNull()
    expect(toValue([{ d: 'M 1 1 L 2 2' }], 'fold').symmetry).toBe('vertical')
    expect(toValue([{ d: 'M 1 1 L 2 2' }], 'off').symmetry).toBeUndefined()
  })
})

describe('createDrawingPad', () => {
  const stored = {
    viewbox: '0 0 100 100',
    paths: [
      { d: 'M 50 10 L 60 50 L 50 90 Z', fill: '#7a2e4a', stroke: '#1d2126', stroke_width: 1.5 },
      { d: 'M 40 30 L 60 30', fill: 'none', stroke: '#1d2126', stroke_width: 1.5 },
    ],
    symmetry: 'vertical',
  }

  it('renders the stored paths', () => {
    const container = document.createElement('div')
    createDrawingPad(stored, { container, onSave: () => {} })
    expect(container.querySelectorAll('.drawing-pad__svg g path')).toHaveLength(2)
  })

  it('recolours the whole drawing from a swatch, keeping open lines unfilled', () => {
    const container = document.createElement('div')
    const onSave = vi.fn()
    createDrawingPad(stored, { container, onSave })
    container.querySelector('.drawing-pad__swatches [data-color="#3f6e5a"]').click()
    const saved = onSave.mock.calls.at(-1)[0]
    expect(saved.paths[0].fill).toBe('#3f6e5a')
    expect(saved.paths[1].fill).toBe('none')
    expect(saved.paths.every((p) => p.stroke === '#1d2126')).toBe(true)
  })

  it('undo restores the colours; clear saves null after a second tap', () => {
    const container = document.createElement('div')
    const onSave = vi.fn()
    createDrawingPad(stored, { container, onSave })
    container.querySelector('[data-color="#3f6e5a"]').click()
    const [undo, , clear] = container.querySelectorAll('.drawing-pad__row button')
    undo.click()
    expect(onSave.mock.calls.at(-1)[0].paths[0].fill).toBe('#7a2e4a')
    clear.click()
    expect(onSave.mock.calls.at(-1)[0]).not.toBeNull()
    clear.click()
    expect(onSave.mock.calls.at(-1)[0]).toBeNull()
  })

  it('starts in Fold for a new drawing and Off for one stored without symmetry', () => {
    const a = document.createElement('div')
    createDrawingPad(null, { container: a, onSave: () => {} })
    expect(a.querySelector('[data-key="fold"]').getAttribute('aria-pressed')).toBe('true')
    const b = document.createElement('div')
    createDrawingPad({ ...stored, symmetry: undefined }, { container: b, onSave: () => {} })
    expect(b.querySelector('[data-key="off"]').getAttribute('aria-pressed')).toBe('true')
  })

  it('isClosed reads the Z', () => {
    expect(isClosed('M 1 1 Z')).toBe(true)
    expect(isClosed('M 1 1 L 2 2')).toBe(false)
  })
})
