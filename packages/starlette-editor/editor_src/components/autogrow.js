/**
 * components/autogrow.js — Text boxes that grow to fit what is in them.
 *
 * A long plain-text field (a definition, a tagline) used to be a one-line
 * <input>: the text ran off the right edge and had to be scrolled through.
 * Plain text fields are now textareas that grow with their content, so the
 * whole value is always on screen:
 *
 * - autoGrow(ta): multi-line, grows from its starting rows up to a cap, then
 *   scrolls. Used for long text and JSON.
 * - singleLine(ta): one logical line that wraps instead of scrolling
 *   sideways. Enter does nothing and pasted line breaks become spaces, so
 *   the value is the same string an <input> would have held.
 *
 * Height is measured from scrollHeight, so the box needs to be in the
 * document: a ResizeObserver catches the first layout and later width changes
 * (a column dragged narrower re-wraps the text).
 */

/** The tallest a growing box gets before it scrolls, as a share of the viewport. */
const MAX_VH = 0.6

let _observer = null

function observer() {
  if (_observer || typeof ResizeObserver === 'undefined') return _observer
  const widths = new WeakMap()
  _observer = new ResizeObserver(entries => {
    for (const entry of entries) {
      const ta = /** @type {HTMLTextAreaElement} */ (entry.target)
      const width = entry.contentRect.width
      if (widths.get(ta) === width) continue
      widths.set(ta, width)
      resizeTextarea(ta)
    }
  })
  return _observer
}

/**
 * Fit a growing textarea's height to its content. Safe to call on anything;
 * it only acts on boxes made by autoGrow() or singleLine().
 *
 * @param {Element|null|undefined} ta
 */
export function resizeTextarea(ta) {
  if (!ta || !(ta instanceof HTMLTextAreaElement) || !ta.hasAttribute('data-autogrow')) return
  if (!ta.isConnected) return
  const style = getComputedStyle(ta)
  const border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0)
  const cap = Math.max(160, Math.round(window.innerHeight * MAX_VH))
  ta.style.height = 'auto'
  const wanted = ta.scrollHeight + border
  ta.style.height = `${Math.min(wanted, cap)}px`
  ta.style.overflowY = wanted > cap ? 'auto' : 'hidden'
}

/**
 * Make a textarea grow with its content.
 *
 * @param {HTMLTextAreaElement} ta
 * @returns {HTMLTextAreaElement}
 */
export function autoGrow(ta) {
  ta.setAttribute('data-autogrow', '')
  ta.addEventListener('input', () => resizeTextarea(ta))
  observer()?.observe(ta)
  return ta
}

/**
 * Make a textarea behave like a one-line input that wraps.
 *
 * @param {HTMLTextAreaElement} ta
 * @returns {HTMLTextAreaElement}
 */
export function singleLine(ta) {
  ta.rows = 1
  ta.setAttribute('data-single-line', '')
  ta.addEventListener('keydown', e => {
    // Enter would insert a line break the field cannot hold. An IME uses
    // Enter to confirm a composition, so leave that alone.
    if (e.key === 'Enter' && !e.isComposing) e.preventDefault()
  })
  // Runs before the field's own input listener sees the value (registered
  // first), so a pasted line break never reaches the form state.
  ta.addEventListener('input', () => {
    if (/[\r\n]/.test(ta.value)) {
      const pos = ta.selectionStart
      ta.value = ta.value.replace(/\r\n?|\n/g, ' ')
      try { ta.setSelectionRange(pos, pos) } catch { /* not focused */ }
    }
  })
  return autoGrow(ta)
}
