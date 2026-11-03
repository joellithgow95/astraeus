/**
 * ChatPanel — floating AI chat panel for the Astraeus embed script.
 *
 * Sits bottom-left alongside the EditToolbar (bottom-right) and ChangesetPanel.
 * Opens a WebSocket chat session against /api/chat/sessions/{id}/ws and streams
 * tokens, tool events, and edit notices in real time.
 *
 * State machine::
 *   idle → thinking → streaming → (tool_calling | applying_edits)* → idle
 *
 * Usage::
 *   const panel = new ChatPanel(cmsBase, session, { getDocContext, apiKey })
 *   panel.mount()
 *   panel.toggle()
 */

import markdownit from 'markdown-it'

// markdown-it defaults to html:false, so LLM output is escaped (safe — no DOMPurify needed).
const md = markdownit()

// localStorage key for persisted panel geometry {left, top, width, height}.
const LS_GEOMETRY_KEY = 'cms-chat-panel-geometry'

// Minimum panel dimensions when resizing.
const MIN_WIDTH = 300
const MIN_HEIGHT = 260

const PANEL_STYLES = `
  position: fixed;
  bottom: 80px;
  left: 24px;
  z-index: 9998;
  width: 360px;
  height: 540px;
  background: var(--bg-elevated);
  color: var(--text-primary);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 13px;
  box-shadow: var(--shadow-md, 0 4px 24px rgba(0,0,0,0.5));
  overflow: hidden;
  display: flex;
  flex-direction: column;
`

// Spacing/styling for markdown-rendered elements inside assistant bubbles.
// Injected once (scoped to the panel) so tokens can render as real block markup.
const MARKDOWN_STYLES = `
  [data-cms-chat-panel] .cms-md > :first-child { margin-top: 0; }
  [data-cms-chat-panel] .cms-md > :last-child { margin-bottom: 0; }
  [data-cms-chat-panel] .cms-md p { margin: 0 0 8px; }
  [data-cms-chat-panel] .cms-md ul,
  [data-cms-chat-panel] .cms-md ol { margin: 4px 0; padding-left: 20px; }
  [data-cms-chat-panel] .cms-md li { margin: 2px 0; }
  [data-cms-chat-panel] .cms-md pre {
    background: var(--border-subtle);
    padding: 8px 10px;
    border-radius: 6px;
    overflow-x: auto;
    margin: 6px 0;
  }
  [data-cms-chat-panel] .cms-md code {
    font-family: monospace;
    font-size: 12px;
  }
  [data-cms-chat-panel] .cms-md h1,
  [data-cms-chat-panel] .cms-md h2,
  [data-cms-chat-panel] .cms-md h3 { margin: 8px 0 4px; font-size: 14px; }
`

const BTN_BASE = `
  border: none;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  font-family: system-ui, -apple-system, sans-serif;
`

const BUBBLE_COMMON = `
  max-width: 85%;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.5;
  padding: 8px 12px;
`

