/**
 * components/doodle-widget.js — freehand SVG doodle capture.
 *
 * Registered as the 'doodles' field widget (see field-widgets.js) — one
 * factory, mounted the same way whether the caller wants it inline (admin
 * form field-group) or in an overlay (live-embed floating panel): the
 * widget only ever appends into ctx.container, so no mode branching is
 * needed inside it at all.
 *
 * Placement is a fixed placeholder for now — the drag/breakpoint placement
 * editor is a separate follow-up. This phase proves draw → save → render.
 */

const CANVAS_SIZE = 220
const POINT_MIN_DISTANCE = 3 // px — decimates high-frequency pointermove samples

const DEFAULT_PLACEMENT = {
  base: {
    mode: 'absolute',
    top: { value: 20, unit: '%' },
    left: { value: 20, unit: '%' },
    width: { value: 64, unit: 'px' },
  },
}

let _idCounter = 0
function _nextId() {
  _idCounter += 1
  return `dd_${Date.now().toString(36)}_${_idCounter}`
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

/**
 * @param {object[] | null | undefined} value — current doodles array
 * @param {{ container: HTMLElement, onSave: (value: object[]) => void }} ctx
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
      preview.setAttribute('viewBox', doodle.viewbox || `0 0 ${CANVAS_SIZE} ${CANVAS_SIZE}`)
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

  function openCanvas() {
    root.querySelector('.__doodle-canvas-wrap')?.remove()

    const wrap = document.createElement('div')
    wrap.className = '__doodle-canvas-wrap'
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;'

    const canvas = document.createElement('canvas')
    canvas.className = '__doodle-canvas'
    canvas.width = CANVAS_SIZE
    canvas.height = CANVAS_SIZE
    canvas.style.cssText = `width:${CANVAS_SIZE}px;height:${CANVAS_SIZE}px;border:1px solid #d1d5db;border-radius:6px;background:#fafafa;touch-action:none;cursor:crosshair;`
    wrap.appendChild(canvas)

    const g = canvas.getContext('2d')
    g.strokeStyle = '#1a1a1a'
    g.lineWidth = 2
    g.lineCap = 'round'
    g.lineJoin = 'round'

    let strokes = []
    let currentStroke = null

    function redraw() {
      g.clearRect(0, 0, CANVAS_SIZE, CANVAS_SIZE)
      for (const stroke of strokes) {
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
      currentStroke = [pointFromEvent(e)]
      strokes.push(currentStroke)
    })
    canvas.addEventListener('pointermove', (e) => {
      if (!currentStroke) return
      currentStroke.push(pointFromEvent(e))
      redraw()
    })
    canvas.addEventListener('pointerup', () => { currentStroke = null })
    canvas.addEventListener('pointerleave', () => { currentStroke = null })

    const btnRow = document.createElement('div')
    btnRow.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;'

    const clearBtn = _makeBtn('Clear', () => { strokes = []; redraw() }, { subtle: true })
    const cancelBtn = _makeBtn('Cancel', () => wrap.remove(), { subtle: true })
    const saveBtn = _makeBtn('Save doodle', () => {
      const nonEmptyStrokes = strokes.filter(s => s.length >= 2)
      if (nonEmptyStrokes.length === 0) {
        wrap.remove()
        return
      }

      // v1: one path per doodle — merge every stroke from this drawing
      // session into one path_data string via consecutive move/curve segments.
      const pathData = nonEmptyStrokes.map(pointsToPathData).join(' ')

      doodles.push({
        id: _nextId(),
        path_data: pathData,
        viewbox: `0 0 ${CANVAS_SIZE} ${CANVAS_SIZE}`,
        stroke: '#1a1a1a',
        stroke_width: 2,
        fill: 'none',
        placement: DEFAULT_PLACEMENT,
      })
      wrap.remove()
      renderList()
      ctx.onSave([...doodles])
    })

    btnRow.appendChild(clearBtn)
    btnRow.appendChild(cancelBtn)
    btnRow.appendChild(saveBtn)
    wrap.appendChild(btnRow)
    root.appendChild(wrap)
  }

  const drawBtn = _makeBtn('+ Draw new doodle', openCanvas)
  root.appendChild(drawBtn)

  renderList()
}
