/**
 * components/drawing-pad.js — the widget for a DrawingField (field_type
 * `drawing`): one hand-drawn shape, such as a taste shape.
 *
 * A square sheet in a 0–100 viewBox. Each stroke becomes one path; with
 * Fold on, one side is drawn and joined to its mirror as one symmetric path.
 * A stroke either closes (and takes the fill) or stays open as a line, for
 * notes and nodules on a shape.
 *
 * Two tools: Draw adds strokes; Reshape pulls the drawing like clay: press
 * on it and drag, and every point within the reach follows, less the further
 * it is (mirrored across the axis while symmetry is on). On a touch screen,
 * pinching on the sheet in Reshape grows or shrinks the reach. A second
 * finger never draws: it cancels the stroke or pull in progress.
 *
 * One style colours the whole drawing: a fill, an outline colour and an
 * outline width. Changing any of them restyles every path, so a shape is
 * drawn once and recoloured after. The stored value is the shared drawing
 * format (components/drawing.js, ``starlette_cms.drawing.Drawing``), with the
 * style written on each path.
 *
 * Saves through `ctx.onSave` after every change (field-sync batches them);
 * an empty sheet saves `null`.
 */

import { el } from '../utils.js'

const SVG_NS = 'http://www.w3.org/2000/svg'
export const VIEWBOX = '0 0 100 100'

export const FILLS = ['#7a2e4a', '#b5463c', '#d98f2b', '#e9c46a', '#8aa05b', '#3f6e5a', '#4b6c9e', '#2a2f45', '#c9ccd1', 'none']
export const OUTLINES = ['#1d2126', '#5a3a2a', '#7a2e4a', '#3f6e5a', '#4b6c9e', '#ffffff', 'none']
export const DEFAULT_STYLE = { fill: '#7a2e4a', stroke: '#1d2126', width: 1.5 }

const r1 = (n) => Math.round(n * 10) / 10

/**
 * Smooth a stroke (points in viewBox units) into path data: decimate, then
 * quadratic segments through consecutive midpoints.
 *
 * @param {{x: number, y: number}[]} pts
 * @param {boolean} closed  end with `Z`
 * @returns {string} '' when the stroke is too short to draw
 */
export function smoothPath(pts, closed) {
  if (!pts.length) return ''
  const dec = [pts[0]]
  for (const p of pts.slice(1)) {
    const l = dec[dec.length - 1]
    if (Math.hypot(p.x - l.x, p.y - l.y) >= 0.8) dec.push(p)
  }
  if (dec.length < 2) return ''
  let d = `M ${r1(dec[0].x)} ${r1(dec[0].y)}`
  for (let i = 1; i < dec.length; i++) {
    const a = dec[i - 1]
    const b = dec[i]
    d += ` Q ${r1(a.x)} ${r1(a.y)} ${r1((a.x + b.x) / 2)} ${r1((a.y + b.y) / 2)}`
  }
  const last = dec[dec.length - 1]
  d += ` L ${r1(last.x)} ${r1(last.y)}`
  return closed ? `${d} Z` : d
}

export const mirrorPoints = (pts) => pts.map((p) => ({ x: 100 - p.x, y: p.y }))

/**
 * The path data a finished stroke adds, for a symmetry mode.
 *
 * @param {{x: number, y: number}[]} pts
 * @param {'fold' | 'mirror' | 'off'} sym
 * @param {boolean} closed
 * @returns {string[]}
 */
export function strokeToPaths(pts, sym, closed) {
  let out
  if (sym === 'fold') out = [smoothPath([...pts, ...mirrorPoints(pts).reverse()], closed)]
  else if (sym === 'mirror') out = [smoothPath(pts, closed), smoothPath(mirrorPoints(pts), closed)]
  else out = [smoothPath(pts, closed)]
  return out.filter(Boolean)
}

export const isClosed = (d) => /Z\s*$/i.test(d)

/**
 * Give every path the one style. An open path never takes the fill.
 *
 * @param {{d: string}[]} paths
 * @param {{fill: string, stroke: string, width: number}} style
 */
