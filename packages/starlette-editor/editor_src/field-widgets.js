/**
 * field-widgets.js — registry mapping a schema `field_type` string to a
 * widget factory, shared by both editing surfaces (ADR 020: "one widget
 * layer, two surfaces").
 *
 * A factory has the shape `factory(value, ctx) => void`, where `ctx` is
 * `{ mode: 'inline' | 'overlay', container, anchorEl, onSave }`:
 *   - `mode: 'inline'`  — the admin form (components/fields.js) renders the
 *     widget directly into `ctx.container`, the field-group div it already
 *     builds.
 *   - `mode: 'overlay'` — the live embed (embed/edit-mode.js) mounts the
 *     widget into a floating panel anchored near the clicked element (see
 *     components/floating-panel.js) and passes the panel body as
 *     `ctx.container`.
 *
 * Registering a field type here is the *entire* integration cost for a new
 * field type on the JS side — no edits to fields.js or edit-mode.js. The
 * Python-side equivalent is `_BaseField.to_pydantic()` in fields.py.
 */

const registry = new Map()

/**
 * @param {string} fieldType
 * @param {(value: unknown, ctx: object) => void} factory
 */
export function registerFieldWidget(fieldType, factory) {
  if (registry.has(fieldType)) {
    throw new Error(`field widget already registered: ${fieldType}`)
  }
  registry.set(fieldType, factory)
}

/**
 * @param {string | undefined} fieldType
 * @returns {((value: unknown, ctx: object) => void) | null}
 */
export function getFieldWidget(fieldType) {
  if (!fieldType) return null
  return registry.get(fieldType) ?? null
}

/** Test-only escape hatch — production code never needs to unregister. */
export function _resetFieldWidgetsForTests() {
  registry.clear()
}
