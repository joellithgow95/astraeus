/**
 * components/floating-panel.js — chrome for a small panel anchored near a
 * clicked element: positioned via getBoundingClientRect, closes on outside
 * click or Escape, replaces any existing instance of the same panel first.
 *
 * Extracted from the tags editor's hand-rolled version (ADR 020) — the
 * registry-dispatched field widgets (embed/edit-mode.js, `mode: 'overlay'`)
 * are the second consumer. Not a replacement for the full-screen modal
 * pattern used by the markdown/image editors — that's a different chrome
 * shape and out of scope here.
 */

/**
 * @param {Element} anchorEl — element the panel positions itself under
 * @param {{ className: string }} opts — `className` also scopes the
 *   "close any existing instance" check, so concurrent panels of different
 *   kinds don't clobber each other.
 * @returns {{ container: HTMLElement, close: () => void }}
 */
export function openFloatingPanel(anchorEl, { className }) {
  document.querySelector(`.${className}`)?.remove()

  const rect = anchorEl.getBoundingClientRect()
  const panel = document.createElement('div')
  panel.className = className
  Object.assign(panel.style, {
    position: 'fixed',
    top: `${rect.bottom + window.scrollY + 6}px`,
    left: `${rect.left + window.scrollX}px`,
    zIndex: '99999',
    background: '#fff',
    border: '1px solid #d1d5db',
    borderRadius: '8px',
    padding: '12px',
    boxShadow: '0 8px 30px rgba(0,0,0,0.15)',
    minWidth: '280px',
    maxWidth: '420px',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  })
  document.body.appendChild(panel)

  function close() {
    panel.remove()
    document.removeEventListener('click', onOutsideClick, true)
    document.removeEventListener('keydown', onKeydown, true)
  }

  function onOutsideClick(e) {
    if (!panel.contains(e.target) && e.target !== anchorEl) close()
  }

  function onKeydown(e) {
    if (e.key === 'Escape') close()
  }

  // Defer so the opening click doesn't immediately close the panel.
  setTimeout(() => document.addEventListener('click', onOutsideClick, true), 0)
  document.addEventListener('keydown', onKeydown, true)

  return { container: panel, close }
}