export function applyStyle(paths, { fill, stroke, width }) {
  return paths.map((p) => ({
    d: p.d,
    stroke,
    stroke_width: stroke === 'none' ? 0 : width,
    fill: isClosed(p.d) ? fill : 'none',
  }))
}

/**
 * Read the style back from a stored drawing (an older one may have had a
 * colour per path: the first filled path and the first outlined one win).
 */
export function styleOf(paths, fallback = DEFAULT_STYLE) {
  const style = { ...fallback }
  const filled = paths.find((p) => isClosed(p.d ?? '') && p.fill && p.fill !== 'none')
  if (filled) style.fill = filled.fill
  const outlined = paths.find((p) => p.stroke && p.stroke !== 'none')
  if (outlined) {
    style.stroke = outlined.stroke
    if (outlined.stroke_width) style.width = outlined.stroke_width
  } else if (paths.length) {
    style.stroke = 'none'
  }
  return style
}

/** The stored value for a set of paths: a drawing, or null when empty. */
export function toValue(paths, sym) {
  if (!paths.length) return null
  return { viewbox: VIEWBOX, paths: paths.map((p) => ({ ...p })), ...(sym !== 'off' ? { symmetry: 'vertical' } : {}) }
}

// ---- reshape: pull the drawing like clay ----

const ABS_POINT_CMDS = /^[MLQCSTZ]$/

/**
 * Read path data back into commands with their points. Only absolute
 * M/L/Q/C/S/T/Z (what the pad writes); anything else returns null, and the
 * path is left as it is.
 *
 * @param {string} d
 * @returns {{c: string, pts: {x: number, y: number}[]}[] | null}
 */
export function parsePath(d) {
  const cmds = []
  for (const m of d.matchAll(/([A-Za-z])([^A-Za-z]*)/g)) {
    if (!ABS_POINT_CMDS.test(m[1])) return null
    const nums = (m[2].match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) || []).map(Number)
    if (nums.length % 2) return null
    const pts = []
    for (let i = 0; i < nums.length; i += 2) pts.push({ x: nums[i], y: nums[i + 1] })
    cmds.push({ c: m[1], pts })
  }
  return cmds
}

/** Write commands from parsePath back as path data. */
export function buildPath(cmds) {
  return cmds.map((k) => (k.c === 'Z' ? 'Z' : `${k.c} ${k.pts.map((q) => `${r1(q.x)} ${r1(q.y)}`).join(' ')}`)).join(' ')
}

const smoothstep = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t))
const falloff = (dist, reach) => smoothstep(1 - dist / reach)

/**
 * How far point p moves when the grab at g moves by (dx, dy). With symmetry
 * on the pull is mirrored: the far side moves the mirrored way, and points on
 * the axis only move up or down, so a symmetric shape stays symmetric.
 */
export function displace(p, g, dx, dy, reach, sym) {
  if (sym === 'off') {
    const w = falloff(Math.hypot(p.x - g.x, p.y - g.y), reach)
    return { x: dx * w, y: dy * w }
  }
  const side = g.x >= 50 ? 1 : -1
  const same = (p.x - 50) * side >= 0
  const qx = same ? p.x : 100 - p.x
  const w = falloff(Math.hypot(qx - g.x, p.y - g.y), reach)
  const offAxis = smoothstep(Math.abs(p.x - 50) / 3)
  return { x: dx * w * (same ? 1 : -1) * offAxis, y: dy * w }
}

/**
 * Pull every path: the grab at g moved by (dx, dy). Style is kept.
 *
 * @param {{d: string}[]} paths
 */
export function warp(paths, g, dx, dy, reach, sym) {
  return paths.map((p) => {
    const cmds = parsePath(p.d)
    if (!cmds) return { ...p }
    for (const k of cmds) {
      for (const q of k.pts) {
        const o = displace(q, g, dx, dy, reach, sym)
        q.x += o.x
        q.y += o.y
      }
    }
    return { ...p, d: buildPath(cmds) }
  })
}

export const REACH = { min: 6, max: 60, initial: 22 }

function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(SVG_NS, tag)
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v))
  return n
}

/**
 * Field widget factory (field-widgets.js).
 *
 * @param {object | null | undefined} value  a stored drawing
 * @param {{ container: HTMLElement, onSave: (v: object | null) => void }} ctx
 */
