// @vitest-environment jsdom
/**
 * Tests for the embed script modules.
 *
 * Tests focus on pure logic that can run without a real browser:
 *   - cmsBase derivation from URL string
 *   - EditToolbar state machine
 *   - activateField field-type detection
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { registerFieldWidget, _resetFieldWidgetsForTests } from '../field-widgets.js'

// ---------------------------------------------------------------------------
// cmsBase derivation
// ---------------------------------------------------------------------------

describe('cmsBase derivation from script src', () => {
  it('extracts origin from a full embed.js URL', () => {
    const src = 'https://cms.example.com/editor/embed.js'
    const url = new URL(src)
    expect(url.origin).toBe('https://cms.example.com')
  })

  it('extracts origin when path is just /embed.js', () => {
    const src = 'https://cms.joellithgow.com/embed.js'
    const url = new URL(src)
    expect(url.origin).toBe('https://cms.joellithgow.com')
  })

  it('handles non-standard port', () => {
    const src = 'http://localhost:8000/editor/embed.js'
    const url = new URL(src)
    expect(url.origin).toBe('http://localhost:8000')
  })

  it('returns null when scriptEl is null (defer/async context)', () => {
    // Simulate the guard: if scriptEl is null, cmsBase is null
    const scriptEl = null
    const scriptUrl = scriptEl ? new URL(scriptEl.src) : null
    const cmsBase = scriptUrl ? scriptUrl.origin : null
    expect(cmsBase).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// EditToolbar state machine
// ---------------------------------------------------------------------------

describe('EditToolbar', async () => {
  // We need a minimal DOM. Vitest uses jsdom by default.
  let EditToolbar

  beforeEach(async () => {
    // Dynamic import so the module can be re-evaluated if needed
    const mod = await import('../embed/toolbar.js')
    EditToolbar = mod.EditToolbar
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('initial state is "viewing"', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    expect(toolbar.state).toBe('viewing')
  })

  it('mount() appends toolbar element to document.body', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    toolbar.mount()
    expect(document.getElementById('astraeus-toolbar')).not.toBeNull()
  })

  it('setState("editing") changes state and re-renders', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    toolbar.mount()
    toolbar.setState('editing')
    expect(toolbar.state).toBe('editing')
    // In editing state the toolbar should show Publish and Discard buttons
    const el = document.getElementById('astraeus-toolbar')
    expect(el.textContent).toContain('Publish')
    expect(el.textContent).toContain('Discard draft')
  })

  it('editing state offers a Close button to leave edit mode', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    toolbar.mount()
    toolbar.setState('editing')
    const el = document.getElementById('astraeus-toolbar')
    expect(el.textContent).toContain('Close')
    // viewing state must NOT show it — only the Edit draft button
    toolbar.setState('viewing')
    expect(document.getElementById('astraeus-toolbar').textContent).not.toContain('Close')
  })

  it('clicking Close stops editing without discarding or publishing', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    const stopSpy = vi.spyOn(toolbar, '_stopEditing').mockImplementation(() => {})
    toolbar.mount()
    toolbar.setState('editing')

    const closeBtn = [...document.getElementById('astraeus-toolbar').querySelectorAll('button')]
      .find(b => b.textContent.includes('Close'))
    expect(closeBtn).toBeTruthy()
    closeBtn.click()

    expect(stopSpy).toHaveBeenCalledOnce()
  })

  it('setState("saving") shows saving indicator', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    toolbar.mount()
    toolbar.setState('saving')
    expect(toolbar.state).toBe('saving')
    const el = document.getElementById('astraeus-toolbar')
    expect(el.textContent).toContain('Saving')
  })

  it('setState("publishing") shows publishing indicator', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    toolbar.mount()
    toolbar.setState('publishing')
    expect(toolbar.state).toBe('publishing')
    const el = document.getElementById('astraeus-toolbar')
    expect(el.textContent).toContain('Publishing')
  })

  it('setState("published") shows published indicator', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    toolbar.mount()
    toolbar.setState('published')
    expect(toolbar.state).toBe('published')
    const el = document.getElementById('astraeus-toolbar')
    expect(el.textContent).toContain('Published')
  })

  it('viewing state renders an Edit button', () => {
    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    toolbar.mount()
    // viewing is initial state
    const el = document.getElementById('astraeus-toolbar')
    expect(el.textContent).toContain('Edit')
  })

  it('_publish() with no changeset publishes the one active document', async () => {
    localStorage.clear()  // no active changeset → single-doc path
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    const cmsBase = 'https://cms.example.com'
    const mockEl = { dataset: { cmsId: 'doc-123' } }
    const toolbar = new EditToolbar({ cmsBase, cmsElements: [mockEl] })
    toolbar.mount()
    toolbar.activeElements = [mockEl]
    toolbar.setState('editing')

    await toolbar._publish()

    expect(fetchMock).toHaveBeenCalledWith(
      `${cmsBase}/api/documents/doc-123/publish`,
      expect.objectContaining({ method: 'POST', credentials: 'include' })
    )
    expect(toolbar.state).toBe('published')
  })

  it('_publish() with an active changeset publishes the whole changeset', async () => {
    const { setActiveChangesetId, getActiveChangesetId } = await import('../changeset-store.js')
    setActiveChangesetId('cs-session')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    const cmsBase = 'https://cms.example.com'
    const toolbar = new EditToolbar({ cmsBase, cmsElements: [{ dataset: { cmsId: 'd1' } }] })
    toolbar.mount()
    toolbar.activeElements = [{ dataset: { cmsId: 'd1' } }]

    await toolbar._publish()

    // Ships the whole session's changeset, not one doc.
    expect(fetchMock).toHaveBeenCalledWith(
      `${cmsBase}/api/changesets/cs-session/publish`,
      expect.objectContaining({ method: 'POST', credentials: 'include' })
    )
    expect(toolbar.state).toBe('published')
    // Session shipped — the active changeset is cleared so the next edits start fresh.
    expect(getActiveChangesetId()).toBeNull()
  })

  it('_publish() does nothing when the confirm is declined', async () => {
    const { setActiveChangesetId } = await import('../changeset-store.js')
    setActiveChangesetId('cs-x')
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    const toolbar = new EditToolbar({ cmsBase: 'https://cms.example.com', cmsElements: [] })
    toolbar.mount()
    toolbar.activeElements = [{ dataset: { cmsId: 'd1' } }]
    toolbar.setState('editing')

    await toolbar._publish()

    // Reads to name the target are fine; nothing may be published.
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toEqual([])
    expect(toolbar.state).toBe('editing')
    localStorage.clear()
  })

  it('_discardDraft() discards every active document and reloads', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const reloadMock = vi.fn()
    Object.defineProperty(window, 'location', {
      value: { reload: reloadMock },
      writable: true,
    })

    const cmsBase = 'https://cms.example.com'
    const els = [{ dataset: { cmsId: 'doc-456' } }, { dataset: { cmsId: 'doc-789' } }]
    const toolbar = new EditToolbar({ cmsBase, cmsElements: els })
    toolbar.mount()
    toolbar.activeElements = els

    await toolbar._discardDraft()

    for (const id of ['doc-456', 'doc-789']) {
      expect(fetchMock).toHaveBeenCalledWith(
        `${cmsBase}/api/documents/${id}/discard-draft`,
        expect.objectContaining({ method: 'POST', credentials: 'include' })
      )
    }
    expect(reloadMock).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// activateField — field type detection
// ---------------------------------------------------------------------------

describe('activateField field-type detection', async () => {
  const { activateField } = await import('../embed/edit-mode.js')

  beforeEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('detects ProseMirror JSON doc by type === "doc"', async () => {
    const pmDoc = { type: 'doc', content: [] }

    // Mock the dynamic import of prosemirror-embed
    vi.mock('../embed/prosemirror-embed.js', () => ({
      mountProseMirrorOnElement: vi.fn().mockResolvedValue(undefined),
    }))

    const el = document.createElement('div')
    document.body.appendChild(el)
    const toolbar = { setState: vi.fn() }

    // Should not throw — the mock handles the PM mount
    await expect(
      activateField(el, {
        fieldName: 'body',
        fieldValue: pmDoc,
        docId: 'doc-1',
        cmsBase: 'https://cms.example.com',
        toolbar,
      })
    ).resolves.toBeUndefined()
  })

  it('makes plain string fields contentEditable for heading tags', async () => {
    const el = document.createElement('h1')
    el.textContent = 'Original title'
    document.body.appendChild(el)
    const toolbar = { setState: vi.fn() }

    await activateField(el, {
      fieldName: 'title',
      fieldValue: 'Original title',
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar,
    })

    expect(el.contentEditable).toBe('true')
  })

  it('shows the draft value in a plain text field, since the page was built from the published one', async () => {
    const el = document.createElement('h1')
    el.textContent = 'Published title'
    document.body.appendChild(el)

    await activateField(el, {
      fieldName: 'title',
      fieldValue: 'Draft title',
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar: { setState: vi.fn() },
    })

    expect(el.textContent).toBe('Draft title')
  })

  it('leaves a text field that has markup inside alone', async () => {
    const el = document.createElement('p')
    el.innerHTML = 'Some <em>marked up</em> text'
    document.body.appendChild(el)

    await activateField(el, {
      fieldName: 'description',
      fieldValue: 'Something else',
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar: { setState: vi.fn() },
    })

    expect(el.innerHTML).toBe('Some <em>marked up</em> text')
  })

  it('makes plain string fields contentEditable for <p> tag', async () => {
    const el = document.createElement('p')
    el.textContent = 'Some text'
    document.body.appendChild(el)
    const toolbar = { setState: vi.fn() }

    await activateField(el, {
      fieldName: 'excerpt',
      fieldValue: 'Some text',
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar,
    })

    expect(el.contentEditable).toBe('true')
  })

  it('does not activate a non-string value on a div (no-op)', async () => {
    const el = document.createElement('div')
    document.body.appendChild(el)
    const toolbar = { setState: vi.fn() }

    // fieldValue is a plain object but NOT a ProseMirror doc (no .type === "doc")
    await activateField(el, {
      fieldName: 'meta',
      fieldValue: { foo: 'bar' },
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar,
    })

    // Should be unmodified — contentEditable not set (jsdom returns undefined or "inherit")
    expect(el.contentEditable === 'inherit' || el.contentEditable === undefined).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Registry-dispatched field types (ADR 020) — the collision this exists to
// prevent: an array-shaped value with a registered field_type must never
// fall into case 4's tags-editor guess.
// ---------------------------------------------------------------------------

describe('activateField: registry dispatch takes precedence over value-shape guessing', async () => {
  const { activateField } = await import('../embed/edit-mode.js')

  beforeEach(() => {
    document.body.innerHTML = ''
    _resetFieldWidgetsForTests()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    _resetFieldWidgetsForTests()
    vi.restoreAllMocks()
  })

  it('an array value with a registered field_type is handed to the registered widget, not the tags editor', async () => {
    const factory = vi.fn()
    registerFieldWidget('doodles', factory)

    const el = document.createElement('div')
    el.dataset.cmsFieldType = 'doodles'
    document.body.appendChild(el)
    const toolbar = { setState: vi.fn() }

    await activateField(el, {
      fieldName: 'doodles',
      fieldValue: [{ id: 'dd_1', path_data: 'M0 0' }],
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar,
    })

    el.click()

    expect(factory).toHaveBeenCalledOnce()
    const [value, ctx] = factory.mock.calls[0]
    expect(value).toEqual([{ id: 'dd_1', path_data: 'M0 0' }])
    expect(ctx.mode).toBe('overlay')
    expect(typeof ctx.onSave).toBe('function')
    // Did NOT open the tags panel.
    expect(document.querySelector('.__cms-tags-panel')).toBeNull()
  })

  it('re-enables pointer events on activation, for fields whose static render CSS is pointer-events: none', async () => {
    // A doodle overlay is pointer-events: none in its normal decorative
    // display (so it never blocks clicks on real content beneath it) — the
    // one activated for editing must override that, or the click meant to
    // open its editor can never reach it at all.
    registerFieldWidget('doodles', vi.fn())

    const el = document.createElement('div')
    el.dataset.cmsFieldType = 'doodles'
    el.style.pointerEvents = 'none'
    document.body.appendChild(el)

    await activateField(el, {
      fieldName: 'doodles',
      fieldValue: [],
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar: { setState: vi.fn() },
    })

    expect(el.style.pointerEvents).toBe('auto')
  })

  it('onSave from the registered widget PATCHes the field and closes the panel', async () => {
    let capturedOnSave
    registerFieldWidget('doodles', (_value, ctx) => {
      capturedOnSave = ctx.onSave
    })
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, headers: new Headers() })
    vi.stubGlobal('fetch', fetchMock)

    const el = document.createElement('div')
    el.dataset.cmsFieldType = 'doodles'
    document.body.appendChild(el)

    await activateField(el, {
      fieldName: 'doodles',
      fieldValue: [],
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar: { setState: vi.fn() },
    })
    el.click()

    await capturedOnSave([{ id: 'dd_2', path_data: 'M1 1' }])

    expect(fetchMock).toHaveBeenCalledWith(
      'https://cms.example.com/api/documents/doc-1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ body: { doodles: [{ id: 'dd_2', path_data: 'M1 1' }] } }),
      }),
    )
  })

  it('an array value with NO registered field_type still opens the tags editor unchanged', async () => {
    const el = document.createElement('div')
    document.body.appendChild(el)
    const toolbar = { setState: vi.fn() }

    await activateField(el, {
      fieldName: 'tags',
      fieldValue: ['a', 'b'],
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar,
    })

    el.click()

    expect(document.querySelector('.__cms-tags-panel')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// patchField — changeset grouping (via the contentEditable input path)
// ---------------------------------------------------------------------------

describe('inline edits join the active changeset', async () => {
  const { activateField } = await import('../embed/edit-mode.js')
  const { getActiveChangesetId, setActiveChangesetId } = await import('../changeset-store.js')

  beforeEach(() => {
    document.body.innerHTML = ''
    localStorage.clear()
    vi.restoreAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  /** Drive one debounced edit and return the fetch mock's call args. */
  async function editOnce(fetchImpl) {
    const fetchMock = vi.fn(fetchImpl)
    vi.stubGlobal('fetch', fetchMock)

    const el = document.createElement('h1')
    el.textContent = 'Before'
    document.body.appendChild(el)

    await activateField(el, {
      fieldName: 'title',
      fieldValue: 'Before',
      docId: 'doc-1',
      cmsBase: 'https://cms.example.com',
      toolbar: { setState: vi.fn() },
    })

    el.textContent = 'After'
    el.dispatchEvent(new Event('input'))
    await vi.runAllTimersAsync()

    return fetchMock
  }

  const okResponse = (headers = {}) => ({ ok: true, headers: new Headers(headers) })

  it('sends the active changeset id as a header', async () => {
    setActiveChangesetId('cs-live')
    const fetchMock = await editOnce(() => Promise.resolve(okResponse()))

    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers['X-Active-Changeset-Id']).toBe('cs-live')
  })

  it('omits the header when no changeset is active', async () => {
    const fetchMock = await editOnce(() => Promise.resolve(okResponse()))

    const [, init] = fetchMock.mock.calls[0]
    expect('X-Active-Changeset-Id' in init.headers).toBe(false)
  })

  it('adopts a changeset the server auto-creates', async () => {
    expect(getActiveChangesetId()).toBeNull()
    await editOnce(() => Promise.resolve(okResponse({ 'X-Changeset-Id': 'cs-new' })))

    // Now stored, so the panel and later edits group into it.
    expect(getActiveChangesetId()).toBe('cs-new')
  })
})

