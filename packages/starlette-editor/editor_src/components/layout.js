/**
 * components/layout.js — Resizable, collapsible columns for the shell.
 *
 * At width the shell is three columns: navigation, the document list, the
 * editor. Each of the first two can be dragged wider or narrower by the
 * handle on its right edge, and collapsed: navigation to an icon rail, the
 * document list out of the way entirely. Widths and collapsed state are kept
 * per browser in localStorage.
 *
 * Below 640px none of this applies: navigation is a route stack
 * (doc-drawer.js) and the stylesheet hides the handles.
 *
 * State lives in classes and custom properties on #app, so the stylesheet owns
 * the geometry:
 *   .nav-rail        — navigation drawn as an icon rail (collapsed, or ≤900px)
 *   .docs-collapsed  — the document list is hidden
 *   --sidebar-width, --doc-list-width — the dragged widths
 */

import { icon } from '../icons.js'

export const LAYOUT_KEY = 'astraeus-editor-layout'

/** Drag limits, in px. The upper bounds keep the editor usable on a laptop. */
export const LIMITS = {
  nav: { min: 168, max: 360, initial: 240 },
  docs: { min: 200, max: 520, initial: 240 },
}

/** Below this the navigation is a rail whatever the stored choice says. */
const RAIL_QUERY = '(max-width: 900px)'
/** Below this the columns are a route stack and layout does nothing. */
const STACK_QUERY = '(max-width: 640px)'

/** @returns {{navCollapsed: boolean, docsCollapsed: boolean, navWidth: number|null, docsWidth: number|null}} */
export function loadLayout() {
  const empty = { navCollapsed: false, docsCollapsed: false, navWidth: null, docsWidth: null }
  try {
    const raw = JSON.parse(localStorage.getItem(LAYOUT_KEY) || 'null')
    if (!raw || typeof raw !== 'object') return empty
    return {
      navCollapsed: raw.navCollapsed === true,
      docsCollapsed: raw.docsCollapsed === true,
      navWidth: typeof raw.navWidth === 'number' ? clampWidth('nav', raw.navWidth) : null,
      docsWidth: typeof raw.docsWidth === 'number' ? clampWidth('docs', raw.docsWidth) : null,
    }
  } catch {
    return empty
  }
}

function saveLayout(layout) {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout))
  } catch { /* storage blocked: the layout lasts this page */ }
}

/**
 * @param {'nav'|'docs'} column
 * @param {number} px
 * @returns {number}
 */
export function clampWidth(column, px) {
  const { min, max } = LIMITS[column]
  return Math.round(Math.min(max, Math.max(min, px)))
}

/**
 * Wire the columns. Returns the controls the shell places in its headers.
 *
 * @param {HTMLElement} app  — #app
 * @param {{nav?: HTMLElement, docs?: HTMLElement}} columns — filled in by the caller once the panels exist
 * @returns {{
 *   navToggle: HTMLButtonElement,
 *   docsToggle: HTMLButtonElement,
 *   docsReveal: HTMLButtonElement,
 *   navResizer: HTMLElement,
 *   docsResizer: HTMLElement,
 *   toggleNav: () => void,
 *   toggleDocs: () => void,
 * }}
 */
