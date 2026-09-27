/**
 * Tests for field-widgets.js — the registry ADR 020 asks for: one widget
 * factory per field type, shared by both editing surfaces.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { registerFieldWidget, getFieldWidget, _resetFieldWidgetsForTests } from '../field-widgets.js'

describe('field widget registry', () => {
  beforeEach(() => {
    _resetFieldWidgetsForTests()
  })

  it('returns null for an unregistered field type', () => {
    expect(getFieldWidget('doodles')).toBeNull()
  })

  it('returns null for an undefined field type', () => {
    expect(getFieldWidget(undefined)).toBeNull()
  })

  it('returns the registered factory for its field type', () => {
    const factory = () => {}
    registerFieldWidget('doodles', factory)
    expect(getFieldWidget('doodles')).toBe(factory)
  })

  it('does not confuse two different field types', () => {
    const doodleFactory = () => {}
    const tagsFactory = () => {}
    registerFieldWidget('doodles', doodleFactory)
    registerFieldWidget('tags', tagsFactory)
    expect(getFieldWidget('doodles')).toBe(doodleFactory)
    expect(getFieldWidget('tags')).toBe(tagsFactory)
  })

  it('throws on a duplicate registration instead of silently overwriting', () => {
    registerFieldWidget('doodles', () => {})
    expect(() => registerFieldWidget('doodles', () => {})).toThrow(/already registered/)
  })
})
