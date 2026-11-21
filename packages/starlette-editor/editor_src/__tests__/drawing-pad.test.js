// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import {
  smoothPath, strokeToPaths, applyStyle, styleOf, toValue, isClosed, createDrawingPad,
  parsePath, buildPath, displace, warp, REACH,
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

describe('reshape geometry', () => {
  it('parsePath and buildPath round-trip what the pad writes', () => {
    const d = smoothPath(line(), true)
    expect(buildPath(parsePath(d))).toBe(d)
  })
  it('leaves paths it cannot read alone', () => {
    expect(parsePath('M 1 1 h 5 Z')).toBeNull()
    const p = { d: 'M 1 1 A 2 2 0 0 1 5 5', fill: 'none' }
    expect(warp([p], { x: 1, y: 1 }, 10, 10, 20, 'off')[0]).toEqual(p)
  })
  it('moves a grabbed point all the way and a far one not at all', () => {
    const g = { x: 70, y: 50 }
    expect(displace(g, g, 5, 3, 20, 'off')).toEqual({ x: 5, y: 3 })
    expect(displace({ x: 10, y: 10 }, g, 5, 3, 20, 'off')).toEqual({ x: 0, y: 0 })
  })
  it('mirrors the pull across the axis while symmetry is on', () => {
    const g = { x: 70, y: 50 }
    expect(displace({ x: 30, y: 50 }, g, 5, 3, 20, 'fold')).toEqual({ x: -5, y: 3 })
    // A point on the axis only moves up or down.
    expect(displace({ x: 50, y: 50 }, { x: 52, y: 50 }, 5, 3, 20, 'fold').x).toBe(0)
  })
  it('keeps a symmetric shape symmetric', () => {
    const [d] = strokeToPaths([{ x: 50, y: 10 }, { x: 70, y: 30 }, { x: 72, y: 60 }, { x: 50, y: 90 }], 'fold', true)
    const [out] = warp([{ d }], { x: 71, y: 45 }, 8, -4, 25, 'fold')
    const xs = parsePath(out.d).flatMap((k) => k.pts.map((q) => q.x))
    expect(Math.min(...xs) + Math.max(...xs)).toBeCloseTo(100, 0)
  })
})

describe('createDrawingPad gestures', () => {
  const stored = {
    viewbox: '0 0 100 100',
    paths: [{ d: 'M 50 10 L 70 50 L 50 90 L 30 50 Z', fill: '#7a2e4a', stroke: '#1d2126', stroke_width: 1.5 }],
    symmetry: 'vertical',
  }
  // jsdom has no layout or PointerEvent: a 100px sheet, so pixels are viewBox units.
  function mount(value = stored) {
    const container = document.createElement('div')
    const onSave = vi.fn()
    createDrawingPad(value, { container, onSave })
    container.querySelector('.drawing-pad__svg').getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 100 })
    const sheet = container.querySelector('.drawing-pad__sheet')
    const fire = (type, id, x, y) => {
      const ev = new Event(type, { bubbles: true, cancelable: true })
      Object.assign(ev, { pointerId: id, clientX: x, clientY: y, button: 0 })
      sheet.dispatchEvent(ev)
    }
    return { container, onSave, sheet, fire }
  }

  it('Reshape pulls the shape and lands as one undo step', () => {
    const { container, onSave, fire } = mount()
    container.querySelector('[data-key="reshape"]').click()
    expect(container.querySelector('.drawing-pad__group').hidden).toBe(false)
    fire('pointerdown', 1, 70, 50)
    fire('pointermove', 1, 80, 50)
    fire('pointerup', 1, 80, 50)
    const saved = onSave.mock.calls.at(-1)[0]
    expect(saved.paths[0].d).toContain('80 50')
    expect(saved.paths[0].d).toContain('20 50') // the mirrored side follows
    expect(saved.paths[0].fill).toBe('#7a2e4a')
    container.querySelector('.drawing-pad__row button').click() // undo
    expect(onSave.mock.calls.at(-1)[0].paths[0].d).toBe(stored.paths[0].d)
  })

  it('a pinch in Reshape changes the reach and moves nothing', () => {
    const { container, onSave, fire } = mount()
    container.querySelector('[data-key="reshape"]').click()
    const calls = onSave.mock.calls.length
    fire('pointerdown', 1, 40, 50)
    fire('pointerdown', 2, 60, 50)
    fire('pointermove', 2, 80, 50) // the fingers spread from 20 to 40 apart
    fire('pointerup', 1, 40, 50)
    fire('pointerup', 2, 80, 50)
    expect(container.querySelector('input[aria-label="Reach"]').value).toBe(String(REACH.initial * 2))
    expect(onSave.mock.calls.length).toBe(calls)
    expect(container.querySelector('.drawing-pad__svg g path').getAttribute('d')).toBe(stored.paths[0].d)
  })

  it('a second finger cancels the stroke being drawn', () => {
    const { onSave, fire } = mount(null)
    fire('pointerdown', 1, 50, 10)
    fire('pointermove', 1, 60, 30)
    fire('pointermove', 1, 65, 50)
    fire('pointerdown', 2, 20, 20)
    fire('pointermove', 1, 60, 80)
    fire('pointerup', 1, 60, 80)
    fire('pointerup', 2, 20, 20)
    expect(onSave).not.toHaveBeenCalled()
  })

  it('one finger still draws', () => {
    const { onSave, fire } = mount(null)
    fire('pointerdown', 1, 50, 10)
    fire('pointermove', 1, 60, 30)
    fire('pointermove', 1, 65, 50)
    fire('pointerup', 1, 50, 90)
    expect(onSave.mock.calls.at(-1)[0].paths).toHaveLength(1)
  })
})
