// @vitest-environment happy-dom
/**
 * Tests for components/block-canvas.js — block management functions.
 * These functions mutate state.formData, so we reset it between tests.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { getBlockTypesMeta, addBlock, removeBlock, moveBlock, buildBlockCanvas } from '../components/block-canvas.js'
import { state } from '../state.js'

/** Minimal render noop. */
const noop = () => {}

/** Reset formData before each test. */
beforeEach(() => {
  state.formData = {}
  state.isDirty = false
  // Stub document.getElementById so updateBlockField doesn't throw
  if (typeof document === 'undefined') return
})

describe('getBlockTypesMeta', () => {
  it('resolves a homogeneous $ref list', () => {
    const prop = {
      type: 'array',
      items: { $ref: '#/$defs/HeroBlock' },
    }
    const typeSchema = {
      $defs: {
        HeroBlock: {
          properties: {
            block_type: { const: 'hero' },
            title: { type: 'string' },
          },
        },
      },
    }
    const result = getBlockTypesMeta(prop, typeSchema)
    expect(result).toHaveLength(1)
    expect(result[0].registeredType).toBe('hero')
    expect(result[0].name).toBe('HeroBlock')
  })

  it('resolves a polymorphic anyOf list', () => {
    const prop = {
      type: 'array',
      items: {
        anyOf: [
          { $ref: '#/$defs/HeroBlock' },
          { $ref: '#/$defs/TextBlock' },
        ],
      },
    }
    const typeSchema = {
      $defs: {
        HeroBlock: { properties: { block_type: { const: 'hero' } } },
        TextBlock: { properties: { block_type: { const: 'text' } } },
      },
    }
    const result = getBlockTypesMeta(prop, typeSchema)
    expect(result).toHaveLength(2)
    expect(result.map(r => r.registeredType)).toEqual(['hero', 'text'])
  })

  it('resolves an optional discriminated list, as pydantic writes it', () => {
    // ListField(blocks=[...]) without required=True
    const prop = {
      anyOf: [
        {
          type: 'array',
          items: {
            discriminator: { propertyName: 'block_type' },
            oneOf: [{ $ref: '#/$defs/RangeSlice' }, { $ref: '#/$defs/PickedSlice' }],
          },
        },
        { type: 'null' },
      ],
    }
    const typeSchema = {
      $defs: {
        RangeSlice: { properties: { block_type: { const: 'range_slice' } } },
        PickedSlice: { properties: { block_type: { const: 'picked_slice' } } },
      },
    }
    expect(getBlockTypesMeta(prop, typeSchema).map(r => r.registeredType)).toEqual(['range_slice', 'picked_slice'])
  })

  it('returns an empty array when items is missing', () => {
    expect(getBlockTypesMeta({}, {})).toEqual([])
    expect(getBlockTypesMeta({ items: null }, {})).toEqual([])
  })

  it('handles an inline (non-$ref) schema', () => {
    const prop = {
      type: 'array',
      items: {
        properties: { title: { type: 'string' } },
      },
    }
    const result = getBlockTypesMeta(prop, {})
    expect(result).toHaveLength(1)
    expect(result[0].registeredType).toBe('block')
  })
})

describe('addBlock', () => {
  it('appends a block with correct block_type and defaults', () => {
    const typeInfo = {
      registeredType: 'hero',
      schema: {
        properties: {
          block_type: { const: 'hero' },
          title: { type: 'string' },
          featured: { type: 'boolean' },
          count: { type: 'integer' },
        },
      },
    }
    addBlock('blocks', typeInfo, noop)
    expect(state.formData.blocks).toHaveLength(1)
    expect(state.formData.blocks[0].block_type).toBe('hero')
    expect(state.formData.blocks[0].title).toBe('')
    expect(state.formData.blocks[0].featured).toBe(false)
    expect(state.formData.blocks[0].count).toBe(null)
  })

  it('appends to an existing array', () => {
    state.formData.blocks = [{ block_type: 'existing' }]
    const typeInfo = { registeredType: 'new', schema: { properties: {} } }
    addBlock('blocks', typeInfo, noop)
    expect(state.formData.blocks).toHaveLength(2)
    expect(state.formData.blocks[1].block_type).toBe('new')
  })

  it('sets isDirty to true', () => {
    const typeInfo = { registeredType: 'x', schema: { properties: {} } }
    addBlock('blocks', typeInfo, noop)
    expect(state.isDirty).toBe(true)
  })
})

