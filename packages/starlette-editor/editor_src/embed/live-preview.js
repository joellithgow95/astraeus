/**
 * Keeps the draft preview on a static page live.
 *
 * The page's draft cards (and live cards shown with their unpublished edits)
 * are drawn once from the CMS. Others change them while they are on screen: a
 * friend in the shell, an agent over MCP, the person themselves in another tab.
 * This listens on the CMS's list-level event channel (/api/events, the same one
 * the shell uses; the login cookie authorises it) and redraws what changed:
 *
 * - a field of a document on the page: fetch its draft again, refill the
 *   template's text (fields being edited in place are left to their editor) and
 *   ask the site to draw it again (`astraeus:preview`, see drafts.js);
 * - a document something on the page shows (a link a draft post picks, a staged
 *   definition): ask the site to draw the cards again, telling it which id changed;
 * - a document created, deleted or published: draw the lens again, since a draft
 *   may have joined or left it.
 *
 * Mirrors events.js: reconnect with backoff, keep-alive ping.
 */

import { docTime, q } from './drafts.js'

const DEBOUNCE_MS = 250

export class LivePreview {
  /**
   * @param {object} options
   * @param {string} options.cmsBase
   * @param {() => {cmsBase: string, context: object}|null} options.current  what is being previewed now, or null for nothing
   * @param {(card: Element, doc: object|null, options: object) => Promise<void>} options.preview  drafts.previewCard
   * @param {(card: Element, doc: object) => void} options.refill  drafts.refillDraftCard
   * @param {(id: string) => Promise<object>} options.load  the document with its draft applied
   * @param {() => Promise<void>} options.resync  draw the lens again
   * @param {typeof WebSocket} [options.WebSocketImpl]
   */
  constructor({ cmsBase, current, preview, refill, load, resync, WebSocketImpl = globalThis.WebSocket }) {
    Object.assign(this, { cmsBase, current, preview, refill, load, resync })
    this._WebSocket = WebSocketImpl
    this._ws = null
    this._delay = 1000
    this._ping = null
    this._stopped = false
    this._pending = new Map() // id → timer
    this._resyncTimer = null
  }

  start() {
    if (this._ws || !this._WebSocket) return this
    this._stopped = false
    this._connect()
    return this
  }

  stop() {
    this._stopped = true
    clearInterval(this._ping)
    this._ws?.close()
    this._ws = null
  }

  _connect() {
    if (this._stopped) return
    const url = `${this.cmsBase.replace(/^http/, 'ws')}/api/events`
    const ws = new this._WebSocket(url)
    this._ws = ws
    ws.onopen = () => {
      this._delay = 1000
      this._ping = setInterval(() => {
        if (ws.readyState === 1) ws.send(JSON.stringify({ type: 'ping' }))
      }, 30000)
    }
    ws.onmessage = (event) => {
      try { this.handle(JSON.parse(event.data)) } catch { /* malformed */ }
    }
    ws.onclose = (event) => {
      clearInterval(this._ping)
      if (this._ws === ws) this._ws = null
      if (this._stopped || event?.code === 4401) return // stopped, or the login is gone
      setTimeout(() => this._connect(), this._delay)
      this._delay = Math.min(this._delay * 2, 30000)
    }
  }

  /** One message from /api/events. Public so it can be driven directly. */
  handle(msg) {
    const event = msg?.event ?? ''
    if (!event.startsWith('document.') || !this.current()) return
    if (['document.created', 'document.deleted', 'document.published', 'document.unpublished'].includes(event)) {
      clearTimeout(this._resyncTimer)
      this._resyncTimer = setTimeout(() => this.resync(), DEBOUNCE_MS)
      return
    }
    const id = msg.document_id
    if (!id) return
    // Another post of a type the page lists, but not on the page: nothing here shows it.
    const onPage = document.querySelector(`[data-cms-id="${q(id)}"]`)
    if (!onPage && document.querySelector(`[data-cms-type="${q(msg.document_type)}"]`)) return
    clearTimeout(this._pending.get(id))
    this._pending.set(id, setTimeout(() => {
      this._pending.delete(id)
      this.redraw(id)
    }, DEBOUNCE_MS))
  }

  /** Redraw what shows document `id`. */
  async redraw(id) {
    const now = this.current()
    if (!now) return
    const options = { ...now, changed: [id] }
    const onPage = [...document.querySelectorAll(`[data-cms-id="${q(id)}"]`)]
    const own = onPage.filter((card) => card.hasAttribute('data-cms-draft-card') || card.hasAttribute('data-cms-previewed'))
    if (onPage.length && !own.length) return // a live card shown as built: its own edit mode keeps it current
    if (own.length) {
      let doc
      try { doc = await this.load(id) } catch { return }
      let moved = false
      for (const card of own) {
        if (card.hasAttribute('data-cms-draft-card')) {
          this.refill(card, doc)
          // Its date changed: it belongs elsewhere in the feed, or on another page.
          const list = card.closest('[data-cms-list]')
          const was = card.dataset.cmsDate ? new Date(card.dataset.cmsDate).valueOf() : null
          if (list && docTime(list, doc) !== was) moved = true
        }
        await this.preview(card, doc, options)
      }
      if (moved) {
        clearTimeout(this._resyncTimer)
        this._resyncTimer = setTimeout(() => this.resync(), DEBOUNCE_MS)
      }
      return
    }
    // Something a drawn card may show (a picked link, a definition): draw them again.
    const cards = [...document.querySelectorAll('[data-cms-draft-card], [data-cms-previewed]')]
    await Promise.allSettled(cards.map(async (card) => {
      const doc = await this.load(card.dataset.cmsId)
      await this.preview(card, doc, options)
    }))
  }
}
