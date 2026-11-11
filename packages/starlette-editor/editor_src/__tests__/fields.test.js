// @vitest-environment happy-dom
/**
 * Tests for components/fields.js — field widget dispatch.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { fieldWidget } from '../components/fields.js'
import { registerFieldWidget, _resetFieldWidgetsForTests } from '../field-widgets.js'

describe('fieldWidget dispatch table', () => {
  it('rich_text field_type → prosemirror', () => {
    expect(fieldWidget('body', { type: 'object' }, { field_type: 'rich_text' })).toBe('prosemirror')
  })

  it('block_list field_type → block_canvas', () => {
    expect(fieldWidget('blocks', { type: 'array' }, { field_type: 'block_list' })).toBe('block_canvas')
  })

  it('block field_type → block_canvas', () => {
    expect(fieldWidget('hero', { type: 'object' }, { field_type: 'block' })).toBe('block_canvas')
  })

  it('image field_type → image_picker', () => {
    expect(fieldWidget('cover_image', { type: 'string' }, { field_type: 'image' })).toBe('image_picker')
  })

  it('select field_type → select', () => {
    expect(fieldWidget('status', { type: 'string' }, { field_type: 'select' })).toBe('select')
  })

  it('boolean field_type → boolean', () => {
    expect(fieldWidget('is_featured', { type: 'boolean' }, { field_type: 'boolean' })).toBe('boolean')
  })

  it('number field_type → number', () => {
    expect(fieldWidget('price', { type: 'number' }, { field_type: 'number' })).toBe('number')
  })

  it('json field_type → json', () => {
    expect(fieldWidget('metadata', { type: 'object' }, { field_type: 'json' })).toBe('json')
  })

  it('document_ref field_type → document_ref picker', () => {
    // Was 'input' — you typed a document id by hand and found out at save time
    // whether it existed.
    expect(fieldWidget('author', { type: 'string' }, { field_type: 'document_ref' }))
      .toBe('document_ref')
  })

  it('choices in meta → select (legacy heuristic)', () => {
    expect(fieldWidget('category', { type: 'string' }, { choices: ['a', 'b'] })).toBe('select')
  })

  it('boolean prop type → boolean', () => {
    expect(fieldWidget('active', { type: 'boolean' }, {})).toBe('boolean')
  })

  it('number prop type → number', () => {
    expect(fieldWidget('count', { type: 'number' }, {})).toBe('number')
  })

  it('integer prop type → number', () => {
    expect(fieldWidget('count', { type: 'integer' }, {})).toBe('number')
  })

  it('object prop type → json', () => {
    expect(fieldWidget('data', { type: 'object' }, {})).toBe('json')
  })

  it('array prop type → json', () => {
    expect(fieldWidget('tags', { type: 'array' }, {})).toBe('json')
  })

  it('long-name string → textarea', () => {
    expect(fieldWidget('body', { type: 'string' }, {})).toBe('textarea')
    expect(fieldWidget('description', { type: 'string' }, {})).toBe('textarea')
    expect(fieldWidget('overview', { type: 'string' }, {})).toBe('textarea')
    expect(fieldWidget('summary', { type: 'string' }, {})).toBe('textarea')
  })

  it('name containing markdown → prosemirror', () => {
    expect(fieldWidget('body_markdown', { type: 'string' }, {})).toBe('prosemirror')
    expect(fieldWidget('intro_markdown', { type: 'string' }, {})).toBe('prosemirror')
  })

  it('default string → input', () => {
    expect(fieldWidget('title', { type: 'string' }, {})).toBe('input')
    expect(fieldWidget('author', { type: 'string' }, {})).toBe('input')
  })
})

// ---------------------------------------------------------------------------
// Registry-dispatched field types (ADR 020)
// ---------------------------------------------------------------------------

describe('fieldWidget: registry precedence', () => {
  beforeEach(() => {
    _resetFieldWidgetsForTests()
  })

  it('a registered field_type dispatches to "registry"', () => {
    registerFieldWidget('doodles', () => {})
    expect(fieldWidget('doodles', { type: 'array' }, { field_type: 'doodles' })).toBe('registry')
  })

  it('an unregistered field_type falls through to the built-in table', () => {
    // 'rich_text' has no registry entry — must still resolve via the
    // hardcoded branch below, unaffected by the registry existing at all.
    expect(fieldWidget('body', { type: 'object' }, { field_type: 'rich_text' })).toBe('prosemirror')
  })

  it('a registered type is never shadowed by a same-named built-in heuristic', () => {
    // If a plugin ever registers a field_type that collides with a name the
    // heuristics below would otherwise catch, the registry must still win.
    registerFieldWidget('image', () => {})
    expect(fieldWidget('cover_image', { type: 'string' }, { field_type: 'image' })).toBe('registry')
  })
})

// ---------------------------------------------------------------------------
// document_ref picker
// ---------------------------------------------------------------------------

describe('buildDocumentRefPicker', () => {
  it('falls back to a text input when no target type is declared', async () => {
    const { buildDocumentRefPicker } = await import('../components/document-ref-picker.js')

    const node = buildDocumentRefPicker('ref', {}, 'doc-9', false, () => {})

    // Nothing to list, so typing an id is still the only option.
    expect(node.tagName).toBe('INPUT')
    expect(node.value).toBe('doc-9')
  })

  it('renders a select when a target type is declared', async () => {
    const { buildDocumentRefPicker } = await import('../components/document-ref-picker.js')

    const node = buildDocumentRefPicker('ref', { ref_block_type: 'blog_post' }, null, false, () => {})

    expect(node.tagName).toBe('SELECT')
    // Disabled until the options arrive, rather than briefly offering none.
    expect(node.hasAttribute('disabled')).toBe(true)
  })
})
