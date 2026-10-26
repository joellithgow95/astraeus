/**
 * CollabConnection — manages the WebSocket connection to the CMS step authority.
 *
 * Implements the prosemirror-collab client protocol:
 *   - On connect: receives init with doc + version
 *   - On local edit: sends sendable steps to server
 *   - On server confirmation: receiveTransaction applied to local state
 *   - On reject: hold pending steps; they rebase when the broadcast arrives
 *   - Reconnects with exponential backoff on disconnect
 *
 * Protocol:
 *   Server → client init:   { type: 'init', doc: <PM JSON>, version: <int> }
 *   Client → server steps:  { type: 'steps', steps: [...], clientID, version, doc }
 *   Server → client steps:  { type: 'steps', steps: [...], clientIDs: [...], version }
 *   Server → client reject: { type: 'reject', version: <int>, reason?: string }
 *   Client → server catch-up: { type: 'catch_up', version } → { type: 'steps', ... } | { type: 'resync_required', doc, version }
 *   Server → client error:  { type: 'error', code?, message? }  (e.g. persist_failed)
 *   Keep-alive:             { type: 'ping' } → { type: 'pong' }
 *
 * Failures are loud. Anything that leaves typed text unsaved calls the
 * toolbar's setSaveError (the shell shows a persistent banner, the embed its
 * error pill), and the field's text is kept in localStorage under
 * `rescueKey(documentId, field)` until the server confirms every step.
 */
import { collab, sendableSteps, receiveTransaction, getVersion } from 'prosemirror-collab'
import { Step } from 'prosemirror-transform'
import { getActiveChangesetId, setActiveChangesetId } from './changeset-store.js'
import { stateForServerCopy } from './collab-sync.js'

export { collab }

/** How long to wait for a `catch_up` reply before warning that it was not served. */
const CATCH_UP_TIMEOUT_MS = 5000

/** How long the socket may stay down with unsaved edits before saying so. */
const OFFLINE_WARNING_MS = 4000

const RESCUE_PREFIX = 'astraeus:collab-rescue:'

/**
 * localStorage key holding a field's text while it has unconfirmed edits.
 *
 * @param {string} documentId
 * @param {string} field
 */
export function rescueKey(documentId, field) {
  return `${RESCUE_PREFIX}${documentId}:${field}`
}

/**
 * The rescued copy of a field, if a session left one: `{ savedAt, version, doc, reason }`.
 *
 * @param {string} documentId
 * @param {string} field
 * @returns {object|null}
 */
