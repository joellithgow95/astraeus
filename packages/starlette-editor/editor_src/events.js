/**
 * DocumentEventsSubscriber — live document-list updates for the shell.
 *
 * Opens a WebSocket to the CMS's list-level event channel (/api/events) and,
 * on any document event for the currently-active type, re-fetches the document
 * list. This keeps the list in sync regardless of who wrote the document —
 * the human in another tab, or the AI chat via the REST API.
 *
 * Mirrors embed/collab.js: exponential-backoff reconnect + keep-alive ping.
 *
 * Protocol:
 *   Server → client:  { event: 'document.created'|'document.updated'|..., document_type, ... }
 *   Keep-alive:       { type: 'ping' } → { type: 'pong' }
 */
import { state, setState } from './state.js'
import { fetchDocuments } from './api.js'
import { applyRemoteFields } from './field-sync.js'

export class DocumentEventsSubscriber {
  /**
   * @param {string} cmsBase — CMS origin (no trailing /chat), e.g. "" or "https://cms.example.com"
   * @param {string|null} apiKey — optional API key for query-param auth
   */
  constructor(cmsBase, apiKey = null) {
    this._base = cmsBase
    this._apiKey = apiKey
    this._ws = null
    this._reconnectDelay = 1000  // ms, doubles on each failure (max 30000)
    this._pingInterval = null
    this._destroyed = false
    this._connect()
  }

  _wsUrl() {
    const base = (this._base || window.location.origin)
      .replace(/^https?/, m => (m === 'https' ? 'wss' : 'ws'))
    const url = `${base}/api/events`
    return this._apiKey ? `${url}?api_key=${this._apiKey}` : url
  }

  _connect() {
    if (this._destroyed) return
    this._ws = new WebSocket(this._wsUrl())

    this._ws.onopen = () => {
      this._reconnectDelay = 1000  // reset backoff on successful connect
      this._pingInterval = setInterval(() => {
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
          this._ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 30000)
    }

    this._ws.onmessage = (event) => {
      let msg
      try {
        msg = JSON.parse(event.data)
      } catch {
        return  // ignore malformed messages
      }
      this._handleMessage(msg)
    }

    this._ws.onclose = () => {
      clearInterval(this._pingInterval)
      this._pingInterval = null
      if (!this._destroyed) {
        setTimeout(() => this._connect(), this._reconnectDelay)
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, 30000)
      }
    }

    this._ws.onerror = () => {
      if (this._ws) this._ws.close()
    }
  }

  /** Route an incoming server message. */
  _handleMessage(msg) {
    if (msg.type === 'pong') return

    // Only document.* events carry document_type; refresh the list when the
    // event concerns the type the user is currently viewing.
    const event = msg.event || ''
    // A plain field changed somewhere else: show it in the open document.
    // The list refresh comes with the document.updated that follows.
    if (event === 'document.fields') {
      applyRemoteFields(msg)
      return
    }
    if (!event.startsWith('document.')) return
    if (!state.activeType || msg.document_type !== state.activeType) return

    this._refreshList()
  }

  /** Re-fetch the active type's document list and update state. */
  async _refreshList() {
    try {
      const result = await fetchDocuments(state.activeType)
      setState({
        documents: result.documents || [],
        docsTotal: result.total || 0,
      })
    } catch {
      // Transient fetch failure — the next event (or a manual reselect) recovers
    }
  }

  destroy() {
    this._destroyed = true
    clearInterval(this._pingInterval)
    this._pingInterval = null
    if (this._ws) this._ws.close()
  }
}