export function createDrawingPad(value, ctx) {
  let paths = Array.isArray(value?.paths) ? value.paths.map((p) => ({ ...p })) : []
  let style = styleOf(paths)
  let sym = paths.length && !value?.symmetry ? 'off' : 'fold'
  let closeStrokes = true
  let tool = 'draw'
  let reach = REACH.initial
  const history = []
  let future = []

  // ---- sheet ----
  const svg = svgEl('svg', { viewBox: VIEWBOX, class: 'drawing-pad__svg', role: 'img', 'aria-label': 'Drawing area' })
  const axis = svgEl('line', { class: 'drawing-pad__axis', x1: 50, y1: 0, x2: 50, y2: 100 })
  const art = svgEl('g')
  const live = svgEl('path', { fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })
  const liveMirror = svgEl('path', { fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })
  const ring = svgEl('circle', { class: 'drawing-pad__ring', r: reach })
  ring.style.display = 'none'
  svg.append(axis, art, live, liveMirror, ring)
  const hint = el('div', { class: 'drawing-pad__hint' })
  const sheet = el('div', { class: 'drawing-pad__sheet' }, svg, hint)

  // ---- controls ----
  const undoBtn = el('button', { type: 'button', class: 'btn btn--ghost' }, 'Undo')
  const redoBtn = el('button', { type: 'button', class: 'btn btn--ghost' }, 'Redo')
  const clearBtn = el('button', { type: 'button', class: 'btn btn--ghost' }, 'Clear')
  let clearArmed = null

  const seg = (label, options, get, set) => {
    const wrap = el('div', { class: 'drawing-pad__seg', role: 'group', 'aria-label': label })
    const buttons = options.map(([key, text, title]) => {
      const b = el('button', { type: 'button', title: title || '', 'data-key': key }, text)
      b.addEventListener('click', () => { set(key); sync() })
      wrap.appendChild(b)
      return b
    })
    return { wrap, update: () => buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.key === get()))) }
  }
  const toolSeg = seg('Tool', [
    ['draw', 'Draw', 'Draw new strokes'],
    ['reshape', 'Reshape', 'Press on the shape and drag: nearby parts follow'],
  ], () => tool, (k) => { tool = k })
  const symSeg = seg('Symmetry', [
    ['fold', 'Fold', 'Draw one side; it joins its mirror as one shape'],
    ['mirror', 'Mirror', 'Every stroke gets a mirrored twin'],
    ['off', 'Off'],
  ], () => sym, (k) => { sym = k; emit() })
  const closeSeg = seg('Each stroke', [
    ['on', 'Closes', 'Join the end to the start and fill it'],
    ['off', 'Stays open', 'Leave it as a line'],
  ], () => (closeStrokes ? 'on' : 'off'), (k) => { closeStrokes = k === 'on' })

  const swatchRow = (label, colors, key) => {
    const row = el('div', { class: 'drawing-pad__swatches', role: 'group', 'aria-label': label })
    const custom = el('input', { type: 'color', class: 'drawing-pad__custom', 'aria-label': `Any ${label.toLowerCase()} colour` })
    custom.addEventListener('input', () => restyle({ [key]: custom.value }))
    const buttons = colors.map((c) => {
      const b = el('button', {
        type: 'button',
        class: `drawing-pad__sw${c === 'none' ? ' drawing-pad__sw--none' : ''}`,
        'aria-label': c === 'none' ? 'None' : c,
        'data-color': c,
      })
      if (c !== 'none') b.style.background = c
      b.addEventListener('click', () => restyle({ [key]: c }))
      row.appendChild(b)
      return b
    })
    row.appendChild(custom)
    return {
      row,
      update: () => {
        buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.color === style[key])))
        if (style[key] !== 'none' && /^#[0-9a-f]{6}$/i.test(style[key])) custom.value = style[key]
      },
    }
  }
  const fillRow = swatchRow('Fill', FILLS, 'fill')
  const outlineRow = swatchRow('Outline', OUTLINES, 'stroke')

  const widthOut = el('output', { class: 'drawing-pad__width-out' })
  const widthIn = el('input', { type: 'range', min: '0.3', max: '6', step: '0.1', class: 'drawing-pad__width', 'aria-label': 'Outline width' })
  let widthBefore = null
  widthIn.addEventListener('input', () => {
    if (widthBefore == null) widthBefore = JSON.stringify(paths)
    style = { ...style, width: Number(widthIn.value) }
    paths = applyStyle(paths, style)
    sync()
  })
  widthIn.addEventListener('change', () => {
    if (widthBefore != null && widthBefore !== JSON.stringify(paths)) { history.push(widthBefore); future = [] }
    widthBefore = null
    emit()
  })

  const reachOut = el('output', { class: 'drawing-pad__width-out' })
  const reachIn = el('input', {
    type: 'range', min: String(REACH.min), max: String(REACH.max), step: '1',
    class: 'drawing-pad__width', 'aria-label': 'Reach',
  })
  reachIn.addEventListener('input', () => { reach = Number(reachIn.value); sync() })

  const label = (text, extra) => el('div', { class: 'drawing-pad__label' }, text, ...(extra ? [extra] : []))
  const reachGroup = el('div', { class: 'drawing-pad__group' }, label('Reach', reachOut), reachIn)
  const panel = el('div', { class: 'drawing-pad__controls' },
    el('div', { class: 'drawing-pad__row' }, undoBtn, redoBtn, clearBtn),
    label('Tool'), toolSeg.wrap,
    reachGroup,
    label('Symmetry'), symSeg.wrap,
    label('Each stroke'), closeSeg.wrap,
    label('Fill'), fillRow.row,
    label('Outline'), outlineRow.row,
    label('Outline width', widthOut), widthIn,
  )
  const root = el('div', { class: 'drawing-pad' }, sheet, panel)
  ctx.container.appendChild(root)

  // ---- state changes ----
  function emit() { sync(); ctx.onSave(toValue(paths, sym)) }
  function commit(next) { history.push(JSON.stringify(paths)); future = []; paths = next; emit() }
  function restyle(change) {
    style = { ...style, ...change }
    if (paths.length) commit(applyStyle(paths, style))
    else sync()
  }

  function sync() {
    art.replaceChildren(...paths.map((p) => svgEl('path', {
      d: p.d, fill: p.fill ?? 'none', stroke: p.stroke ?? 'none', 'stroke-width': p.stroke_width ?? 0,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    })))
    axis.style.display = sym === 'off' ? 'none' : ''
    hint.hidden = paths.length > 0
    hint.textContent = tool === 'reshape' ? 'Press on the shape and drag. Nearby parts follow.'
      : !closeStrokes ? 'Strokes stay open, as lines.'
      : sym === 'fold' ? 'Draw one side, top to bottom. It folds into one shape.'
        : 'Draw a loop. It closes and fills when you lift.'
    undoBtn.disabled = history.length === 0
    redoBtn.disabled = future.length === 0
    toolSeg.update(); symSeg.update(); closeSeg.update(); fillRow.update(); outlineRow.update()
    sheet.classList.toggle('drawing-pad__sheet--reshape', tool === 'reshape')
    reachGroup.hidden = tool !== 'reshape'
    reachIn.value = String(reach)
    reachOut.textContent = String(reach)
    ring.setAttribute('r', String(reach))
    widthIn.value = String(style.width)
    widthOut.textContent = Number(style.width).toFixed(1)
  }

  undoBtn.addEventListener('click', () => {
    if (!history.length) return
    future.push(JSON.stringify(paths)); paths = JSON.parse(history.pop()); style = styleOf(paths, style); emit()
  })
  redoBtn.addEventListener('click', () => {
    if (!future.length) return
    history.push(JSON.stringify(paths)); paths = JSON.parse(future.pop()); style = styleOf(paths, style); emit()
  })
  clearBtn.addEventListener('click', () => {
    if (!paths.length) return
    if (clearArmed) { clearTimeout(clearArmed); clearArmed = null; clearBtn.textContent = 'Clear'; commit([]); return }
    clearBtn.textContent = 'Tap again to clear'
    clearArmed = setTimeout(() => { clearArmed = null; clearBtn.textContent = 'Clear' }, 2500)
  })

  // ---- pointers: draw, pull, pinch ----
  // `pointers` holds every finger (or pen, or mouse) down on the sheet. One
  // pointer draws a stroke or pulls the shape; a second cancels that, and in
  // Reshape the two pinch the reach.
  const pointers = new Map()
  let pts = null    // the stroke being drawn
  let grab = null   // { g, from } while pulling
  let pinch = null  // { dist, reach } while pinching
  const toPoint = (e) => {
    const r = svg.getBoundingClientRect()
    return { x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 }
  }
  const showRing = (at) => {
    ring.setAttribute('cx', String(at.x))
    ring.setAttribute('cy', String(at.y))
    ring.style.display = ''
  }
  const pinchState = () => {
    const [a, b] = [...pointers.values()]
    return { dist: Math.hypot(a.x - b.x, a.y - b.y), mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } }
  }
  const cancelStroke = () => {
    pts = null
    live.setAttribute('d', '')
    liveMirror.setAttribute('d', '')
  }
  const cancelGrab = () => {
    if (!grab) return
    paths = grab.from
    grab = null
    sheet.classList.remove('drawing-pad__sheet--grabbing')
    sync()
  }

  sheet.addEventListener('pointerdown', (e) => {
    if (e.button > 0) return
    e.preventDefault()
    sheet.setPointerCapture?.(e.pointerId)
    pointers.set(e.pointerId, toPoint(e))
    if (pointers.size === 2) {
      // A second finger: never a stroke or a pull. In Reshape it pinches the reach.
      cancelStroke()
      cancelGrab()
      if (tool === 'reshape') {
        const { dist, mid } = pinchState()
        pinch = { dist: Math.max(dist, 1), reach }
        showRing(mid)
      }
      return
    }
    if (pointers.size > 2) return
    if (tool === 'reshape') {
      if (!paths.length) return
      grab = { g: toPoint(e), from: paths.map((p) => ({ ...p })) }
      showRing(grab.g)
      sheet.classList.add('drawing-pad__sheet--grabbing')
      return
    }
    pts = [toPoint(e)]
    for (const l of [live, liveMirror]) {
      l.setAttribute('stroke', style.stroke === 'none' ? '#8a929b' : style.stroke)
      l.setAttribute('stroke-width', String(style.stroke === 'none' ? 0.6 : style.width))
    }
  })
  sheet.addEventListener('pointermove', (e) => {
    if (!pointers.has(e.pointerId)) return
    pointers.set(e.pointerId, toPoint(e))
    if (pinch && pointers.size === 2) {
      const { dist, mid } = pinchState()
      reach = Math.round(Math.min(REACH.max, Math.max(REACH.min, pinch.reach * (dist / pinch.dist))))
      showRing(mid)
      sync()
      return
    }
    if (grab) {
      const c = toPoint(e)
      paths = warp(grab.from, grab.g, c.x - grab.g.x, c.y - grab.g.y, reach, sym)
      sync()
      return
    }
    if (!pts) return
    const events = e.getCoalescedEvents ? e.getCoalescedEvents() : [e]
    for (const ev of (events.length ? events : [e])) pts.push(toPoint(ev))
    live.setAttribute('d', smoothPath(pts, false))
    liveMirror.setAttribute('d', sym === 'off' ? '' : smoothPath(mirrorPoints(pts), false))
  })
  const finish = (e) => {
    if (!pointers.delete(e.pointerId)) return
    if (pinch) {
      if (pointers.size === 0) { pinch = null; ring.style.display = 'none' }
      return
    }
    if (grab) {
      const { from } = grab
      grab = null
      ring.style.display = 'none'
      sheet.classList.remove('drawing-pad__sheet--grabbing')
      if (JSON.stringify(paths) !== JSON.stringify(from)) { history.push(JSON.stringify(from)); future = []; emit() }
      return
    }
    if (!pts) return
    const drawn = pts
    cancelStroke()
    if (drawn.length < 3) return
    const added = strokeToPaths(drawn, sym, closeStrokes)
    if (added.length) commit(applyStyle([...paths, ...added.map((d) => ({ d }))], style))
  }
  sheet.addEventListener('pointerup', finish)
  sheet.addEventListener('pointercancel', finish)

  sync()
  return root
}
