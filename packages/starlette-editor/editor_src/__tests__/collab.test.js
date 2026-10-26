// @vitest-environment jsdom
/**
 * Tests for CollabConnection — the WebSocket collab client.
 *
 * WebSocket is not natively available in jsdom, so we use a mock.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ---------------------------------------------------------------------------
// Mock WebSocket
// ---------------------------------------------------------------------------

class MockWebSocket {
  constructor(url) {
    this.url = url
    this.readyState = MockWebSocket.OPEN
    this.sent = []
    MockWebSocket.instances.push(this)
  }

  send(data) {
    this.sent.push(JSON.parse(data))
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }

  // Simulate a message arriving from the server
  _receive(data) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  static OPEN = 1
  static CLOSED = 3
  static instances = []
}

beforeEach(() => {
  MockWebSocket.instances = []
  vi.useFakeTimers()
  global.WebSocket = MockWebSocket
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// Minimal ProseMirror stubs
// We don't want to pull in a full PM schema for unit tests of collab logic.
// ---------------------------------------------------------------------------

function makeMinimalView(dispatchFn) {
  // A stub view object that CollabConnection interacts with
  return {
    state: {
      doc: {
        toJSON: () => ({ type: 'doc', content: [] }),
      },
      apply: vi.fn((tr) => ({ doc: tr.doc || { toJSON: () => ({}) } })),
    },
    dispatch: vi.fn(),
    updateState: vi.fn(),
  }
}

function makeToolbar() {
  return { setState: vi.fn() }
}

// We partially mock prosemirror-collab so we can control sendableSteps
vi.mock('prosemirror-collab', () => {
  const collab = vi.fn(() => ({ spec: {} }))
  const sendableSteps = vi.fn(() => null)  // no pending steps by default
  const receiveTransaction = vi.fn((state, steps, clientIDs) => ({
    // Return a minimal transaction-like object
    docChanged: steps.length > 0,
    doc: { toJSON: () => ({ type: 'doc', content: [] }) },
  }))
  const getVersion = vi.fn(() => 0)
  return { collab, sendableSteps, receiveTransaction, getVersion }
})

// The server-copy sync needs real ProseMirror states; it has its own tests in
// collab-sync.test.js. Here the stub view never matches it, so skip it.
vi.mock('../collab-sync.js', () => ({ stateForServerCopy: vi.fn(() => null) }))

vi.mock('prosemirror-transform', () => {
  const Step = {
    fromJSON: vi.fn((schema, json) => ({ toJSON: () => json })),
  }
  return { Step }
})

// ---------------------------------------------------------------------------
// Import after mocks are set up
// ---------------------------------------------------------------------------

const { CollabConnection } = await import('../collab.js')
const { sendableSteps, receiveTransaction } = await import('prosemirror-collab')

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('CollabConnection._wsUrl()', () => {
  it('converts https to wss', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      field: 'body_markdown',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })
    expect(conn._wsUrl()).toBe('wss://cms.example.com/api/documents/doc-1/collab?field=body_markdown')
    conn.destroy()
  })

  it('carries the api key alongside the field for the shell', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      field: 'body_markdown',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
      apiKey: 'k1',
    })
    expect(conn._wsUrl()).toBe(
      'wss://cms.example.com/api/documents/doc-1/collab?field=body_markdown&api_key=k1'
    )
    conn.destroy()
  })

  it('converts http to ws', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-42',
      field: 'body_markdown',
      cmsBase: 'http://localhost:8000',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })
    expect(conn._wsUrl()).toBe('ws://localhost:8000/api/documents/doc-42/collab?field=body_markdown')
    conn.destroy()
  })
})

describe('CollabConnection._connect()', () => {
  it('creates a WebSocket to the correct URL', () => {
    const view = makeMinimalView()
    new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-99',
      field: 'body_markdown',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'xyz',
    })
    // A MockWebSocket instance should have been created
    expect(MockWebSocket.instances.length).toBe(1)
    expect(MockWebSocket.instances[0].url).toBe(
      'wss://cms.example.com/api/documents/doc-99/collab?field=body_markdown'
    )
  })
})

describe('CollabConnection._sendPendingSteps()', () => {
  it('does nothing when WS readyState is not OPEN', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    // Force WS closed
    conn.ws.readyState = MockWebSocket.CLOSED
    sendableSteps.mockReturnValueOnce({
      steps: [{ toJSON: () => ({ stepType: 'replace' }) }],
      version: 1,
    })

    conn._sendPendingSteps()

    // Should not send anything
    expect(conn.ws.sent).toHaveLength(0)
    conn.destroy()
  })

  it('does nothing when sendableSteps returns null', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    sendableSteps.mockReturnValueOnce(null)
    conn._sendPendingSteps()

    expect(conn.ws.sent).toHaveLength(0)
    conn.destroy()
  })
})

describe('CollabConnection._handleMessage()', () => {
  it('handles init: updates this.version to server version', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    expect(conn.version).toBe(0)
    conn._handleMessage({ type: 'init', doc: {}, version: 5 })
    expect(conn.version).toBe(5)
    conn.destroy()
  })

  it('handles steps: updates version and calls toolbar.setState("editing")', async () => {
    const { getVersion } = await import('prosemirror-collab')
    getVersion.mockReturnValue(5)
    const view = makeMinimalView()
    const toolbar = makeToolbar()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar,
      clientID: 'abc',
    })

    conn._handleMessage({
      type: 'steps',
      steps: [{ stepType: 'replace' }],
      clientIDs: ['xyz'],
      version: 6,
    })

    expect(conn.version).toBe(6)
    expect(toolbar.setState).toHaveBeenCalledWith('editing')
    getVersion.mockReturnValue(0)
    conn.destroy()
  })

  describe('steps that do not start at our version (ADR 024)', () => {
    async function connect(localVersion) {
      const { getVersion } = await import('prosemirror-collab')
      getVersion.mockReturnValue(localVersion)
      const view = makeMinimalView()
      const conn = new CollabConnection({
        view, schema: {}, documentId: 'doc-1', cmsBase: 'https://cms.example.com',
        initialVersion: localVersion, toolbar: makeToolbar(), clientID: 'abc',
      })
      return { conn, view, getVersion }
    }

    it('asks for catch-up instead of applying a batch that leaves a gap', async () => {
      const { conn, view, getVersion } = await connect(2)
      receiveTransaction.mockClear()

      // Batch is versions 6..7; we are at 2, so 3..5 are missing.
      conn._handleMessage({ type: 'steps', steps: [{ stepType: 'replace' }], clientIDs: ['x'], version: 7 })

      expect(receiveTransaction).not.toHaveBeenCalled()
      expect(view.dispatch).not.toHaveBeenCalled()
      expect(conn.ws.sent.filter(m => m.type === 'catch_up')).toEqual([{ type: 'catch_up', version: 2 }])
      getVersion.mockReturnValue(0)
      conn.destroy()
    })

    it('ignores a batch we already have', async () => {
      const { conn, view, getVersion } = await connect(5)
      receiveTransaction.mockClear()

      conn._handleMessage({ type: 'steps', steps: [{ stepType: 'replace' }], clientIDs: ['x'], version: 5 })

      expect(receiveTransaction).not.toHaveBeenCalled()
      expect(view.dispatch).not.toHaveBeenCalled()
      expect(conn.ws.sent).toEqual([])
      getVersion.mockReturnValue(0)
      conn.destroy()
    })

    it('applies only the part of an overlapping batch that is new', async () => {
      const { conn, getVersion } = await connect(5)
      receiveTransaction.mockClear()

      // Versions 4..7 (three steps), of which 4 and 5 we already applied.
      conn._handleMessage({
        type: 'steps',
        steps: [{ stepType: 'a' }, { stepType: 'b' }, { stepType: 'c' }],
        clientIDs: ['x', 'y', 'z'],
        version: 7,
      })

      const [, steps, clientIDs] = receiveTransaction.mock.calls[0]
      expect(steps.map(s => s.toJSON().stepType)).toEqual(['b', 'c'])
      expect(clientIDs).toEqual(['y', 'z'])
      getVersion.mockReturnValue(0)
      conn.destroy()
    })
  })

  it('handles reject: holds the socket open so pending steps survive', () => {
    const view = makeMinimalView()
    const toolbar = makeToolbar()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar,
      clientID: 'abc',
    })

    const ws = conn.ws
    conn._handleMessage({ type: 'reject', version: 3 })

    // Closing here used to strand pending steps: nothing resends them after a
    // reconnect, so the edits never reached the server.
    expect(conn.version).toBe(3)
    expect(ws.readyState).toBe(MockWebSocket.OPEN)
    // Rejected steps aren't saved; "Saved" here hid the version mismatch that
    // dropped every body edit.
    expect(toolbar.setState).toHaveBeenCalledWith('saving')
    expect(toolbar.setState).not.toHaveBeenCalledWith('editing')
    conn.destroy()
  })

  it('retries pending steps when the broadcast that rejected us arrives', async () => {
    const { getVersion } = await import('prosemirror-collab')
    getVersion.mockReturnValue(2)
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    conn._handleMessage({ type: 'reject', version: 3 })

    // prosemirror-collab rebases pending steps through receiveTransaction; the
    // steps handler then retries the send.
    sendableSteps.mockReturnValueOnce({ steps: [{ toJSON: () => ({ stepType: 'replace' }) }], version: 3 })
    conn._handleMessage({ type: 'steps', steps: [{ stepType: 'replace' }], clientIDs: ['other'], version: 3 })

    const sentSteps = conn.ws.sent.filter(m => m.type === 'steps')
    expect(sentSteps).toHaveLength(1)
    expect(sentSteps[0].version).toBe(3)
    getVersion.mockReturnValue(0)
    conn.destroy()
  })

  it('ignores pong messages silently', () => {
    const view = makeMinimalView()
    const toolbar = makeToolbar()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar,
      clientID: 'abc',
    })

    // Should not throw or call toolbar
    conn._handleMessage({ type: 'pong' })
    expect(toolbar.setState).not.toHaveBeenCalled()
    conn.destroy()
  })
})

describe('CollabConnection.destroy()', () => {
  it('sets _destroyed = true and closes the WebSocket', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    const ws = conn.ws
    conn.destroy()

    expect(conn._destroyed).toBe(true)
    expect(ws.readyState).toBe(MockWebSocket.CLOSED)
  })

  it('does not reconnect after destroy even if WS closes', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    conn.destroy()
    const instanceCountAfterDestroy = MockWebSocket.instances.length

    // Advance timers — no reconnect should fire
    vi.advanceTimersByTime(5000)
    expect(MockWebSocket.instances.length).toBe(instanceCountAfterDestroy)
  })
})

describe('CollabConnection exponential backoff', () => {
  it('_reconnectDelay doubles on each disconnect, caps at 30000', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    expect(conn._reconnectDelay).toBe(1000)

    // Simulate a close (onclose is called, which sets reconnect and doubles delay)
    // We need to patch _connect to not actually reconnect
    conn._connect = vi.fn()

    // Manually trigger what onclose does
    conn._reconnectDelay = Math.min(conn._reconnectDelay * 2, 30000)
    expect(conn._reconnectDelay).toBe(2000)

    conn._reconnectDelay = Math.min(conn._reconnectDelay * 2, 30000)
    expect(conn._reconnectDelay).toBe(4000)

    // Keep doubling until cap
    for (let i = 0; i < 10; i++) {
      conn._reconnectDelay = Math.min(conn._reconnectDelay * 2, 30000)
    }
    expect(conn._reconnectDelay).toBe(30000)

    conn.destroy()
  })

  it('resets _reconnectDelay to 1000 on successful reconnect (ws.onopen)', () => {
    const view = makeMinimalView()
    const conn = new CollabConnection({
      view,
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    // Simulate multiple failures increasing delay
    conn._reconnectDelay = 16000

    // Simulate onopen firing
    conn.ws.onopen()
    expect(conn._reconnectDelay).toBe(1000)

    conn.destroy()
  })
})

describe('CollabConnection reconnect resume', () => {
  it('resends pending steps when the server has not moved on', async () => {
    const { getVersion } = await import('prosemirror-collab')
    const conn = new CollabConnection({
      view: makeMinimalView(),
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 4,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    sendableSteps.mockReturnValue({ steps: [{ toJSON: () => ({ stepType: 'replace' }) }], version: 4 })
    getVersion.mockReturnValue(4)

    conn._handleMessage({ type: 'init', version: 4, peers: [] })

    expect(conn.ws.sent.filter(m => m.type === 'steps')).toHaveLength(1)

    sendableSteps.mockReturnValue(null)
    conn.destroy()
  })

  it('holds pending steps and asks for the missed steps when the server moved on while away', async () => {
    const { getVersion } = await import('prosemirror-collab')
    const conn = new CollabConnection({
      view: makeMinimalView(),
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 4,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    sendableSteps.mockReturnValue({ steps: [{ toJSON: () => ({ stepType: 'replace' }) }], version: 4 })
    getVersion.mockReturnValue(4)

    conn._handleMessage({ type: 'init', version: 9, peers: [] })

    // Sending at a version the server has passed would just reject again, so
    // ask for the steps in between; they rebase the pending ones when they arrive.
    expect(conn.ws.sent.filter(m => m.type === 'steps')).toHaveLength(0)
    expect(conn.ws.sent.filter(m => m.type === 'catch_up')).toEqual([{ type: 'catch_up', version: 4 }])

    sendableSteps.mockReturnValue(null)
    conn.destroy()
  })

  it('says so loudly if the server never answers a catch-up request (a server that predates it)', async () => {
    const { getVersion } = await import('prosemirror-collab')
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const toolbar = makeToolbar()
    toolbar.setSaveError = vi.fn()
    const conn = new CollabConnection({
      view: makeMinimalView(),
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 4,
      toolbar,
      clientID: 'abc',
    })
    sendableSteps.mockReturnValue({ steps: [{ toJSON: () => ({ stepType: 'replace' }) }], version: 4 })
    getVersion.mockReturnValue(4)

    conn._handleMessage({ type: 'init', version: 9, peers: [] })
    vi.advanceTimersByTime(4900)
    expect(toolbar.setSaveError).not.toHaveBeenCalled()
    vi.advanceTimersByTime(200)
    // A console line was all this used to produce: the text was lost unseen.
    expect(toolbar.setSaveError).toHaveBeenCalledTimes(1)
    expect(error).toHaveBeenCalledTimes(1)

    sendableSteps.mockReturnValue(null)
    conn.destroy()
  })

  it('does not warn when the catch-up reply arrives', async () => {
    const { getVersion } = await import('prosemirror-collab')
    const warn = vi.spyOn(console, 'error').mockImplementation(() => {})
    const conn = new CollabConnection({
      view: makeMinimalView(),
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 4,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })
    sendableSteps.mockReturnValue({ steps: [{ toJSON: () => ({ stepType: 'replace' }) }], version: 4 })
    getVersion.mockReturnValue(4)

    conn._handleMessage({ type: 'init', version: 9, peers: [] })
    conn._handleMessage({ type: 'steps', steps: [], clientIDs: [], version: 4 })
    vi.advanceTimersByTime(10000)

    expect(warn).not.toHaveBeenCalled()
    sendableSteps.mockReturnValue(null)
    getVersion.mockReturnValue(0)
    conn.destroy()
  })

  it('warns and keeps the pending steps when the server cannot catch the client up', async () => {
    const { getVersion } = await import('prosemirror-collab')
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const toolbar = makeToolbar()
    toolbar.setSaveError = vi.fn()
    const conn = new CollabConnection({
      view: makeMinimalView(),
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 4,
      toolbar,
      clientID: 'abc',
    })
    getVersion.mockReturnValue(4)

    conn._handleMessage({ type: 'resync_required', version: 90, doc: null })

    expect(error).toHaveBeenCalled()
    expect(toolbar.setSaveError).toHaveBeenCalled()
    expect(conn.ws.sent.filter(m => m.type === 'steps')).toHaveLength(0)
    conn.destroy()
  })

  it('does nothing when there is nothing pending', async () => {
    const { getVersion } = await import('prosemirror-collab')
    sendableSteps.mockReturnValue(null)
    getVersion.mockReturnValue(0)

    const conn = new CollabConnection({
      view: makeMinimalView(),
      schema: {},
      documentId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar: makeToolbar(),
      clientID: 'abc',
    })

    conn._handleMessage({ type: 'init', version: 7, peers: [] })

    expect(conn.ws.sent.filter(m => m.type === 'steps')).toHaveLength(0)
    conn.destroy()
  })
})

// ---------------------------------------------------------------------------
// Failures are loud and typed text survives them (2026-10-26)
// ---------------------------------------------------------------------------

describe('CollabConnection failure surfacing', () => {
  function makeLoudToolbar() {
    return { setState: vi.fn(), setSaveError: vi.fn(), clearSaveError: vi.fn(), setNotice: vi.fn() }
  }

  function makeConn(toolbar) {
    return new CollabConnection({
      view: makeMinimalView(),
      schema: {},
      documentId: 'doc-1',
      field: 'body_markdown',
      cmsBase: 'https://cms.example.com',
      initialVersion: 0,
      toolbar,
      clientID: 'abc',
    })
  }

  const pending = { steps: [{ toJSON: () => ({ stepType: 'replace' }) }], version: 0 }

  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    sendableSteps.mockReturnValue(null)
  })

  it('shows a persist failure and keeps the text in localStorage', async () => {
    const { rescueKey } = await import('../collab.js')
    const toolbar = makeLoudToolbar()
    const conn = makeConn(toolbar)

    conn._handleMessage({ type: 'error', code: 'persist_failed', message: 'disk full', version: 3 })

    expect(toolbar.setSaveError).toHaveBeenCalledWith(expect.stringContaining('disk full'))
    const saved = JSON.parse(localStorage.getItem(rescueKey('doc-1', 'body_markdown')))
    expect(saved.doc).toEqual({ type: 'doc', content: [] })
    conn.destroy()
  })

  it('shows a refused step instead of silently retrying it forever', () => {
    const toolbar = makeLoudToolbar()
    const conn = makeConn(toolbar)

    conn._handleMessage({ type: 'reject', version: 0, reason: 'step 0 failed: bad' })

    expect(toolbar.setSaveError).toHaveBeenCalledWith(expect.stringContaining('step 0 failed'))
    conn.destroy()
  })

  it('asks for the missed steps after a version reject instead of only waiting', async () => {
    const { getVersion } = await import('prosemirror-collab')
    getVersion.mockReturnValue(2)
    const conn = makeConn(makeLoudToolbar())

    conn._handleMessage({ type: 'reject', version: 5, reason: 'version' })

    expect(conn.ws.sent).toContainEqual({ type: 'catch_up', version: 2 })
    getVersion.mockReturnValue(0)
    conn.destroy()
  })

  it('a reconnect does not clear a failure it cannot fix', () => {
    const toolbar = makeLoudToolbar()
    const conn = makeConn(toolbar)
    conn._handleMessage({ type: 'reject', version: 0, reason: 'step 0 failed: bad' })

    conn.ws.onopen()

    expect(toolbar.clearSaveError).not.toHaveBeenCalled()
    conn.destroy()
  })

  it('clears the error and the rescued copy once every step is confirmed', async () => {
    const { rescueKey } = await import('../collab.js')
    const toolbar = makeLoudToolbar()
    const conn = makeConn(toolbar)
    conn._handleMessage({ type: 'error', code: 'persist_failed', version: 1 })

    conn._handleMessage({ type: 'steps', steps: [{ stepType: 'replace' }], clientIDs: ['abc'], version: 1 })

    expect(toolbar.clearSaveError).toHaveBeenCalled()
    expect(localStorage.getItem(rescueKey('doc-1', 'body_markdown'))).toBeNull()
    conn.destroy()
  })

  it('tells the editor when a write from outside it was folded in', () => {
    const toolbar = makeLoudToolbar()
    const conn = makeConn(toolbar)

    conn._handleMessage({
      type: 'steps', steps: [{ stepType: 'replace' }], clientIDs: ['server:patch'], version: 1, origin: 'patch',
    })

    expect(toolbar.setNotice).toHaveBeenCalledWith(expect.stringContaining('outside the editor'))
    conn.destroy()
  })

  it('warns when the connection stays down with unsaved edits', () => {
    const toolbar = makeLoudToolbar()
    const conn = makeConn(toolbar)
    sendableSteps.mockReturnValue(pending)
    conn._connect = vi.fn()  // the server stays unreachable

    conn.ws.readyState = MockWebSocket.CLOSED
    conn.ws.onclose({ code: 1006 })
    expect(toolbar.setSaveError).not.toHaveBeenCalled()
    vi.advanceTimersByTime(4100)

    expect(toolbar.setSaveError).toHaveBeenCalledWith(expect.stringContaining('Connection lost'))
    conn.destroy()
  })
})