export class ChatPanel {
  /**
   * @param {string} cmsBase
   * @param {object|null} session  - collab session reference (reserved for future use)
   * @param {{ getDocContext: () => object|null, apiKey?: string|null }} opts
   */
  constructor(cmsBase, session, { getDocContext, apiKey = null }) {
    this._base = cmsBase
    this._session = session
    this._getDocContext = getDocContext
    this._apiKey = apiKey
    this._sessionId = null
    this._ws = null
    this._state = 'idle'
    this._el = null
    this._messagesEl = null
    this._inputEl = null
    this._sendBtn = null
    this._currentAssistantBubble = null
    this._activeToolChips = {}   // tool_name → DOM element
    this._toolbar = null         // set by toolbar.setChatPanel()
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /** Append panel to document.body, initially hidden. */
  mount() {
    // Inject markdown styling once (shared across any panel instances).
    if (!document.querySelector('style[data-cms-chat-md]')) {
      const style = document.createElement('style')
      style.setAttribute('data-cms-chat-md', '')
      style.textContent = MARKDOWN_STYLES
      document.head.appendChild(style)
    }

    this._el = document.createElement('div')
    this._el.setAttribute('data-cms-chat-panel', '')
    this._el.style.cssText = PANEL_STYLES
    this._el.style.display = 'none'

    this._el.appendChild(this._buildHeader())
    this._messagesEl = this._buildMessagesArea()
    this._el.appendChild(this._messagesEl)

    const divider = document.createElement('div')
    divider.style.cssText = 'height: 1px; background: var(--border-default); flex-shrink: 0;'
    this._el.appendChild(divider)

    this._el.appendChild(this._buildInputArea())
    this._el.appendChild(this._buildResizeHandle())

    document.body.appendChild(this._el)

    this._restoreGeometry()
  }

  /** Toggle panel visibility and reset unread badge via toolbar. */
  toggle() {
    if (!this._el) return
    const isVisible = this._el.style.display !== 'none'
    this._el.style.display = isVisible ? 'none' : 'flex'
    if (!isVisible) {
      // Opening — clear unread badge
      if (this._toolbar) {
        this._toolbar._unread = 0
        this._toolbar._updateChatBadge?.()
      }
    }
  }

  /** @returns {boolean} */
  get isOpen() {
    return this._el ? this._el.style.display !== 'none' : false
  }

  /**
   * Back-reference to the toolbar — set by toolbar.setChatPanel().
   * @param {import('./toolbar.js').EditToolbar} toolbar
   */
  setToolbar(toolbar) {
    this._toolbar = toolbar
  }

  // ── Sending ────────────────────────────────────────────────────────────────

  /**
   * Send a user message: append bubble, ensure session, set thinking state, send over WS.
   * @param {string} content
   */
  async send(content) {
    if (!content?.trim()) return
    this._appendMessage({ role: 'user', content })
    await this._ensureSession()
    this._setState('thinking')
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({
        type: 'message',
        content,
        context: this._getDocContext?.() ?? {},
      }))
    }
    // Clear textarea (spec step 6 — also clears when called directly, not just from _sendFromInput)
    if (this._inputEl) {
      this._inputEl.value = ''
      this._inputEl.style.height = 'auto'
    }
  }

  // ── Private — session management ───────────────────────────────────────────

  /** Lazy session initialisation — POST /api/chat/sessions then open WS. */
  async _ensureSession() {
    // Already have an open connection — nothing to do
    if (this._ws && this._ws.readyState === WebSocket.OPEN) return

    // If we have a session ID but the WS dropped, reconnect to the same session
    if (!this._sessionId) {
      const res = await fetch(`${this._base}/api/chat/sessions`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...this._authHeaders() },
        body: JSON.stringify({
          persona: 'default',
          doc_id: this._getDocContext?.()?.doc_id ?? null,
        }),
      })
      const { session_id } = await res.json()
      this._sessionId = session_id
    }

    await this._initWs(this._sessionId)
  }

  /**
   * Open the chat WebSocket and wait for it to be ready.
   * Returns a Promise that resolves when the connection is open.
   * On close: sets this._ws = null so next send reconnects via _ensureSession.
   * @param {string} sessionId
   * @returns {Promise<void>}
   */
  _initWs(sessionId) {
    return new Promise((resolve, reject) => {
      const wsBase = this._base.replace(/^https?/, match => match === 'https' ? 'wss' : 'ws')
      const url = this._apiKey
        ? `${wsBase}/api/chat/sessions/${sessionId}/ws?api_key=${this._apiKey}`
        : `${wsBase}/api/chat/sessions/${sessionId}/ws`

      this._ws = new WebSocket(url)

      this._ws.onopen = () => resolve()

      this._ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          this._onServerMessage(msg)
        } catch {
          // Ignore malformed messages
        }
      }

      this._ws.onclose = () => {
        this._ws = null
      }

      this._ws.onerror = () => {
        if (this._ws) this._ws.close()
        reject(new Error('WebSocket connection failed'))
      }
    })
  }

  /**
   * @returns {{ Authorization: string }|{}}
   */
  _authHeaders() {
    return this._apiKey ? { 'Authorization': 'Bearer ' + this._apiKey } : {}
  }

  // ── Private — server message routing ──────────────────────────────────────

  /**
   * Route an incoming server message through the state machine.
   * @param {{ type: string, [key: string]: any }} msg
   */
  _onServerMessage(msg) {
    switch (msg.type) {
      case 'thinking':
        this._setState('thinking')
        this._startAssistantBubble()
        break
      case 'token':
        this._setState('streaming')
        this._appendToken(msg.delta)
        // Notify toolbar of new message when panel is closed (unread badge)
        if (!this.isOpen && this._toolbar) {
          this._toolbar._onChatMessage?.()
        }
        break
      case 'tool_use':
        this._setState('tool_calling')
        this._appendToolChip(msg.tool, msg.input)
        break
      case 'tool_result':
        this._updateToolChip(msg.tool, msg.summary)
        break
      case 'applying_edits':
        this._setState('applying_edits')
        this._appendEditNotice(msg)
        break
      case 'done':
        this._setState('idle')
        this._finalizeAssistantBubble()
        window.dispatchEvent(new CustomEvent('cms:chat-turn-done'))
        break
    }
  }

  // ── Private — state machine ────────────────────────────────────────────────

  /**
   * Update internal state and reflect in input/send-button enabled state.
   * Shows/hides the "◆ thinking..." indicator.
   * @param {'idle'|'thinking'|'streaming'|'tool_calling'|'applying_edits'} state
   */
  _setState(state) {
    this._state = state
    const busy = state !== 'idle'

    if (this._inputEl) {
      this._inputEl.disabled = busy
      this._inputEl.style.opacity = busy ? '0.5' : '1'
    }
    if (this._sendBtn) {
      this._sendBtn.disabled = busy
      this._sendBtn.style.opacity = busy ? '0.5' : '1'
    }

    // Show thinking indicator in the stream area when state is 'thinking'
    if (state === 'thinking') {
      this._messagesEl?.querySelectorAll('[data-thinking]').forEach(el => el.remove())
      const indicator = document.createElement('div')
      indicator.setAttribute('data-thinking', '')
      indicator.style.cssText = `
        color: var(--text-muted);
        font-style: italic;
        font-size: 12px;
        padding: 4px 0;
        align-self: flex-start;
      `
      indicator.textContent = '◆ thinking...'
      this._messagesEl?.appendChild(indicator)
      this._scrollToBottom()
    } else {
      this._messagesEl?.querySelectorAll('[data-thinking]').forEach(el => el.remove())
    }
  }

  // ── Private — message rendering ────────────────────────────────────────────

  /**
   * Append a fully-formed message bubble (user or assistant).
   * @param {{ role: 'user'|'assistant', content: string }} opts
   */
  _appendMessage({ role, content }) {
    if (!this._messagesEl) return
    const bubble = document.createElement('div')

    if (role === 'user') {
      bubble.style.cssText = `
        ${BUBBLE_COMMON}
        align-self: flex-end;
        background: var(--text-secondary);
        color: var(--bg-elevated);
        border-radius: 12px 12px 2px 12px;
      `
    } else {
      bubble.style.cssText = `
        ${BUBBLE_COMMON}
        align-self: flex-start;
        background: var(--border-default);
        color: var(--text-primary);
        border-radius: 2px 12px 12px 12px;
      `
    }

    bubble.textContent = content
    this._messagesEl.appendChild(bubble)
    this._scrollToBottom()
  }

  /** Start a new streaming assistant bubble. Stores ref in _currentAssistantBubble. */
  _startAssistantBubble() {
    if (!this._messagesEl) return
    const bubble = document.createElement('div')
    bubble.className = 'cms-md'
    bubble.style.cssText = `
      ${BUBBLE_COMMON}
      align-self: flex-start;
      background: var(--border-default);
      color: var(--text-primary);
      border-radius: 2px 12px 12px 12px;
    `
    // Raw markdown buffer — re-rendered to innerHTML on each token.
    bubble._raw = ''
    this._messagesEl.appendChild(bubble)
    this._currentAssistantBubble = bubble
    this._scrollToBottom()
  }

  /**
   * Append a token delta to the active streaming assistant bubble and
   * re-render it as markdown. If no bubble exists yet, starts one first.
   * @param {string} delta
   */
  _appendToken(delta) {
    if (!this._currentAssistantBubble) {
      this._startAssistantBubble()
    }
    this._currentAssistantBubble._raw += delta
    this._currentAssistantBubble.innerHTML = md.render(this._currentAssistantBubble._raw)
    this._scrollToBottom()
  }

  /**
   * Add a tool-use chip showing "🔍 tool_name { key: val... }".
   * @param {string} tool
   * @param {object} input
   */
  _appendToolChip(tool, input) {
    if (!this._messagesEl) return
    const chip = document.createElement('div')
    chip.style.cssText = `
      align-self: flex-start;
      background: var(--bg-elevated);
      border: 1px solid var(--border-strong);
      color: var(--text-secondary);
      padding: 6px 10px;
      border-radius: 8px;
      font-size: 12px;
      font-family: monospace;
      max-width: 90%;
      word-break: break-all;
    `
    chip.textContent = `🔍 ${tool} ${this._summarizeInput(input)}`.trimEnd()
    this._messagesEl.appendChild(chip)
    this._activeToolChips[tool] = chip

    // End the current LLM turn: the next token starts a fresh bubble so each
    // ReAct turn renders as its own paragraph instead of one concatenated blob.
    this._currentAssistantBubble = null

    this._scrollToBottom()
  }

  /**
   * Resolve a pending tool chip to "✓ tool — summary".
   * Keeps the tool name; falls back to the tool name alone when summary is empty.
   * @param {string} tool
   * @param {string} summary
   */
  _updateToolChip(tool, summary) {
    const chip = this._activeToolChips[tool]
    if (!chip) return
    chip.style.color = 'var(--accent)'
    chip.textContent = summary ? `✓ ${tool} — ${summary}` : `✓ ${tool}`
    delete this._activeToolChips[tool]
  }

  /**
   * Append an edit-notice row: "✏️ Applying N edits — rationale".
   * @param {{ step_count?: number, edit_rationale?: string }} msg
   */
  _appendEditNotice(msg) {
    if (!this._messagesEl) return
    const notice = document.createElement('div')
    notice.style.cssText = `
      align-self: stretch;
      background: var(--bg-elevated);
      border: 1px solid var(--text-secondary);
      color: var(--text-secondary);
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 12px;
      line-height: 1.5;
    `
    const count = msg.step_count ?? 0
    const rationale = msg.edit_rationale ?? ''
    notice.textContent = `✏️ Applying ${count} edit${count !== 1 ? 's' : ''} — ${rationale}`
    this._messagesEl.appendChild(notice)
    this._scrollToBottom()
  }

  /** Detach the current streaming bubble reference after 'done'. */
  _finalizeAssistantBubble() {
    this._currentAssistantBubble = null
  }

  // ── Private — utilities ────────────────────────────────────────────────────

  /**
   * Compact an input object to "{ key: val, ... }" (first 2 keys, truncated values).
   * @param {object} input
   * @returns {string}
   */
  _summarizeInput(input) {
    if (!input || typeof input !== 'object') return ''
    const entries = Object.entries(input).slice(0, 2)
    if (entries.length === 0) return ''
    const inner = entries
      .map(([k, v]) => `${k}: ${JSON.stringify(v).slice(0, 30)}`)
      .join(', ')
    return `{ ${inner} }`
  }

  _scrollToBottom() {
    if (this._messagesEl) {
      this._messagesEl.scrollTop = this._messagesEl.scrollHeight
    }
  }

  // ── Private — DOM construction ─────────────────────────────────────────────

  _buildHeader() {
    const header = document.createElement('div')
    header.style.cssText = `
      padding: 12px 14px 10px;
      border-bottom: 1px solid var(--border-default);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
      cursor: move;
      user-select: none;
    `

    const title = document.createElement('span')
    title.style.cssText = 'font-weight: 700; color: var(--text-primary);'
    title.textContent = 'Chat'

    const closeBtn = document.createElement('button')
    closeBtn.style.cssText = `${BTN_BASE} background: transparent; color: var(--text-muted); font-size: 16px; padding: 0 4px;`
    closeBtn.textContent = '×'
    closeBtn.addEventListener('click', () => this.toggle())

    // Drag the panel by its header (ignore drags starting on the close button).
    header.addEventListener('mousedown', (e) => this._startDrag(e, closeBtn))

    header.appendChild(title)
    header.appendChild(closeBtn)
    return header
  }

  // ── Private — drag / resize / geometry ─────────────────────────────────────

  /**
   * Begin dragging the panel from a header mousedown.
   * @param {MouseEvent} e
   * @param {HTMLElement} closeBtn - close button; drags starting on it are ignored
   */
  _startDrag(e, closeBtn) {
    // Below this width the panel is a full-width sheet and geometry is
    // owned by CSS — dragging or resizing it would fight the stylesheet, and
    // these are mouse-only interactions anyway.
    if (window.innerWidth <= 640) return
    if (!this._el || (closeBtn && closeBtn.contains(e.target))) return
    e.preventDefault()

    // Switch from bottom/left to top/left anchoring so left/top drive position.
    const rect = this._el.getBoundingClientRect()
    this._el.style.top = rect.top + 'px'
    this._el.style.left = rect.left + 'px'
    this._el.style.bottom = 'auto'

    const offsetX = e.clientX - rect.left
    const offsetY = e.clientY - rect.top

    const onMove = (ev) => {
      const maxLeft = window.innerWidth - this._el.offsetWidth
      const maxTop = window.innerHeight - this._el.offsetHeight
      const left = Math.max(0, Math.min(ev.clientX - offsetX, maxLeft))
      const top = Math.max(0, Math.min(ev.clientY - offsetY, maxTop))
      this._el.style.left = left + 'px'
      this._el.style.top = top + 'px'
    }

    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      this._persistGeometry()
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  /** Build the bottom-right resize handle that drives width/height. */
  _buildResizeHandle() {
    const handle = document.createElement('div')
    handle.setAttribute('data-cms-chat-resize', '')
    handle.style.cssText = `
      position: absolute;
      right: 0;
      bottom: 0;
      width: 16px;
      height: 16px;
      cursor: nwse-resize;
      background:
        linear-gradient(135deg, transparent 50%, var(--border-strong) 50%, var(--border-strong) 60%, transparent 60%, transparent 70%, var(--border-strong) 70%, var(--border-strong) 80%, transparent 80%);
    `
    handle.addEventListener('mousedown', (e) => this._startResize(e))
    return handle
  }

  /**
   * Begin resizing the panel from a handle mousedown.
   * @param {MouseEvent} e
   */
  _startResize(e) {
    // Below this width the panel is a full-width sheet and geometry is
    // owned by CSS — dragging or resizing it would fight the stylesheet, and
    // these are mouse-only interactions anyway.
    if (window.innerWidth <= 640) return
    if (!this._el) return
    e.preventDefault()
    e.stopPropagation()

    const startX = e.clientX
    const startY = e.clientY
    const startW = this._el.offsetWidth
    const startH = this._el.offsetHeight

    const onMove = (ev) => {
      const width = Math.max(MIN_WIDTH, startW + (ev.clientX - startX))
      const height = Math.max(MIN_HEIGHT, startH + (ev.clientY - startY))
      this._el.style.width = width + 'px'
      this._el.style.height = height + 'px'
    }

    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      this._persistGeometry()
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  /** Save current geometry {left, top, width, height} to localStorage. */
  _persistGeometry() {
    if (!this._el) return
    const rect = this._el.getBoundingClientRect()
    try {
      localStorage.setItem(
        LS_GEOMETRY_KEY,
        JSON.stringify({
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
        }),
      )
    } catch {
      // localStorage unavailable (private mode / quota) — geometry just won't persist
    }
  }

  /**
   * Restore geometry from localStorage, clamped to the viewport.
   * Falls back to the default PANEL_STYLES anchoring if none is saved.
   */
  _restoreGeometry() {
    // Below this width the panel is a full-width sheet and geometry is
    // owned by CSS — dragging or resizing it would fight the stylesheet, and
    // these are mouse-only interactions anyway.
    if (window.innerWidth <= 640) return
    if (!this._el) return
    let geo = null
    try {
      geo = JSON.parse(localStorage.getItem(LS_GEOMETRY_KEY) || 'null')
    } catch {
      geo = null
    }
    if (!geo) return

    const width = Math.max(MIN_WIDTH, geo.width || MIN_WIDTH)
    const height = Math.max(MIN_HEIGHT, geo.height || MIN_HEIGHT)
    const left = Math.max(0, Math.min(geo.left ?? 0, window.innerWidth - width))
    const top = Math.max(0, Math.min(geo.top ?? 0, window.innerHeight - height))

    this._el.style.width = width + 'px'
    this._el.style.height = height + 'px'
    this._el.style.left = left + 'px'
    this._el.style.top = top + 'px'
    this._el.style.bottom = 'auto'
  }

  _buildMessagesArea() {
    const el = document.createElement('div')
    el.style.cssText = `
      flex: 1;
      overflow-y: auto;
      padding: 10px 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      min-height: 0;
    `
    return el
  }

  _buildInputArea() {
    const inputArea = document.createElement('div')
    inputArea.style.cssText = `
      padding: 10px 14px;
      display: flex;
      gap: 8px;
      align-items: flex-end;
      flex-shrink: 0;
    `

    this._inputEl = document.createElement('textarea')
    this._inputEl.placeholder = 'Ask Claude...'
    this._inputEl.rows = 1
    this._inputEl.style.cssText = `
      flex: 1;
      background: var(--border-default);
      color: var(--text-primary);
      border: 1px solid var(--border-strong);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 13px;
      font-family: system-ui, -apple-system, sans-serif;
      resize: none;
      min-height: 36px;
      max-height: 96px;
      overflow-y: auto;
      line-height: 1.4;
    `

    // Auto-resize up to 4 lines (~96px)
    this._inputEl.addEventListener('input', () => {
      this._inputEl.style.height = 'auto'
      this._inputEl.style.height = Math.min(this._inputEl.scrollHeight, 96) + 'px'
    })

    // Send on Enter; Shift+Enter inserts newline
    this._inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        this._sendFromInput()
      }
    })

    this._sendBtn = document.createElement('button')
    this._sendBtn.textContent = 'Send'
    this._sendBtn.style.cssText = `
      ${BTN_BASE}
      background: var(--text-secondary);
      color: var(--bg-elevated);
      padding: 8px 14px;
      font-weight: 600;
    `
    this._sendBtn.addEventListener('click', () => this._sendFromInput())

    inputArea.appendChild(this._inputEl)
    inputArea.appendChild(this._sendBtn)
    return inputArea
  }

  _sendFromInput() {
    const content = this._inputEl?.value?.trim()
    if (!content) return
    this.send(content)
    // Textarea clearing is handled by send()
  }
}