describe('removeBlock', () => {
  it('removes the block at the given index', () => {
    state.formData.blocks = [
      { block_type: 'a' },
      { block_type: 'b' },
      { block_type: 'c' },
    ]
    removeBlock('blocks', 1, noop)
    expect(state.formData.blocks).toHaveLength(2)
    expect(state.formData.blocks.map(b => b.block_type)).toEqual(['a', 'c'])
  })

  it('removes the first block', () => {
    state.formData.blocks = [{ block_type: 'a' }, { block_type: 'b' }]
    removeBlock('blocks', 0, noop)
    expect(state.formData.blocks.map(b => b.block_type)).toEqual(['b'])
  })

  it('removes the last block', () => {
    state.formData.blocks = [{ block_type: 'a' }, { block_type: 'b' }]
    removeBlock('blocks', 1, noop)
    expect(state.formData.blocks.map(b => b.block_type)).toEqual(['a'])
  })

  it('sets isDirty to true', () => {
    state.formData.blocks = [{ block_type: 'a' }]
    removeBlock('blocks', 0, noop)
    expect(state.isDirty).toBe(true)
  })
})

describe('moveBlock', () => {
  it('moves a block forward in the array', () => {
    state.formData.blocks = [
      { block_type: 'a' },
      { block_type: 'b' },
      { block_type: 'c' },
    ]
    moveBlock('blocks', 0, 2, noop)
    expect(state.formData.blocks.map(b => b.block_type)).toEqual(['b', 'c', 'a'])
  })

  it('moves a block backward in the array', () => {
    state.formData.blocks = [
      { block_type: 'a' },
      { block_type: 'b' },
      { block_type: 'c' },
    ]
    moveBlock('blocks', 2, 0, noop)
    expect(state.formData.blocks.map(b => b.block_type)).toEqual(['c', 'a', 'b'])
  })

  it('is a no-op when fromIndex === toIndex', () => {
    state.formData.blocks = [{ block_type: 'a' }, { block_type: 'b' }]
    moveBlock('blocks', 1, 1, noop)
    expect(state.formData.blocks.map(b => b.block_type)).toEqual(['a', 'b'])
  })

  it('sets isDirty to true after a real move', () => {
    state.formData.blocks = [{ block_type: 'a' }, { block_type: 'b' }]
    moveBlock('blocks', 0, 1, noop)
    expect(state.isDirty).toBe(true)
  })
})

describe('reorder controls', () => {
  /** Render a canvas of `count` blocks and return its move buttons. */
  function renderCanvas(count) {
    const blocks = Array.from({ length: count }, (_, i) => ({ block_type: 'hero', title: `b${i}` }))
    state.formData = { blocks }

    const prop = { type: 'array', items: { $ref: '#/$defs/HeroBlock' } }
    const typeSchema = {
      $defs: { HeroBlock: { properties: { block_type: { const: 'hero' }, title: { type: 'string' } } } },
    }

    return buildBlockCanvas('blocks', prop, {}, typeSchema, blocks, noop)
  }

  it('reorders by control rather than drag, which is inert on touch', () => {
    const canvas = renderCanvas(3)

    expect(canvas.querySelectorAll('[draggable="true"]')).toHaveLength(0)
    expect(canvas.querySelectorAll('.block-card__move').length).toBe(6)
  })

  it('numbers the blocks by position', () => {
    const canvas = renderCanvas(3)
    const positions = [...canvas.querySelectorAll('.block-card__position')].map(n => n.textContent)

    expect(positions).toEqual(['1', '2', '3'])
  })

  it('disables up on the first block and down on the last', () => {
    const canvas = renderCanvas(3)
    const cards = [...canvas.querySelectorAll('.block-card')]
    const moves = cards.map(c => [...c.querySelectorAll('.block-card__move')])

    expect(moves[0][0].disabled).toBe(true)
    expect(moves[0][1].disabled).toBe(false)
    expect(moves[1][0].disabled).toBe(false)
    expect(moves[1][1].disabled).toBe(false)
    expect(moves[2][0].disabled).toBe(false)
    expect(moves[2][1].disabled).toBe(true)
  })

  it('disables both controls for a lone block', () => {
    const canvas = renderCanvas(1)
    const moves = [...canvas.querySelectorAll('.block-card__move')]

    expect(moves.every(b => b.disabled)).toBe(true)
  })

  it('moves a block down when its control is clicked', () => {
    const canvas = renderCanvas(3)
    const firstCard = canvas.querySelector('.block-card')
    const down = [...firstCard.querySelectorAll('.block-card__move')][1]

    down.click()

    expect(state.formData.blocks.map(b => b.title)).toEqual(['b1', 'b0', 'b2'])
    expect(state.isDirty).toBe(true)
  })

  it('moves a block up when its control is clicked', () => {
    const canvas = renderCanvas(3)
    const lastCard = [...canvas.querySelectorAll('.block-card')][2]
    const up = [...lastCard.querySelectorAll('.block-card__move')][0]

    up.click()

    expect(state.formData.blocks.map(b => b.title)).toEqual(['b0', 'b2', 'b1'])
  })

  it('does not collapse the card when a control is used', () => {
    const canvas = renderCanvas(2)
    const card = canvas.querySelector('.block-card')
    card.classList.add('is-open')

    card.querySelector('.block-card__delete').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    )

    expect(card.classList.contains('is-open')).toBe(true)
  })
})
