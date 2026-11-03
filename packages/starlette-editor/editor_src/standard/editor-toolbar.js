import { state } from '../state.js'
import { setActiveChangesetId } from '../changeset-store.js'
import { setState } from '../state.js'
import { apiFetch } from '../api.js'
import { showToast } from '../components/toast.js'
import { icon } from '../icons.js'

const LS_KEY = 'cms-editor-toolbar-geometry'

export class EditorToolbar {
  constructor({ changesetPanel, chatPanel, actions }) {
    this._changesetPanel = changesetPanel
    this._chatPanel = chatPanel
    this._actions = actions || []
    this._el = null
    this._unread = 0
  }

  mount() {
    this._el = document.createElement('div')
    // Marker so the stylesheet can re-dock the pill on small screens; its
    // geometry is inline, so those rules need !important to win.
    this._el.setAttribute('data-cms-editor-toolbar', '')
    this._el.style.cssText = `
      position: fixed;
      /* Clear the iOS home indicator, which sits over a bare 24px offset. */
      bottom: calc(24px + env(safe-area-inset-bottom));
      left: 50%;
      transform: translateX(-50%);
      z-index: 9999;
      display: flex;
      align-items: stretch;
      gap: 0;
      min-height: var(--target-min, 44px);
      background: var(--bg-elevated, #1a1a1a);
      border: 1px solid var(--border-default, #2a2a2a);
      border-radius: 20px;
      font-family: var(--font-sans, system-ui, sans-serif);
      font-size: 12px;
      color: var(--text-secondary, #8a8a8a);
      box-shadow: var(--shadow-md, 0 4px 16px rgba(0, 0, 0, 0.4));
      user-select: none;
      cursor: default;
    `

    document.body.appendChild(this._el)
    this._render()

    this._restoreGeometry()
    this._wireUnreadListener()
  }

  update() {
    this._render()
  }

  _render() {
    if (!this._el) return
    this._el.innerHTML = ''

    this._el.appendChild(this._buildChangesetSegment())

    if (this._chatPanel) {
      this._el.appendChild(this._buildDivider())
      this._el.appendChild(this._buildChatSegment())
    }

    // Custom actions configured by the instance (e.g. "Rebuild site").
    for (const action of this._actions) {
      this._el.appendChild(this._buildDivider())
      this._el.appendChild(this._buildActionSegment(action))
    }

    this._el.appendChild(this._buildDivider())
    this._el.appendChild(this._buildDragGrip())
  }

  _buildActionSegment(action) {
    const seg = document.createElement('button')
    seg.style.cssText = `
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 0 12px;
      align-self: stretch;
      background: none;
      border: none;
      color: var(--text-secondary, #8a8a8a);
      font: inherit;
      cursor: pointer;
      white-space: nowrap;
    `
    seg.textContent = this._actionLabel(action)

    seg.addEventListener('mouseenter', () => {
      if (!seg.disabled) seg.style.background = 'var(--bg-hover, #202020)'
    })
    seg.addEventListener('mouseleave', () => {
      seg.style.background = 'none'
    })
    seg.addEventListener('click', (e) => {
      e.stopPropagation()
      this._runAction(action, seg)
    })

    return seg
  }

  _actionLabel(action) {
    return `${action.icon ? action.icon + ' ' : ''}${action.label}`
  }

