/**
 * components/doodle-widget.js — freehand SVG doodle capture.
 *
 * Registered as the 'doodles' field widget (see field-widgets.js). Drawing
 * happens directly on the live page, not in an isolated panel: the canvas
 * is inserted as a child of the doodle's actual CSS anchor (the field
 * marker's own parentElement — DoodleOverlay renders the marker and the
 * decorative overlay as siblings, both direct children of whatever
 * position:relative container it's invoked inside, which is definitionally
 * the same element `placement` coordinates are measured from — not
 * offsetParent, which depends on computed layout a test environment may not
 * fully implement, and isn't actually a more correct answer here anyway),
 * sized to the anchor plus a fixed bleed margin (Option B from the
 * draw-surface review — bounded, so the coordinate math stays simple and a
 * doodle can't wander into unrelated page content, while still letting it
 * visually spill past the card's own edges).
 *
 * mode: 'inline' (no live page — e.g. the admin form, no anchor to draw
 * against) falls back to a small self-contained canvas in the widget's own
 * container instead.
 */

const MARGIN = 70 // px bleed around the anchor, validated interactively against Option A/B/C
const EDGE_PAD = 4 // px — keeps a stroke right at the bounding edge from being clipped by the viewBox
const FALLBACK_CANVAS_SIZE = 220 // px — inline-mode-only, no anchor to size against
const POINT_MIN_DISTANCE = 3 // px — decimates high-frequency pointermove samples

let _idCounter = 0
function _nextId() {
  _idCounter += 1
  return `dd_${Date.now().toString(36)}_${_idCounter}`
}

function _round2(n) {
  return Math.round(n * 100) / 100
}

/**
 * Decimate a raw point list by minimum distance, then fit a smooth path
 * through it (quadratic segments through consecutive midpoints — enough for
 * a small decorative doodle, no curve-fitting dependency needed).
 *
 * @param {{x: number, y: number}[]} points
 * @returns {string} SVG path `d` attribute value
 */
export function pointsToPathData(points) {
  if (points.length === 0) return ''

  const decimated = [points[0]]
  for (const p of points.slice(1)) {
    const last = decimated[decimated.length - 1]
    const dx = p.x - last.x
    const dy = p.y - last.y
    if (Math.sqrt(dx * dx + dy * dy) >= POINT_MIN_DISTANCE) decimated.push(p)
  }

  if (decimated.length < 2) {
    const p = decimated[0]
    return `M ${p.x} ${p.y} L ${p.x} ${p.y}`
  }

  let d = `M ${decimated[0].x} ${decimated[0].y}`
  for (let i = 1; i < decimated.length; i++) {
    const prev = decimated[i - 1]
    const cur = decimated[i]
    const midX = (prev.x + cur.x) / 2
    const midY = (prev.y + cur.y) / 2
    d += ` Q ${prev.x} ${prev.y} ${midX} ${midY}`
  }
  const last = decimated[decimated.length - 1]
  d += ` L ${last.x} ${last.y}`
  return d
}

/**
 * Bounding box of every point across every stroke, in the same coordinate
 * space the strokes were recorded in.
 *
 * @param {{x: number, y: number}[][]} strokes
 */
export function boundingBoxOf(strokes) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const stroke of strokes) {
    for (const p of stroke) {
      if (p.x < minX) minX = p.x
      if (p.y < minY) minY = p.y
      if (p.x > maxX) maxX = p.x
      if (p.y > maxY) maxY = p.y
    }
  }
  return { minX, minY, maxX, maxY, width: maxX - minX, height: maxY - minY }
}

/**
 * Convert a drawn-and-cropped stroke set into a doodle record, with
 * placement computed relative to `anchorRect` (the doodle's real CSS
 * positioning root) from where it was actually drawn on the draw canvas
 * (which sits at `anchorRect` expanded by `MARGIN` on every side).
 *
 * @param {{x: number, y: number}[][]} nonEmptyStrokes
 * @param {DOMRect} anchorRect
 */
