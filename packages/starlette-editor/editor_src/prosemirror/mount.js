/**
 * prosemirror/mount.js — Mount and destroy ProseMirror editor instances.
 */

import { EditorState } from 'prosemirror-state'
import { EditorView } from 'prosemirror-view'
import { exampleSetup } from 'prosemirror-example-setup'

import { collab } from 'prosemirror-collab'
import { schemaWithLists } from './schema.js'
import { CollabConnection } from '../collab.js'
import { markdownToPmDoc, pmDocToMarkdown } from './markdown.js'
import { execPmCommand, updateToolbarState } from './toolbar.js'
import { fieldWidget } from '../components/fields.js'
import { state } from '../state.js'

// Re-exported so existing importers of this module keep working.
export { schemaWithLists }

/** id of the shell's body-save banner. */
const BANNER_ID = 'collab-save-banner'

/**
 * Show (or replace) the shell's banner for body edits.
 *
 * Errors stay until cleared: a body edit that did not save used to show
 * nothing at all in the shell (the shim had no setSaveError), so typed text
 * was lost without a word.
 *
 * @param {string} message
 * @param {{ kind?: 'error'|'notice', getText?: () => string }} [opts]
 */
export function showCollabBanner(message, { kind = 'error', getText } = {}) {
  let banner = document.getElementById(BANNER_ID)
  if (!banner) {
    banner = document.createElement('div')
    banner.id = BANNER_ID
    banner.setAttribute('role', kind === 'error' ? 'alert' : 'status')
    Object.assign(banner.style, {
      position: 'fixed', top: '0', left: '0', right: '0', zIndex: '10000',
      padding: '10px 16px', display: 'flex', gap: '12px', alignItems: 'center',
      flexWrap: 'wrap', font: '14px/1.4 system-ui, sans-serif',
      boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
    })
    document.body.appendChild(banner)
  }
  banner.dataset.kind = kind
  banner.style.background = kind === 'error' ? '#b91c1c' : '#1d4ed8'
  banner.style.color = '#fff'
  banner.textContent = ''
  const text = document.createElement('span')
  text.textContent = message
  text.style.flex = '1 1 auto'
  banner.appendChild(text)
  if (kind === 'error' && getText) {
    const copy = document.createElement('button')
    copy.type = 'button'
    copy.textContent = 'Copy my text'
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(getText())
        copy.textContent = 'Copied'
      } catch {
        copy.textContent = 'Copy failed: select the text and copy it'
      }
    }
    banner.appendChild(copy)
  }
  if (kind === 'notice') {
    clearTimeout(banner._timer)
    banner._timer = setTimeout(() => hideCollabBanner('notice'), 8000)
  }
}

/**
 * Remove the banner, or only a banner of *kind* if given.
 *
 * @param {'error'|'notice'} [kind]
 */
export function hideCollabBanner(kind) {
  const banner = document.getElementById(BANNER_ID)
  if (!banner) return
  if (kind && banner.dataset.kind !== kind) return
  banner.remove()
}

/**
 * Toolbar for CollabConnection in the shell: the saving dot plus the banner.
 * Peer presence and AI editing animations are no-ops here for now.
 *
 * @param {() => string} getText  The field's current text, for "Copy my text".
 */
function _makeCollabToolbarShim(getText) {
  return {
    setState(s) {
      // Update dirty dot: 'saving' → show spinner class, 'editing' → clear
      const dot = document.getElementById('dirty-dot')
      if (dot) dot.classList.toggle('is-saving', s === 'saving')
    },
    setSaveError(message) {
      showCollabBanner(message, { kind: 'error', getText })
    },
    clearSaveError() {
      hideCollabBanner('error')
    },
    setNotice(message) {
      // Never cover an unresolved error with a notice.
      const current = document.getElementById(BANNER_ID)
      if (current?.dataset.kind === 'error') return
      showCollabBanner(message, { kind: 'notice' })
    },
    updatePeers() {},   // no peer presence display in shell (yet)
    setAiEditing() {},  // no AI pulse in shell (yet)
  }
}

/**
 * Mount ProseMirror on all .pm-editor placeholder elements in the form.
 *
 * @param {Array<{name: string, prop: object, meta: object}>} fields
 * @param {function} onFieldChange
 * @param {{ cmsBase?: string, docId?: string, apiKey?: string }} [opts]
 */