export function createLayout(app, columns) {
  const layout = loadLayout()
  const railMedia = window.matchMedia?.(RAIL_QUERY)
  const stackMedia = window.matchMedia?.(STACK_QUERY)

  const navToggle = el('button', 'icon-btn sidebar-types__collapse')
  const docsToggle = el('button', 'icon-btn sidebar-docs__collapse')
  docsToggle.append(icon('panel-left-close'))
  docsToggle.title = 'Hide the document list'
  docsToggle.setAttribute('aria-label', 'Hide the document list')

  // Shown in the editor header while the list is hidden, so it can come back.
  const docsReveal = el('button', 'icon-btn editor-header__docs-reveal')
  docsReveal.append(icon('panel-left-open'))
  docsReveal.title = 'Show the document list'
  docsReveal.setAttribute('aria-label', 'Show the document list')

  const apply = () => {
    const stacked = stackMedia?.matches === true
    const forcedRail = railMedia?.matches === true
    const rail = !stacked && (layout.navCollapsed || forcedRail)

    app.classList.toggle('nav-rail', rail)
    app.classList.toggle('docs-collapsed', !stacked && layout.docsCollapsed)

    if (layout.navWidth) app.style.setProperty('--sidebar-width', `${layout.navWidth}px`)
    else app.style.removeProperty('--sidebar-width')
    if (layout.docsWidth) app.style.setProperty('--doc-list-width', `${layout.docsWidth}px`)
    else app.style.removeProperty('--doc-list-width')

    // A rail forced by the window width cannot be expanded from here.
    navToggle.hidden = forcedRail
    navToggle.replaceChildren(icon(rail ? 'panel-left-open' : 'panel-left-close'))
    const navLabel = rail ? 'Expand navigation' : 'Collapse navigation'
    navToggle.title = navLabel
    navToggle.setAttribute('aria-label', navLabel)
    navToggle.setAttribute('aria-expanded', String(!rail))
    docsToggle.setAttribute('aria-expanded', String(!layout.docsCollapsed))
  }

  const toggleNav = () => {
    layout.navCollapsed = !layout.navCollapsed
    saveLayout(layout)
    apply()
  }
  const toggleDocs = () => {
    layout.docsCollapsed = !layout.docsCollapsed
    saveLayout(layout)
    apply()
  }

  navToggle.addEventListener('click', toggleNav)
  docsToggle.addEventListener('click', toggleDocs)
  docsReveal.addEventListener('click', toggleDocs)

  const navResizer = buildResizer('nav', () => columns.nav, 'Resize navigation', width => {
    layout.navWidth = width
    apply()
  }, () => saveLayout(layout), () => {
    layout.navWidth = null
    saveLayout(layout)
    apply()
  })
  const docsResizer = buildResizer('docs', () => columns.docs, 'Resize the document list', width => {
    layout.docsWidth = width
    apply()
  }, () => saveLayout(layout), () => {
    layout.docsWidth = null
    saveLayout(layout)
    apply()
  })

  railMedia?.addEventListener?.('change', apply)
  stackMedia?.addEventListener?.('change', apply)
  apply()

  return { navToggle, docsToggle, docsReveal, navResizer, docsResizer, toggleNav, toggleDocs }
}

/**
 * A drag handle on a column's right edge. Pointer drag resizes; with focus,
 * the arrow keys step by 16px (Shift: 64px); double-click resets.
 *
 * @param {'nav'|'docs'} column
 * @param {() => HTMLElement|undefined} target — the column being resized (read when a drag starts)
 * @param {string} label
 * @param {(width: number) => void} onResize
 * @param {() => void} onCommit — drag or key step finished
 * @param {() => void} onReset
 * @returns {HTMLElement}
 */
function buildResizer(column, target, label, onResize, onCommit, onReset) {
  const handle = el('div', `col-resizer col-resizer--${column}`)
  handle.setAttribute('role', 'separator')
  handle.setAttribute('aria-orientation', 'vertical')
  handle.setAttribute('aria-label', label)
  handle.setAttribute('aria-valuemin', String(LIMITS[column].min))
  handle.setAttribute('aria-valuemax', String(LIMITS[column].max))
  handle.tabIndex = 0
  handle.title = `${label} (double-click to reset)`

  const current = () => target()?.getBoundingClientRect().width || LIMITS[column].initial
  const set = px => {
    const width = clampWidth(column, px)
    handle.setAttribute('aria-valuenow', String(width))
    onResize(width)
  }

  handle.addEventListener('pointerdown', e => {
    if (e.button !== 0) return
    e.preventDefault()
    const startX = e.clientX
    const startWidth = current()
    handle.setPointerCapture?.(e.pointerId)
    document.body.classList.add('is-resizing')
    handle.classList.add('is-dragging')

    const move = ev => set(startWidth + (ev.clientX - startX))
    const up = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', up)
      handle.removeEventListener('pointercancel', up)
      document.body.classList.remove('is-resizing')
      handle.classList.remove('is-dragging')
      onCommit()
    }
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', up)
    handle.addEventListener('pointercancel', up)
  })

  handle.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    const step = (e.shiftKey ? 64 : 16) * (e.key === 'ArrowLeft' ? -1 : 1)
    set(current() + step)
    onCommit()
  })

  handle.addEventListener('dblclick', () => {
    handle.removeAttribute('aria-valuenow')
    onReset()
  })

  return handle
}

function el(tag, className) {
  const node = document.createElement(tag)
  node.className = className
  if (tag === 'button') node.type = 'button'
  return node
}