export function strokesToDoodle(nonEmptyStrokes, anchorRect) {
  const box = boundingBoxOf(nonEmptyStrokes)
  const originX = box.minX - EDGE_PAD
  const originY = box.minY - EDGE_PAD
  const w = box.width + EDGE_PAD * 2
  const h = box.height + EDGE_PAD * 2

  const rebased = nonEmptyStrokes.map((stroke) => stroke.map((p) => ({ x: p.x - originX, y: p.y - originY })))
  const pathData = rebased.map(pointsToPathData).join(' ')

  const topPct = ((originY - MARGIN) / anchorRect.height) * 100
  const leftPct = ((originX - MARGIN) / anchorRect.width) * 100
  const widthPct = (w / anchorRect.width) * 100

  return {
    id: _nextId(),
    path_data: pathData,
    viewbox: `0 0 ${w} ${h}`,
    stroke: '#1a1a1a',
    stroke_width: 2,
    fill: 'none',
    placement: {
      base: {
        mode: 'absolute',
        top: { value: _round2(topPct), unit: '%' },
        left: { value: _round2(leftPct), unit: '%' },
        width: { value: _round2(widthPct), unit: '%' },
      },
    },
  }
}

function _makeBtn(text, onClick, { subtle = false } = {}) {
  const btn = document.createElement('button')
  btn.type = 'button'
  btn.textContent = text
  btn.style.cssText = subtle
    ? 'background:none;border:1px solid #d1d5db;border-radius:4px;padding:6px 12px;font-size:13px;cursor:pointer;color:#374151;'
    : 'background:#2563eb;color:#fff;border:none;border-radius:4px;padding:6px 12px;font-size:13px;cursor:pointer;font-weight:500;'
  btn.addEventListener('click', onClick)
  return btn
}

