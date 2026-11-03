/**
 * components/drawing.js — the drawing value shared by every field that holds
 * a hand-drawn shape: doodles (DoodleField) and single drawings (DrawingField,
 * drawn with components/drawing-pad.js).
 *
 * A drawing is plain JSON, mirrored by ``starlette_cms.drawing.Drawing``::
 *
 *   { viewbox: '0 0 120 80',
 *     paths: [{ d: 'M 4 4 Q ...', stroke: 'currentColor', stroke_width: 2, fill: 'none' }],
 *     symmetry: 'vertical' }   // optional: drawn with the mirror aid on
 *
 * One path per stroke, each with its own stroke and fill, so an outline and a
 * shaded area can live in one drawing. Vector, so it scales and takes the
 * page's colour through ``currentColor``.
 */

const POINT_MIN_DISTANCE = 3 // px — decimates high-frequency pointermove samples
const SVG_NS = 'http://www.w3.org/2000/svg'

export const DEFAULT_STROKE = 'currentColor'
export const DEFAULT_STROKE_WIDTH = 2

/**
 * Decimate a raw point list by minimum distance, then fit a smooth path
 * through it (quadratic segments through consecutive midpoints).
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
 * Turn strokes (already in the drawing's own coordinates) into a drawing.
 *
 * @param {{x: number, y: number}[][]} strokes
 * @param {{ width: number, height: number, stroke?: string, strokeWidth?: number }} box
 * @returns {{ viewbox: string, paths: object[] }}
 */
export function strokesToDrawing(strokes, { width, height, stroke = DEFAULT_STROKE, strokeWidth = DEFAULT_STROKE_WIDTH }) {
  return {
    viewbox: `0 0 ${width} ${height}`,
    paths: strokes.map((s) => ({ d: pointsToPathData(s), stroke, stroke_width: strokeWidth, fill: 'none' })),
  }
}

/**
 * Append a drawing's paths to an `<svg>` (and set its viewBox).
 *
 * @param {SVGSVGElement} svg
 * @param {{ viewbox?: string, paths?: object[] } | null | undefined} drawing
 * @returns {SVGSVGElement} the same svg
 */
export function renderDrawingInto(svg, drawing) {
  if (drawing?.viewbox) svg.setAttribute('viewBox', drawing.viewbox)
  for (const p of drawing?.paths ?? []) {
    const path = document.createElementNS(SVG_NS, 'path')
    path.setAttribute('d', p.d ?? '')
    path.setAttribute('stroke', p.stroke ?? DEFAULT_STROKE)
    path.setAttribute('stroke-width', String(p.stroke_width ?? DEFAULT_STROKE_WIDTH))
    path.setAttribute('fill', p.fill ?? 'none')
    if (p.opacity != null) path.setAttribute('opacity', String(p.opacity))
    path.setAttribute('stroke-linecap', 'round')
    path.setAttribute('stroke-linejoin', 'round')
    svg.appendChild(path)
  }
  return svg
}