export function loadRescue(documentId, field) {
  try {
    const raw = localStorage.getItem(rescueKey(documentId, field))
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export class CollabConnection {
  constructor({ view, schema, documentId, field, cmsBase, initialVersion, toolbar, clientID, apiKey = null }) {
    this.view = view              // EditorView instance
    this.schema = schema          // ProseMirror Schema
    this.documentId = documentId
    this.field = field            // rich-text field being edited, e.g. 'body_markdown'
    this.cmsBase = cmsBase
    this.version = initialVersion
    this.toolbar = toolbar
    this.clientID = clientID      // UUID identifying this client
    this.apiKey = apiKey          // optional API key for shell (cookie auth not available)
    this.ws = null
    this._reconnectDelay = 1000   // ms, doubles on each failure (max 30000)
    this._destroyed = false
    this._pingInterval = null
    this._peers = new Map()       // server client_id → peer info dict
    this._toolbar = null          // set via setToolbar() for peer-presence updates
    this._hardError = null        // a failure a reconnect does not fix
    this._offlineTimer = null
    this._connect()
  }

  /** Wire an EditToolbar for peer-presence display callbacks. */
  setToolbar(toolbar) {
    this._toolbar = toolbar
  }

  /** Current ProseMirror document as JSON, or null if no view. */
  currentDoc() {
    return this.view?.state.doc.toJSON() ?? null
  }

  /** Current collab version number from the local ProseMirror state. */
  currentVersion() {
    return getVersion(this.view?.state) ?? 0
  }

  /** True while typed steps have not been confirmed by the server. */
  hasUnconfirmedSteps() {
    try {
      return Boolean(this.view && sendableSteps(this.view.state))
    } catch {
      return false
    }
  }

  /** Keep the field's current text in localStorage until the server confirms it. */
  _saveRescue(reason = null) {
    try {
      localStorage.setItem(rescueKey(this.documentId, this.field), JSON.stringify({
        savedAt: new Date().toISOString(),
        version: this.currentVersion(),
        doc: this.currentDoc(),
        reason,
      }))
    } catch { /* storage unavailable: the banner still shows */ }
  }

  _clearRescue() {
    try {
      localStorage.removeItem(rescueKey(this.documentId, this.field))
    } catch { /* nothing to do */ }
  }

  /**
   * Say, loudly and until resolved, that typed text is not saved.
   *
   * @param {string} message
   * @param {{ hard?: boolean }} [opts]  hard: a reconnect does not fix it.
   */
  _fail(message, { hard = false } = {}) {
    if (hard) this._hardError = message
    this._saveRescue(message)
    console.error(`[astraeus] ${message}`)
    this.toolbar?.setSaveError?.(message)
  }

  /** Everything typed is confirmed: drop the rescued copy and any error. */
  _allConfirmed() {
    clearTimeout(this._offlineTimer)
    this._offlineTimer = null
    this._hardError = null
    this._clearRescue()
    this.toolbar?.clearSaveError?.()
  }

  /** Current selection range {from, to}, or null if no view. */
  currentSelection() {
    const sel = this.view?.state.selection
    return sel ? { from: sel.from, to: sel.to } : null
  }

  _wsUrl() {
    // Convert https://cms.example.com → wss://cms.example.com
    const base = this.cmsBase.replace(/^https?/, match => match === 'https' ? 'wss' : 'ws')
    // The server scopes the session to this field; without it an edit would
    // overwrite the whole document body, so it rejects field-less connections.
    const params = new URLSearchParams({ field: this.field })
    if (this.apiKey) params.set('api_key', this.apiKey)
    return `${base}/api/documents/${this.documentId}/collab?${params}`
  }

  _connect() {
    if (this._destroyed) return
    this.ws = new WebSocket(this._wsUrl())

    this.ws.onopen = () => {
      this._reconnectDelay = 1000  // reset backoff on successful connect
      clearTimeout(this._offlineTimer)
      this._offlineTimer = null
      // A prior connection error is resolved; a refusal or a lost catch-up is not.
      if (!this._hardError) this.toolbar?.clearSaveError?.()
      // Start ping interval
      this._pingInterval = setInterval(() => {
        if (this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 30000)
    }

    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      this._handleMessage(msg)
    }

    this.ws.onclose = (event) => {
      clearInterval(this._pingInterval)
      this._pingInterval = null
      if (this._destroyed) return
      // 4401 = auth rejected by the server. Reconnecting keeps failing, so stop
      // and surface it — the body-edit equivalent of a 401 on the PATCH path.
      if (event?.code === 4401) {
        this._fail('⚠ Body not saved — session expired, log in again', { hard: true })
        return
      }
      if (this.hasUnconfirmedSteps() && !this._offlineTimer) {
        this._saveRescue('offline')
        this._offlineTimer = setTimeout(() => {
          this._offlineTimer = null
          if (this.hasUnconfirmedSteps() && this.ws?.readyState !== WebSocket.OPEN) {
            this.toolbar?.setSaveError?.(
              '⚠ Connection lost — your latest edits are not saved yet. Reconnecting… (kept in this browser)',
            )
          }
        }, OFFLINE_WARNING_MS)
      }
      // Exponential backoff reconnect
      setTimeout(() => this._connect(), this._reconnectDelay)
      this._reconnectDelay = Math.min(this._reconnectDelay * 2, 30000)
    }

    this.ws.onerror = () => {
      this.ws.close()
    }
  }

  _handleMessage(msg) {
    if (msg.type === 'pong') {
      // Keep-alive response — no action needed
      return
    }

    if (msg.type === 'error') {
      // persist_failed: the server accepted the steps but could not store them.
      // This used to be dropped on the floor.
      const detail = msg.message || msg.code || 'unknown error'
      this._fail(
        `⚠ Body not saved — the server could not store your edits (${detail}). ` +
        'Your text is kept in this browser; copy it out before reloading.',
        { hard: true },
      )
      return
    }

    if (msg.type === 'changeset') {
      // Server created a changeset to hold this body edit — adopt it so the rest
      // of the session (title/description PATCHes, other cards) groups into it.
      setActiveChangesetId(msg.id)
      return
    }

    if (msg.type === 'init') {
      // Server sends current state — handles reconnects where we may have
      // missed steps. Take its copy and version (see collab-sync.js): keeping
      // our own loaded copy let a stale editor overwrite the draft, and never
      // updating the collab plugin's version got every step rejected once the
      // server's version moved past 0.
      this.version = msg.version
      const synced = stateForServerCopy(this.view.state, {
        docJSON: msg.doc,
        version: msg.version,
        clientID: this.clientID,
        schema: this.schema,
      })
      if (synced) this.view.updateState(synced)
      // Populate peer map from the server's current peer list
      this._peers = new Map()
      for (const p of (msg.peers ?? [])) {
        this._peers.set(p.client_id, p)
      }
      this._toolbar?.updatePeers(this._peers)
      this._resumePendingSteps(msg.version)
    }

    else if (msg.type === 'peer_joined') {
      this._peers.set(msg.peer.client_id, msg.peer)
      this._toolbar?.updatePeers(this._peers)
    }

    else if (msg.type === 'peer_left') {
      this._peers.delete(msg.client_id)
      this._toolbar?.updatePeers(this._peers)
    }

    else if (msg.type === 'editing') {
      this._toolbar?.setAiEditing(msg.client_id, true)
    }

    else if (msg.type === 'editing_done') {
      this._toolbar?.setAiEditing(msg.client_id, false)
    }

    else if (msg.type === 'steps') {
      this._catchUpAnswered()
      // prosemirror-collab counts steps; it does not know which version a step
      // belongs to. A batch that does not start exactly at our version (one that
      // raced ahead of a catch-up, one we already have) would be applied to the
      // wrong document, so check before applying. `msg.version` is the version
      // *after* the batch.
      const localVersion = getVersion(this.view.state)
      const firstVersion = msg.version - msg.steps.length
      if (msg.version <= localVersion) return  // already have all of these
      if (firstVersion > localVersion) {
        // A gap: we missed steps (a broadcast raced our catch-up). Ask for them;
        // the reply starts at our version and includes this batch.
        this._requestCatchUp()
        return
      }
      const skip = localVersion - firstVersion  // overlap we already applied
      const steps = msg.steps.slice(skip).map(s => Step.fromJSON(this.schema, s))
      const clientIDs = msg.clientIDs.slice(skip)

      const tr = receiveTransaction(
        this.view.state,
        steps,
        clientIDs
      )
      this.view.dispatch(tr)
      this.version = msg.version

      // Show saved indicator after confirmation
      this.toolbar.setState('editing')

      if (msg.origin || clientIDs.some(id => typeof id === 'string' && id.startsWith('server:'))) {
        // A REST/MCP write (or a discarded draft) was folded into the session.
        this.toolbar?.setNotice?.(
          msg.origin === 'discard'
            ? 'The draft was discarded elsewhere — this field now shows the published text.'
            : 'This field was changed outside the editor — it now shows that change, with your edits on top.',
        )
      }

      // After receiving, check if we have pending local steps to send
      if (this._sendPendingSteps() === 'none') this._allConfirmed()
    }

    else if (msg.type === 'resync_required') {
      this._catchUpAnswered()
      // The server no longer has the steps between our version and its own, so
      // our pending steps cannot be rebased. Keep them and say so rather than
      // silently diverging or dropping them.
      this._fail(
        `⚠ Edits not saved — the server (version ${msg.version}) cannot merge edits based on ` +
        `version ${getVersion(this.view.state)}. Copy your text out, then reload.`,
        { hard: true },
      )
    }

    else if (msg.type === 'reject') {
      // Someone else's steps landed first, so ours were based on a stale
      // version. Our steps are still pending in the collab plugin: the server
      // broadcasts every accepted batch to all connections, and when that
      // broadcast arrives the 'steps' handler rebases through
      // receiveTransaction and retries the send.
      //
      // Closing the socket here used to strand them. Nothing resends after a
      // reconnect, so the edits stayed local — and the next keystroke sent them
      // at the same stale version, rejecting and reconnecting again.
      //
      // Not saved yet, so don't say so: this used to show "Saved", which hid
      // the version mismatch that dropped every body edit.
      this.version = msg.version
      this.toolbar.setState('saving')

      if (msg.reason && msg.reason !== 'version') {
        // Refused outright (a step the server cannot apply). Resending the same
        // steps is refused again, so every later keystroke would be lost too.
        this._fail(
          `⚠ Body edits refused by the server and not saved (${msg.reason}). ` +
          'Copy your text out, then reload.',
          { hard: true },
        )
        return
      }
      // Don't just wait for a broadcast that may never come (a server that
      // reloaded its session): ask for what we missed. A broadcast that does
      // arrive answers it too.
      if (this.view && msg.version > getVersion(this.view.state)) this._requestCatchUp()
    }
  }

  /**
   * Re-send anything left pending after a (re)connect.
   *
   * A brief drop — tunnel, wifi handoff — leaves the server where we left it,
   * so pending steps are still based on a document it recognises and can just
   * be sent.
   *
   * If the server moved on while we were away, our steps are based on a
   * document it no longer has. Rebasing needs the steps we missed, so ask for
   * them (`catch_up`, ADR 024). They come back as an ordinary `steps` message,
   * which the handler below applies with `receiveTransaction`, rebasing our
   * pending steps over them, and then resends what is left. A server that
   * cannot serve them answers `resync_required`; one that predates the message
   * ignores it, and the steps stay pending as before.
   *
   * @param {number} serverVersion
   */
  _resumePendingSteps(serverVersion) {
    if (!sendableSteps(this.view.state)) return

    const localVersion = getVersion(this.view.state)
    if (localVersion === serverVersion) {
      this._sendPendingSteps()
      return
    }

    this._requestCatchUp()
  }

  /**
   * Ask the server for the steps after our version (ADR 024).
   *
   * A server that predates `catch_up` ignores it, which would leave pending
   * edits unsaved without a word (they used to produce a warning). So if
   * nothing answers, say so.
   */
  _requestCatchUp() {
    if (this.ws?.readyState !== WebSocket.OPEN) return
    const version = getVersion(this.view.state)
    this.ws.send(JSON.stringify({ type: 'catch_up', version }))
    clearTimeout(this._catchUpTimer)
    this._catchUpTimer = setTimeout(() => {
      this._fail(
        `⚠ Edits not saved — the server did not send the changes after version ${version}, ` +
        'so your edits cannot be merged. Copy your text out, then reload.',
        { hard: true },
      )
    }, CATCH_UP_TIMEOUT_MS)
  }

  _catchUpAnswered() {
    clearTimeout(this._catchUpTimer)
    this._catchUpTimer = null
  }

  /**
   * Send whatever the collab plugin has pending.
   *
   * @returns {'sent'|'none'|'offline'}
   */
  _sendPendingSteps() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return 'offline'

    const sendable = sendableSteps(this.view.state)
    if (!sendable) return 'none'

    this.toolbar.setState('saving')

    this.ws.send(JSON.stringify({
      type: 'steps',
      steps: sendable.steps.map(s => s.toJSON()),
      clientID: this.clientID,
      version: sendable.version,
      doc: this.view.state.doc.toJSON(),  // include updated doc for server
      // Group this body edit into the session's changeset. The server links the
      // doc and, if it had to create a changeset, echoes it back (see below) so
      // plain-text edits join the same one.
      activeChangesetId: getActiveChangesetId(),
    }))
    return 'sent'
  }

  // Called by EditorView.dispatchTransaction after state update
  onTransaction() {
    this._scheduleRescue()
    this._sendPendingSteps()
  }

  /** Debounced copy of the field to localStorage while edits are unconfirmed. */
  _scheduleRescue() {
    clearTimeout(this._rescueTimer)
    this._rescueTimer = setTimeout(() => {
      if (this.hasUnconfirmedSteps()) this._saveRescue(this._hardError)
    }, 500)
  }

  destroy() {
    this._destroyed = true
    clearInterval(this._pingInterval)
    this._pingInterval = null
    this._catchUpAnswered()
    clearTimeout(this._offlineTimer)
    clearTimeout(this._rescueTimer)
    // A destroyed connection with unconfirmed edits leaves its rescue copy behind.
    if (this.hasUnconfirmedSteps()) this._saveRescue('closed with unsaved edits')
    if (this.ws) this.ws.close()
  }
}