/** Wires shared pointer-draw behavior onto a canvas. Returns {strokes, redraw}. */
function _wireDrawing(canvas) {
  const g = canvas.getContext('2d')
  g.strokeStyle = '#1a1a1a'
  g.lineWidth = 2
  g.lineCap = 'round'
  g.lineJoin = 'round'

  const state = { strokes: [], currentStroke: null }

  function redraw() {
    g.clearRect(0, 0, canvas.width, canvas.height)
    for (const stroke of state.strokes) {
      if (stroke.length < 2) continue
      g.beginPath()
      g.moveTo(stroke[0].x, stroke[0].y)
      for (const p of stroke.slice(1)) g.lineTo(p.x, p.y)
      g.stroke()
    }
  }

  function pointFromEvent(e) {
    const rect = canvas.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  canvas.addEventListener('pointerdown', (e) => {
    // Not implemented in every test environment — capture is a nice-to-have
    // (keeps the stroke going if the pointer leaves the canvas mid-drag),
    // not load-bearing for the widget to function.
    try { canvas.setPointerCapture(e.pointerId) } catch { /* ignore */ }
    state.currentStroke = [pointFromEvent(e)]
    state.strokes.push(state.currentStroke)
  })
  canvas.addEventListener('pointermove', (e) => {
    if (!state.currentStroke) return
    state.currentStroke.push(pointFromEvent(e))
    redraw()
  })
  canvas.addEventListener('pointerup', () => { state.currentStroke = null })
  canvas.addEventListener('pointerleave', () => { state.currentStroke = null })

  return { state, redraw }
}

/**
 * Draw directly on the live page: the canvas is a child of `anchor` itself
 * (the doodle's real CSS positioning root), sized to `anchor` plus MARGIN on
 * every side, so the real post content is visible underneath while drawing
 * and the captured position/size are real, not a placeholder.
 */
function _openOnPageDrawMode({ anchor, doodles, onDone }) {
  if (getComputedStyle(anchor).position === 'static') anchor.style.position = 'relative'

  // Compute the exact pixel box ourselves rather than lean on CSS auto-sizing
  // — two different CSS footguns in a row here: (1) <canvas> is a replaced
  // element (like <img>), so `position: absolute` + `inset` alone does NOT
  // stretch it the way it would a plain <div> — it silently keeps its
  // 300x150 intrinsic default; (2) adding explicit `width: 100%` alongside
  // `inset` "fixes" that but over-constrains the box (left + width + right
  // all definite), so the browser drops `right`/`bottom` and recomputes
  // them — which cuts the canvas short on exactly those edges instead of
  // extending MARGIN past them. Skip both by computing top/left/width/height
  // in px directly; no auto-sizing involved at all.
  const anchorRect = anchor.getBoundingClientRect()
  const canvasW = Math.max(1, Math.round(anchorRect.width + MARGIN * 2))
  const canvasH = Math.max(1, Math.round(anchorRect.height + MARGIN * 2))

  const canvas = document.createElement('canvas')
  canvas.className = '__doodle-onpage-canvas'
  // The dashed border + faint tint mark the actual drawable boundary — with
  // no visual cue at all, there's no way to tell how far you can draw before
  // hitting the edge (found by trying to use this without it).
  canvas.style.cssText = `position:absolute; top:-${MARGIN}px; left:-${MARGIN}px; width:${canvasW}px; height:${canvasH}px; z-index:9997; cursor:crosshair; touch-action:none; border:2px dashed rgba(255,184,108,0.55); border-radius:8px; background:rgba(255,184,108,0.04); box-sizing:border-box;`
  anchor.appendChild(canvas)

  canvas.width = canvasW
  canvas.height = canvasH

  const { state, redraw } = _wireDrawing(canvas)

  const toolbar = document.createElement('div')
  toolbar.className = '__doodle-onpage-toolbar'
  toolbar.style.cssText = 'position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:9998;display:flex;gap:8px;background:#111827;border:1px solid #374151;border-radius:999px;padding:6px 8px;box-shadow:0 8px 30px rgba(0,0,0,0.4);'
  document.body.appendChild(toolbar)

  function cleanup() {
    canvas.remove()
    toolbar.remove()
  }

  toolbar.appendChild(_makeBtn('Clear', () => { state.strokes = []; redraw() }, { subtle: true }))
  toolbar.appendChild(_makeBtn('Cancel', cleanup, { subtle: true }))
  toolbar.appendChild(_makeBtn('Save doodle', () => {
    const nonEmpty = state.strokes.filter((s) => s.length >= 2)
    if (nonEmpty.length === 0) { cleanup(); return }

    const anchorRect = anchor.getBoundingClientRect()
    const doodle = strokesToDoodle(nonEmpty, anchorRect)
    doodles.push(doodle)
    cleanup()
    onDone()
  }))
}

/** No live anchor to draw against (e.g. the admin form) — small self-contained canvas. */
function _openPanelCanvasFallback({ root, doodles, onDone }) {
  root.querySelector('.__doodle-canvas-wrap')?.remove()

  const wrap = document.createElement('div')
  wrap.className = '__doodle-canvas-wrap'
  wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;'

  const canvas = document.createElement('canvas')
  canvas.className = '__doodle-canvas'
  canvas.width = FALLBACK_CANVAS_SIZE
  canvas.height = FALLBACK_CANVAS_SIZE
  canvas.style.cssText = `width:${FALLBACK_CANVAS_SIZE}px;height:${FALLBACK_CANVAS_SIZE}px;border:1px solid #d1d5db;border-radius:6px;background:#fafafa;touch-action:none;cursor:crosshair;`
  wrap.appendChild(canvas)

  const { state, redraw } = _wireDrawing(canvas)

  const btnRow = document.createElement('div')
  btnRow.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;'
  btnRow.appendChild(_makeBtn('Clear', () => { state.strokes = []; redraw() }, { subtle: true }))
  btnRow.appendChild(_makeBtn('Cancel', () => wrap.remove(), { subtle: true }))
  btnRow.appendChild(_makeBtn('Save doodle', () => {
    const nonEmpty = state.strokes.filter((s) => s.length >= 2)
    if (nonEmpty.length === 0) { wrap.remove(); return }

    // No real anchor, so no meaningful placement to compute — same fixed
    // placeholder position this widget always used before draw-on-page.
    const pathData = nonEmpty.map(pointsToPathData).join(' ')
    doodles.push({
      id: _nextId(),
      path_data: pathData,
      viewbox: `0 0 ${FALLBACK_CANVAS_SIZE} ${FALLBACK_CANVAS_SIZE}`,
      stroke: '#1a1a1a',
      stroke_width: 2,
      fill: 'none',
      placement: {
        base: { mode: 'absolute', top: { value: 20, unit: '%' }, left: { value: 20, unit: '%' }, width: { value: 64, unit: 'px' } },
      },
    })
    wrap.remove()
    onDone()
  }))
  wrap.appendChild(btnRow)
  root.appendChild(wrap)
}

/**
 * @param {object[] | null | undefined} value — current doodles array
 * @param {{ container: HTMLElement, anchorEl?: Element, close?: () => void, onSave: (value: object[]) => void }} ctx
 */
export function createDoodleWidget(value, ctx) {
  const doodles = Array.isArray(value) ? [...value] : []

  const root = document.createElement('div')
  root.style.cssText = 'display:flex;flex-direction:column;gap:10px;min-width:240px;'
  ctx.container.appendChild(root)

  const listEl = document.createElement('div')
  listEl.style.cssText = 'display:flex;flex-direction:column;gap:6px;'
  root.appendChild(listEl)

  function renderList() {
    listEl.innerHTML = ''

    if (doodles.length === 0) {
      const empty = document.createElement('p')
      empty.textContent = 'No doodles yet.'
      empty.style.cssText = 'margin:0;font-size:12px;color:#6b7280;'
      listEl.appendChild(empty)
    }

    doodles.forEach((doodle, i) => {
      const row = document.createElement('div')
      row.className = '__doodle-row'
      row.style.cssText = 'display:flex;align-items:center;gap:8px;'

      const preview = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
      preview.setAttribute('viewBox', doodle.viewbox || `0 0 ${FALLBACK_CANVAS_SIZE} ${FALLBACK_CANVAS_SIZE}`)
      preview.setAttribute('width', '32')
      preview.setAttribute('height', '32')
      preview.style.cssText = 'border:1px solid #e5e7eb;border-radius:4px;background:#fafafa;'
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
      path.setAttribute('d', doodle.path_data || '')
      path.setAttribute('stroke', doodle.stroke || '#1a1a1a')
      path.setAttribute('stroke-width', String(doodle.stroke_width ?? 2))
      path.setAttribute('fill', 'none')
      preview.appendChild(path)
      row.appendChild(preview)

      const removeBtn = document.createElement('button')
      removeBtn.type = 'button'
      removeBtn.className = '__doodle-remove'
      removeBtn.textContent = 'Remove'
      removeBtn.style.cssText = 'background:none;border:none;color:#6b7280;cursor:pointer;font-size:12px;padding:0;'
      removeBtn.addEventListener('click', () => {
        doodles.splice(i, 1)
        renderList()
        ctx.onSave([...doodles])
      })
      row.appendChild(removeBtn)

      listEl.appendChild(row)
    })
  }

  function onDoodleAdded() {
    renderList()
    ctx.onSave([...doodles])
  }

  root.appendChild(_makeBtn('+ Draw new doodle', () => {
    const anchor = ctx.anchorEl?.parentElement
    if (anchor instanceof HTMLElement) {
      // The live page is the drawing surface — get the small panel out of
      // the way while you draw on the real content.
      ctx.close?.()
      _openOnPageDrawMode({ anchor, doodles, onDone: onDoodleAdded })
    } else {
      _openPanelCanvasFallback({ root, doodles, onDone: onDoodleAdded })
    }
  }))

  renderList()
}
