// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  smoothPath, strokeToPaths, applyStyle, styleOf, toValue, isClosed, createDrawingPad,
  parsePath, buildPath, displace, warp, REACH,
} from '../components/drawing-pad.js'

// The pad draws on its own screen, appended to the body when opened.
function openPad(value, onSave = () => {}) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  createDrawingPad(value, { container, onSave })
  container.querySelector('.drawing-pad__open').click()
  const screen = document.querySelector('.drawing-pad-screen')
  screen.querySelector('[data-key="draw"]').click() // the tool outlives a pad
  return { container, screen }
}

afterEach(() => {
  document.querySelector('.drawing-pad__done')?.click()
  document.body.replaceChildren()
})

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

  it('renders the stored paths in the preview and on the screen', () => {
    const { container, screen } = openPad(stored)
    expect(container.querySelectorAll('.drawing-pad__thumb-svg path')).toHaveLength(2)
    expect(screen.querySelectorAll('.drawing-pad__svg g path')).toHaveLength(2)
    expect(container.querySelector('.drawing-pad__open').textContent).toBe('Edit shape')
  })

  it('opens full screen with the page locked, and Done closes it', () => {
    const { container, screen } = openPad(null)
    expect(container.querySelector('.drawing-pad__open').textContent).toBe('Draw shape')
    expect(screen.isConnected).toBe(true)
    expect(document.documentElement.classList.contains('drawing-pad-open')).toBe(true)
    screen.querySelector('.drawing-pad__done').click()
    expect(screen.isConnected).toBe(false)
    expect(document.documentElement.classList.contains('drawing-pad-open')).toBe(false)
  })

  it('Escape closes the screen', () => {
    const { screen } = openPad(stored)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(screen.isConnected).toBe(false)
  })

  it('opening puts away the keyboard of a field being typed in', () => {
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    openPad(stored)
    expect(document.activeElement).not.toBe(input)
  })

  it('Style opens and closes the tray', () => {
    const { screen } = openPad(stored)
    const btn = screen.querySelector('.drawing-pad__style-btn')
    btn.click()
    expect(screen.classList.contains('drawing-pad-screen--tray')).toBe(true)
    btn.click()
    expect(screen.classList.contains('drawing-pad-screen--tray')).toBe(false)
  })

  it('recolours the whole drawing from a swatch, keeping open lines unfilled', () => {
    const onSave = vi.fn()
    const { container, screen } = openPad(stored, onSave)
    screen.querySelector('.drawing-pad__swatches [data-color="#3f6e5a"]').click()
    const saved = onSave.mock.calls.at(-1)[0]
    expect(saved.paths[0].fill).toBe('#3f6e5a')
    expect(saved.paths[1].fill).toBe('none')
    expect(saved.paths.every((p) => p.stroke === '#1d2126')).toBe(true)
    expect(container.querySelector('.drawing-pad__thumb-svg path').getAttribute('fill')).toBe('#3f6e5a')
  })

  it('undo restores the colours; clear saves null after a second tap', () => {
    const onSave = vi.fn()
    const { screen } = openPad(stored, onSave)
    screen.querySelector('[data-color="#3f6e5a"]').click()
    const [undo] = screen.querySelectorAll('.drawing-pad__bar .drawing-pad__row button')
    const clear = screen.querySelector('.drawing-pad__clear')
    undo.click()
    expect(onSave.mock.calls.at(-1)[0].paths[0].fill).toBe('#7a2e4a')
    clear.click()
    expect(onSave.mock.calls.at(-1)[0]).not.toBeNull()
    clear.click()
    expect(onSave.mock.calls.at(-1)[0]).toBeNull()
  })

  it('starts in Fold for a new drawing and Off for one stored without symmetry', () => {
    const a = openPad(null).screen
    expect(a.querySelector('[data-key="fold"]').getAttribute('aria-pressed')).toBe('true')
    a.querySelector('.drawing-pad__done').click()
    const b = openPad({ ...stored, symmetry: undefined }).screen
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
    const onSave = vi.fn()
    const { container, screen } = openPad(value, onSave)
    screen.querySelector('.drawing-pad__svg').getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 100 })
    const sheet = screen.querySelector('.drawing-pad__sheet')
    const fire = (type, id, x, y, extra = {}) => {
      const ev = new Event(type, { bubbles: true, cancelable: true })
      Object.assign(ev, { pointerId: id, clientX: x, clientY: y, button: 0, ...extra })
      sheet.dispatchEvent(ev)
    }
    return { container: screen, form: container, onSave, sheet, fire }
  }

  it('Reshape pulls the shape and lands as one undo step', () => {
    const { container, onSave, fire } = mount()
    container.querySelector('[data-key="reshape"]').click()
    expect(container.querySelector('.drawing-pad__reach').hidden).toBe(false)
    fire('pointerdown', 1, 70, 50)
    fire('pointermove', 1, 80, 50)
    fire('pointerup', 1, 80, 50)
    const saved = onSave.mock.calls.at(-1)[0]
    expect(saved.paths[0].d).toContain('80 50')
    expect(saved.paths[0].d).toContain('20 50') // the mirrored side follows
    expect(saved.paths[0].fill).toBe('#7a2e4a')
    container.querySelector('.drawing-pad__bar .drawing-pad__row button').click() // undo
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

  it('shows the reach ring as soon as Reshape is on, and it follows a pull', () => {
    const { container, fire } = mount()
    const ring = container.querySelector('.drawing-pad__ring:not(.drawing-pad__ring--mirror)')
    expect(ring.style.display).toBe('none')
    container.querySelector('[data-key="reshape"]').click()
    expect(ring.style.display).toBe('')
    fire('pointerdown', 1, 70, 50)
    fire('pointermove', 1, 80, 55)
    expect(ring.getAttribute('cx')).toBe('80')
    expect(ring.getAttribute('cy')).toBe('55')
    fire('pointerup', 1, 80, 55)
    expect(ring.style.display).toBe('') // stays where the pull ended
  })

  it('the reach slider resizes the visible ring', () => {
    const { container } = mount()
    container.querySelector('[data-key="reshape"]').click()
    const reachIn = container.querySelector('input[aria-label="Reach"]')
    reachIn.value = '40'
    reachIn.dispatchEvent(new Event('input'))
    expect(container.querySelector('.drawing-pad__ring').getAttribute('r')).toBe('40')
  })

  it('a cancelled pull is undone, not saved', () => {
    const { container, onSave, fire } = mount()
    container.querySelector('[data-key="reshape"]').click()
    const calls = onSave.mock.calls.length
    fire('pointerdown', 1, 70, 50)
    fire('pointermove', 1, 85, 50)
    fire('pointercancel', 1, 85, 50)
    expect(onSave.mock.calls.length).toBe(calls)
    expect(container.querySelector('.drawing-pad__svg g path').getAttribute('d')).toBe(stored.paths[0].d)
  })

  it('a cancelled stroke is dropped', () => {
    const { onSave, fire } = mount(null)
    fire('pointerdown', 1, 50, 10)
    fire('pointermove', 1, 60, 30)
    fire('pointermove', 1, 65, 50)
    fire('pointercancel', 1, 65, 50)
    expect(onSave).not.toHaveBeenCalled()
  })

  it('after a pinch, the next single finger pulls again', () => {
    const { container, onSave, fire } = mount()
    container.querySelector('[data-key="reshape"]').click()
    fire('pointerdown', 1, 40, 50)
    fire('pointerdown', 2, 60, 50)
    fire('pointerup', 1, 40, 50)
    fire('pointerup', 2, 60, 50)
    fire('pointerdown', 3, 70, 50)
    fire('pointermove', 3, 80, 50)
    fire('pointerup', 3, 80, 50)
    expect(onSave.mock.calls.at(-1)[0].paths[0].d).toContain('80 50')
  })

  it('Reshape stays on when the form rebuilds the pad', () => {
    const { container } = mount()
    container.querySelector('[data-key="reshape"]').click()
    container.querySelector('.drawing-pad__done').click()
    const c2 = document.createElement('div')
    document.body.appendChild(c2)
    createDrawingPad(stored, { container: c2, onSave: () => {} })
    c2.querySelector('.drawing-pad__open').click()
    expect(document.querySelector('.drawing-pad-screen [data-key="reshape"]').getAttribute('aria-pressed')).toBe('true')
  })
})
