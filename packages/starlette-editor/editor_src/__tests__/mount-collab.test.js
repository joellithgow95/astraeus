// @vitest-environment jsdom

/**
 * A re-render of the shell form must not rebuild a live collab editor.
 *
 * Every setState re-renders the form. Remounting destroyed the editor's
 * connection with any unconfirmed steps and rebuilt it from formData, which
 * holds the copy loaded when the document was opened.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'

const conns = vi.hoisted(() => [])

vi.mock('../collab.js', () => ({
  CollabConnection: class {
    constructor(opts) {
      Object.assign(this, opts)
      this._destroyed = false
      this.destroy = vi.fn(() => { this._destroyed = true })
      this.onTransaction = vi.fn()
      conns.push(this)
    }
  },
}))

import { mountProseMirrorEditors, destroyPmInstances, showCollabBanner } from '../prosemirror/mount.js'
import { state } from '../state.js'

const FIELDS = [{ name: 'body', prop: {}, meta: { field_type: 'rich_text' } }]

function renderForm() {
  document.body.innerHTML = '<div id="pm-mount-body"></div>'
}

beforeEach(() => {
  conns.length = 0
  destroyPmInstances()
  state.schema = { post: { field_meta: { body: { field_type: 'rich_text' } } } }
  state.activeType = 'post'
  state.formData = { body: { type: 'doc', content: [{ type: 'paragraph' }] } }
})

describe('mountProseMirrorEditors re-render', () => {
  it('keeps the live editor and its connection for the same document', async () => {
    renderForm()
    await mountProseMirrorEditors(FIELDS, vi.fn(), { cmsBase: 'http://x', docId: 'doc-1' })
    const view = state.pmInstances.body

    renderForm()  // the form is rebuilt from scratch on every setState
    await mountProseMirrorEditors(FIELDS, vi.fn(), { cmsBase: 'http://x', docId: 'doc-1' })

    expect(conns).toHaveLength(1)
    expect(conns[0].destroy).not.toHaveBeenCalled()
    expect(state.pmInstances.body).toBe(view)
    expect(document.getElementById('pm-mount-body').contains(view.dom)).toBe(true)
  })

  it('replaces the editor when another document is opened', async () => {
    renderForm()
    await mountProseMirrorEditors(FIELDS, vi.fn(), { cmsBase: 'http://x', docId: 'doc-1' })
    renderForm()
    await mountProseMirrorEditors(FIELDS, vi.fn(), { cmsBase: 'http://x', docId: 'doc-2' })

    expect(conns).toHaveLength(2)
    expect(conns[0].destroy).toHaveBeenCalled()
  })

  it('gives the shell connection a toolbar that shows save errors', async () => {
    renderForm()
    await mountProseMirrorEditors(FIELDS, vi.fn(), { cmsBase: 'http://x', docId: 'doc-1' })

    conns[0].toolbar.setSaveError('⚠ Body not saved')

    const banner = document.getElementById('collab-save-banner')
    expect(banner.textContent).toContain('Body not saved')
    expect(banner.querySelector('button').textContent).toBe('Copy my text')
    // A notice never hides an error.
    conns[0].toolbar.setNotice('changed elsewhere')
    expect(banner.textContent).toContain('Body not saved')
    conns[0].toolbar.clearSaveError()
    expect(document.getElementById('collab-save-banner')).toBeNull()
  })

  it('banner notice can be shown on its own', () => {
    showCollabBanner('hello', { kind: 'notice' })
    expect(document.getElementById('collab-save-banner').dataset.kind).toBe('notice')
  })
})