  async _runAction(action, seg) {
    if (action.confirm && !window.confirm(action.confirm)) return

    seg.disabled = true
    seg.style.cursor = 'default'
    seg.textContent = `⏳ ${action.label}`
    try {
      const res = await apiFetch(action.endpoint, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || `HTTP ${res.status}`)
      }
      showToast('success', action.label, 'Triggered')
    } catch (err) {
      showToast('error', action.label, err.message)
    } finally {
      seg.disabled = false
      seg.style.cursor = 'pointer'
      seg.textContent = this._actionLabel(action)
    }
  }

  _buildChangesetSegment() {
    const seg = document.createElement('button')
    seg.style.cssText = `
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 0 14px;
      align-self: stretch;
      background: none;
      border: none;
      color: var(--text-secondary, #8a8a8a);
      font: inherit;
      cursor: pointer;
      white-space: nowrap;
      border-radius: 20px 0 0 20px;
    `

    const hasActive = !!state.activeChangesetId
    const title = hasActive
      ? (state.activeChangesetTitle || 'Changeset')
      : 'Changesets'
    const count = state.activeChangesetDocCount || 0

    let label = title
    if (hasActive && count > 0) {
      label += ` (${count})`
    }
    seg.replaceChildren(icon('layers', { size: 14 }), document.createTextNode(label))

    if (hasActive && count > 0) {
      seg.style.color = 'var(--text-primary, #e8e8e8)'
    }

    seg.addEventListener('mouseenter', () => {
      seg.style.background = 'var(--bg-hover, #202020)'
    })
    seg.addEventListener('mouseleave', () => {
      seg.style.background = 'none'
    })
    seg.addEventListener('click', (e) => {
      e.stopPropagation()
      this._changesetPanel?.toggle()
    })

    return seg
  }

  _buildChatSegment() {
    const seg = document.createElement('button')
    seg.style.cssText = `
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 0 12px;
      align-self: stretch;
      background: none;
      border: none;
      color: var(--text-secondary, #8a8a8a);
      font: inherit;
      cursor: pointer;
      white-space: nowrap;
      position: relative;
    `
    seg.replaceChildren(icon('chat', { size: 14 }), document.createTextNode('Chat'))

    if (this._unread > 0) {
      const dot = document.createElement('span')
      dot.style.cssText = `
        position: absolute;
        top: 6px;
        right: 6px;
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--accent, #4c6ef5);
      `
      seg.appendChild(dot)
    }

    seg.addEventListener('mouseenter', () => {
      seg.style.background = 'var(--bg-hover, #202020)'
    })
    seg.addEventListener('mouseleave', () => {
      seg.style.background = 'none'
    })
    seg.addEventListener('click', (e) => {
      e.stopPropagation()
      this._unread = 0
      this._chatPanel?.toggle()
      this._render()
    })

    return seg
  }

  _buildDivider() {
    const d = document.createElement('div')
    d.style.cssText = `
      width: 1px;
      height: 18px;
      align-self: center;
      background: var(--border-subtle, #222222);
      flex-shrink: 0;
    `
    return d
  }

  _buildDragGrip() {
    const grip = document.createElement('div')
    grip.style.cssText = `
      display: flex;
      align-items: center;
      padding: 0 10px;
      align-self: stretch;
      cursor: grab;
      color: var(--text-muted, #555555);
      font-size: 14px;
      border-radius: 0 20px 20px 0;
    `
    grip.replaceChildren(icon('grip', { size: 14 }))
    grip.title = 'Drag to reposition'

    grip.addEventListener('mouseenter', () => {
      grip.style.background = 'var(--bg-hover, #202020)'
    })
    grip.addEventListener('mouseleave', () => {
      grip.style.background = 'none'
    })
    grip.addEventListener('mousedown', (e) => this._startDrag(e))

    return grip
  }

  // ── Drag ──

  _startDrag(e) {
    // Below this width the panel is a full-width sheet and geometry is
    // owned by CSS — dragging or resizing it would fight the stylesheet, and
    // these are mouse-only interactions anyway.
    if (window.innerWidth <= 640) return
    if (!this._el) return
    e.preventDefault()

    const rect = this._el.getBoundingClientRect()
    this._el.style.left = rect.left + 'px'
    this._el.style.top = rect.top + 'px'
    this._el.style.bottom = 'auto'
    this._el.style.transform = 'none'

    const offsetX = e.clientX - rect.left
    const offsetY = e.clientY - rect.top

    const onMove = (ev) => {
      const maxLeft = window.innerWidth - this._el.offsetWidth
      const maxTop = window.innerHeight - this._el.offsetHeight
      this._el.style.left = Math.max(0, Math.min(ev.clientX - offsetX, maxLeft)) + 'px'
      this._el.style.top = Math.max(0, Math.min(ev.clientY - offsetY, maxTop)) + 'px'
    }

    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      this._persistGeometry()
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  // ── Geometry persistence ──

  _persistGeometry() {
    if (!this._el) return
    const rect = this._el.getBoundingClientRect()
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ left: rect.left, top: rect.top }))
    } catch { /* quota / private mode */ }
  }

  _restoreGeometry() {
    // Below this width the panel is a full-width sheet and geometry is
    // owned by CSS — dragging or resizing it would fight the stylesheet, and
    // these are mouse-only interactions anyway.
    if (window.innerWidth <= 640) return
    if (!this._el) return
    let geo = null
    try { geo = JSON.parse(localStorage.getItem(LS_KEY) || 'null') } catch { geo = null }
    if (!geo) return

    const left = Math.max(0, Math.min(geo.left ?? 0, window.innerWidth - this._el.offsetWidth))
    const top = Math.max(0, Math.min(geo.top ?? 0, window.innerHeight - this._el.offsetHeight))

    this._el.style.left = left + 'px'
    this._el.style.top = top + 'px'
    this._el.style.bottom = 'auto'
    this._el.style.transform = 'none'
  }

  // ── Unread chat badge ──

  _wireUnreadListener() {
    if (!this._chatPanel) return
    this._chatPanel.setToolbar(this)
  }

  _onChatMessage() {
    if (!this._chatPanel?.isOpen) {
      this._unread++
      this._render()
    }
  }

  _updateChatBadge() {
    this._render()
  }
}