export async function mountProseMirrorEditors(fields, onFieldChange, { cmsBase, docId, apiKey } = {}) {
  const pmFields = fields.filter(({ name, prop, meta }) => fieldWidget(name, prop, meta) === 'prosemirror');
  if (!pmFields.length) return;

  // Every setState re-renders the form. Rebuilding a live collab editor here
  // threw away its unconfirmed steps and remounted it from formData, the copy
  // loaded when the document was opened. Keep live editors for the same
  // document and move them into the new form; destroy the rest.
  const live = {}
  for (const { name } of pmFields) {
    const conn = state.collabConnections[name]
    const view = state.pmInstances[name]
    if (conn && view && docId && conn.documentId === docId && !conn._destroyed) {
      live[name] = { conn, view }
    }
  }
  destroyPmInstances({ keep: Object.keys(live) });

  for (const { name } of pmFields) {
    const mountEl = document.getElementById(`pm-mount-${name}`);
    if (!mountEl) continue;

    const fieldMeta = (state.schema?.[state.activeType]?.field_meta || {})[name] || {};

    if (live[name]) {
      mountEl.textContent = ''
      mountEl.appendChild(live[name].view.dom)
      _wireToolbar(name)
      continue
    }

    const rawValue = state.formData[name];
    // If the stored value is already a ProseMirror JSON doc object (RichTextField),
    // restore it directly; otherwise fall back to the markdown parse path.
    let doc;
    if (rawValue && typeof rawValue === 'object' && rawValue.type === 'doc') {
      try {
        doc = schemaWithLists.nodeFromJSON(rawValue);
      } catch (e) {
        console.warn('[editor] Failed to restore PM doc from JSON, falling back to markdown:', e);
        doc = markdownToPmDoc('', schemaWithLists);
      }
    } else {
      doc = markdownToPmDoc(rawValue || '', schemaWithLists);
    }

    // cmsBase may be "" in the shell (routes.py injects it empty for same-origin serving).
    // CollabConnection._wsUrl() needs an absolute URL to build ws://... so resolve here.
    const resolvedCmsBase = cmsBase || window.location.origin

    // Rich text fields use the collab WS path when docId is known
    if (fieldMeta.field_type === 'rich_text' && docId) {
      const clientID = Math.random().toString(36).slice(2) + Date.now().toString(36)
      const pmState = EditorState.create({
        doc,
        plugins: [...exampleSetup({ schema: schemaWithLists, menuBar: false }), collab({ version: 0, clientID })],
      })
      let collabConn = null
      const view = new EditorView(mountEl, {
        state: pmState,
        dispatchTransaction(tr) {
          const newState = view.state.apply(tr)
          view.updateState(newState)
          // Refresh toolbar active state on every transaction (selection moves too)
          const toolbar = document.getElementById(`pm-toolbar-${name}`)
          updateToolbarState(view, toolbar)
          if (collabConn && tr.docChanged) collabConn.onTransaction()
        },
      })
      collabConn = new CollabConnection({
        view,
        schema: schemaWithLists,
        documentId: docId,
        field: name,
        cmsBase: resolvedCmsBase,
        initialVersion: 0,
        toolbar: _makeCollabToolbarShim(() => view.state.doc.textBetween(0, view.state.doc.content.size, '\n\n')),
        clientID,
        apiKey: apiKey || null,
      })
      state.pmInstances[name] = view
      state.collabConnections[name] = collabConn
    } else {
      // Standalone path for non-rich_text fields (markdown, etc.)
      const pmState = EditorState.create({
        doc,
        plugins: exampleSetup({ schema: schemaWithLists, menuBar: false }),
      });

      const view = new EditorView(mountEl, {
        state: pmState,
        dispatchTransaction(transaction) {
          const newState = view.state.apply(transaction);
          view.updateState(newState);
          // Refresh toolbar active state on every transaction (selection moves too)
          const toolbar = document.getElementById(`pm-toolbar-${name}`);
          updateToolbarState(view, toolbar);
          if (transaction.docChanged) {
            // Store as PM JSON when this field is a RichTextField (field_type === 'rich_text'),
            // otherwise serialise to markdown for backwards compatibility.
            const value = fieldMeta.field_type === 'rich_text'
              ? newState.doc.toJSON()
              : pmDocToMarkdown(newState.doc);
            onFieldChange(name, value);
          }
        },
      });

      state.pmInstances[name] = view;
    }

    _wireToolbar(name)
  }
}

/**
 * Wire a field's formatting toolbar buttons to its editor.
 *
 * @param {string} name
 */
function _wireToolbar(name) {
  const toolbar = document.getElementById(`pm-toolbar-${name}`);
  if (!toolbar) return;
  toolbar.addEventListener('mousedown', e => {
    e.preventDefault(); // prevent blur on editor
    const btn = e.target.closest('[data-cmd]');
    if (!btn) return;
    const cmd = btn.getAttribute('data-cmd');
    execPmCommand(state.pmInstances[name], schemaWithLists, cmd);
  });
}

/**
 * Destroy active ProseMirror instances and collab connections, then clear both maps.
 *
 * @param {{ keep?: string[] }} [opts]  Field names whose editor and connection
 *   survive (a re-render of the same document).
 */
export function destroyPmInstances({ keep = [] } = {}) {
  const pm = {}
  const conns = {}
  for (const [name, view] of Object.entries(state.pmInstances)) {
    if (keep.includes(name)) { pm[name] = view; continue }
    try { view.destroy(); } catch { /* already destroyed */ }
  }
  for (const [name, conn] of Object.entries(state.collabConnections)) {
    if (keep.includes(name)) { conns[name] = conn; continue }
    try { conn.destroy(); } catch { /* already destroyed */ }
  }
  state.pmInstances = pm;
  state.collabConnections = conns;
  if (!Object.keys(conns).length) hideCollabBanner();
}
