// @vitest-environment happy-dom
import { describe, it, expect } from 'vitest'
import { pointsToPathData, renderDrawingInto, strokesToDrawing } from '../components/drawing.js'

describe('strokesToDrawing', () => {
  it('keeps one path per stroke, so each can carry its own stroke and fill', () => {
    const a = [{ x: 0, y: 0 }, { x: 10, y: 0 }]
    const b = [{ x: 0, y: 5 }, { x: 10, y: 5 }]
    const drawing = strokesToDrawing([a, b], { width: 20, height: 10 })
    expect(drawing.viewbox).toBe('0 0 20 10')
    expect(drawing.paths.map((p) => p.d)).toEqual([pointsToPathData(a), pointsToPathData(b)])
    expect(drawing.paths[0]).toMatchObject({ stroke: 'currentColor', stroke_width: 2, fill: 'none' })
  })
})

describe('renderDrawingInto', () => {
  it('draws every path with its own attributes and sets the viewBox', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    renderDrawingInto(svg, {
      viewbox: '0 0 100 100',
      paths: [
        { d: 'M 0 0 L 10 10' },
        { d: 'M 5 5 L 9 9', stroke: 'none', fill: '#c9a227', opacity: 0.4 },
      ],
    })
    const paths = svg.querySelectorAll('path')
    expect(svg.getAttribute('viewBox')).toBe('0 0 100 100')
    expect(paths).toHaveLength(2)
    expect(paths[0].getAttribute('stroke')).toBe('currentColor')
    expect(paths[0].getAttribute('fill')).toBe('none')
    expect(paths[1].getAttribute('fill')).toBe('#c9a227')
    expect(paths[1].getAttribute('opacity')).toBe('0.4')
  })

  it('draws nothing for a missing drawing', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    renderDrawingInto(svg, null)
    expect(svg.querySelectorAll('path')).toHaveLength(0)
  })
})