// ---------------------------------------------------------------------------
// suppressAnchorNavigationWhileEditing — clicks-to-edit don't navigate
// ---------------------------------------------------------------------------

describe('suppressAnchorNavigationWhileEditing', async () => {
  const { suppressAnchorNavigationWhileEditing } = await import('../embed/edit-mode.js')

  it('cancels navigation when an editable field inside a link is clicked, but not otherwise', () => {
    suppressAnchorNavigationWhileEditing()
    document.body.innerHTML = `
      <a id="edit-link" href="/somewhere"><span data-cms-field="title">Title</span></a>
      <a id="plain-link" href="/elsewhere">Just a link</a>
    `

    const onEditable = new MouseEvent('click', { bubbles: true, cancelable: true })
    document.querySelector('[data-cms-field]').dispatchEvent(onEditable)
    expect(onEditable.defaultPrevented).toBe(true)

    // A normal link elsewhere on the page still navigates.
    const onPlain = new MouseEvent('click', { bubbles: true, cancelable: true })
    document.getElementById('plain-link').dispatchEvent(onPlain)
    expect(onPlain.defaultPrevented).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Collapsible body field on a listing card (Expand/Collapse toggle)
// ---------------------------------------------------------------------------

describe('collapsible body field', async () => {
  const { activateEditMode } = await import('../embed/edit-mode.js')
  const flush = () => new Promise((r) => setTimeout(r, 0))

  beforeEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('gates the body behind an Expand/Collapse toggle and mounts the editor once', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ body: { body_markdown: { type: 'doc', content: [] } } }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const { mountProseMirrorOnElement } = await import('../embed/prosemirror-embed.js')
    mountProseMirrorOnElement.mockClear()

    const card = document.createElement('article')
    card.dataset.cmsId = 'doc-1'
    const preview = document.createElement('div')
    preview.dataset.cmsField = 'body_markdown'
    preview.setAttribute('data-cms-collapsible', '')
    preview.textContent = 'Truncated preview…'
    card.appendChild(preview)
    document.body.appendChild(card)

    await activateEditMode(card, { cmsBase: 'https://cms.example.com', toolbar: { setState: vi.fn() } })

    // Toggle exists; preview still shown; editor not mounted yet.
    const toggle = [...card.querySelectorAll('button')].find((b) => b.textContent.includes('Expand'))
    expect(toggle).toBeTruthy()
    expect(preview.style.display).not.toBe('none')
    expect(mountProseMirrorOnElement).not.toHaveBeenCalled()

    // Expand → mounts editor, hides preview, relabels to Collapse.
    toggle.click()
    await flush()
    expect(mountProseMirrorOnElement).toHaveBeenCalledOnce()
    expect(preview.style.display).toBe('none')
    expect(toggle.textContent).toContain('Collapse')

    // Collapse → preview returns, no re-mount.
    toggle.click()
    await flush()
    expect(preview.style.display).not.toBe('none')
    expect(toggle.textContent).toContain('Expand')

    // Re-expand → editor stays mounted once (visibility only).
    toggle.click()
    await flush()
    expect(mountProseMirrorOnElement).toHaveBeenCalledOnce()
    expect(preview.style.display).toBe('none')
  })
})
