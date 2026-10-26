/**
 * Astraeus CMS Editor — index.js
 *
 * Entry point. Wires state, render, and actions together, then boots the SPA.
 *
 * Architecture:
 *   state.js   → shared state object, mutated through setState()
 *   render.js  → full re-render of each panel on every state change
 *   api.js     → thin fetch wrapper
 *   actions.js → async user interactions → API calls → setState
 *   mount.js   → ProseMirror lifecycle (npm-bundled, no CDN)
 */

import { state, setState, setRenderFn } from './state.js'
import { fetchSchema, fetchChangeset } from './api.js'
import { showToast } from './components/toast.js'
import { ChatPanel } from './components/chat-panel.js'
import { DocumentEventsSubscriber } from './events.js'
import { EditorToolbar } from './standard/editor-toolbar.js'
import { ChangesetPanel } from './components/changeset-panel.js'
import { getActiveChangesetId, onActiveChangesetChange } from './changeset-store.js'
import {
  closeDocDrawer,
  showDocRoute,
  showTypeRoute,
  toggleDocDrawer,
  wireDocDrawerDismiss,
} from './components/doc-drawer.js'
import { PendingView } from './components/pending-view.js'
import { wireKeyboardInset } from './components/keyboard-inset.js'
import {
  renderTypeList,
  renderDocList,
  renderHeader,
  renderForm,
} from './standard/render.js'
import {
  selectType,
  selectDoc,
  openNewDoc,
  onFieldChange,
  saveDocument,
  togglePublish,
  deleteActiveDoc,
  hasUnsavedBodyEdits,
} from './standard/actions.js'
import { el } from './utils.js'

/**
 * Mirror the active changeset into shell state for the header.
 *
 * @param {string|null} csId
 */
async function syncActiveChangesetInfo(csId) {
  if (!csId) {
    setState({
      activeChangesetId: null,
      activeChangesetTitle: null,
      activeChangesetDocCount: 0,
      activeChangesetDocs: [],
    })
    return
  }

  try {
    const cs = await fetchChangeset(csId)
    const docs = cs.documents || []
    setState({
      activeChangesetId: csId,
      activeChangesetTitle: cs.title || 'Untitled',
      activeChangesetDocCount: cs.document_count ?? docs.length ?? 0,
      activeChangesetDocs: docs,
    })
  } catch {
    setState({
      activeChangesetId: csId,
      activeChangesetTitle: null,
      activeChangesetDocCount: 0,
      activeChangesetDocs: [],
    })
  }
}

/**
 * Choose a type, then advance to its documents.
 *
 * @param {string} typeKey
 */
async function selectTypeFromNav(typeKey) {
  await selectType(typeKey);
  if (state.activeType === typeKey) showDocRoute();
}

/**
 * Select a document, then dismiss the drawer if the selection took.
 *
 * Below 640px the list covers the editor, so it has to get out of the way — but
 * not when the unsaved-edit guard sent the user back to pick again.
 *
 * @param {string} docId
 */
async function selectDocFromList(docId) {
  await selectDoc(docId);
  if (state.activeDocId === docId) closeDocDrawer();
}

/**
 * Start a new document, then dismiss the drawer if the guard allowed it.
 */
async function openNewDocFromList() {
  await openNewDoc();
  if (state.activeDocId === null) closeDocDrawer();
}

/**
 * Top-level render orchestrator — called by setState() on every state change.
 */
function render() {
  renderTypeList(selectTypeFromNav);
  renderDocList(selectDocFromList, openNewDocFromList);
  renderHeader(togglePublish, saveDocument, deleteActiveDoc);
  state.editorToolbar?.update();

  // The action bar's children are hidden inline rather than removed, so :empty
  // never matches and it rendered as a bare strip with nothing in it.
  const actions = document.querySelector('.editor-header__actions');
  if (actions) {
    const hasVisible = [...actions.children].some(c => c.style.display !== 'none');
    actions.classList.toggle('is-empty', !hasVisible);
  }

  const showPending = !state.activeType && !state.activeDocId;
  const pendingEl = document.getElementById('pending-area');
  const formEl = document.getElementById('form-area');
  if (pendingEl) pendingEl.style.display = showPending ? '' : 'none';
  if (formEl) formEl.style.display = showPending ? 'none' : '';

  if (!showPending) renderForm(onFieldChange, render);
}

// Wire render into state so setState() can call it
setRenderFn(render);

// Warn before leaving the page with unsaved edits. Browsers ignore any custom
// message and show their own; preventDefault() is what triggers the prompt.
window.addEventListener('beforeunload', e => {
  if (!state.isDirty && !hasUnsavedBodyEdits()) return;

  e.preventDefault();
  e.returnValue = '';
});

/**
 * Build the initial HTML shell structure inside #app.
 */
function buildShell() {
  const root = document.getElementById('app');
  root.innerHTML = '';

  const typeSidebar = el('aside', { class: 'sidebar-types' },
    el('div', { class: 'sidebar-types__header' },
      el('span', { class: 'sidebar-types__logo' }, 'Astraeus'),
      // At full width the panel covers the backdrop and the header toggle, so
      // without this there is no way out but the device back gesture.
      el('button', {
        class: 'sidebar-types__close',
        type: 'button',
        title: 'Close',
        'aria-label': 'Close navigation',
        onclick: () => closeDocDrawer(),
      }, '\u00d7')
    ),
    el('nav', { class: 'sidebar-types__list', id: 'type-list' }),
    // Changesets and Chat are destinations, not document actions, so on small
    // screens they live here rather than in floating chrome that has to dodge
    // the action bar. Hidden at width, where the toolbar pill handles them.
    el('div', { class: 'sidebar-types__destinations' },
      el('button', {
        class: 'sidebar-types__item',
        type: 'button',
        onclick: () => {
          closeDocDrawer();
          state.changesetPanel?.toggle();
        },
      }, el('span', { class: 'sidebar-types__item-icon' }, '\u{1F4CB}'), el('span', {}, 'Changesets')),
      el('button', {
        class: 'sidebar-types__item',
        type: 'button',
        onclick: () => {
          closeDocDrawer();
          state.chatPanel?.toggle();
        },
      }, el('span', { class: 'sidebar-types__item-icon' }, '\u{1F4AC}'), el('span', {}, 'Chat'))
    )
  );

  const docSidebar = el('aside', { class: 'sidebar-docs' },
    el('div', { class: 'sidebar-docs__header' },
      el('button', {
        class: 'sidebar-docs__back',
        type: 'button',
        title: 'Back to types',
        'aria-label': 'Back to types',
        onclick: showTypeRoute,
      }, '\u2039'),
      el('span', { class: 'sidebar-docs__title', id: 'doc-list-title' }, '—'),
      el('button', {
        class: 'sidebar-docs__new-btn',
        id: 'doc-new-btn',
        title: 'New document',
        style: 'display:none',
        onclick: openNewDocFromList,
      }, '+')
    ),
    el('input', {
      class: 'sidebar-docs__search',
      id: 'doc-search',
      type: 'search',
      placeholder: 'Filter documents',
      'aria-label': 'Filter documents',
      oninput: e => setState({ docFilter: e.target.value }),
    }),
    el('div', { class: 'sidebar-docs__list', id: 'doc-list' },
      el('div', { class: 'sidebar-docs__empty' }, 'Select a type')
    )
  );

  const main = el('main', { class: 'main-content' },
    el('header', { class: 'editor-header' },
      el('button', {
        class: 'editor-header__drawer-toggle',
        type: 'button',
        title: 'Documents',
        'aria-label': 'Show document list',
        onclick: () => toggleDocDrawer(state),
      }, '☰'),
      el('div', { class: 'editor-header__title-wrap' },
        el('h1', { class: 'editor-header__title', id: 'header-title' }, 'CMS Editor'),
        el('span', { class: 'editor-header__dirty-dot', id: 'dirty-dot', title: 'Unsaved changes' })
      ),
      el('div', { class: 'editor-header__actions' },
        el('div', { class: 'publish-toggle', id: 'publish-wrap', style: 'display:none' },
          el('label', { class: 'toggle-switch', for: 'publish-toggle-input' },
            el('input', {
              type: 'checkbox',
              id: 'publish-toggle-input',
              onchange: togglePublish,
            }),
            el('span', { class: 'toggle-track' }),
            el('span', { class: 'toggle-thumb' })
          ),
          el('span', { class: 'publish-toggle__label', id: 'publish-toggle-label' }, 'Draft')
        ),
        el('button', {
          class: 'btn btn--danger',
          id: 'delete-btn',
          style: 'display:none',
          onclick: deleteActiveDoc,
        }, 'Delete'),
        el('button', {
          class: 'btn btn--primary',
          id: 'save-btn',
          style: 'display:none',
          onclick: saveDocument,
        }, 'Save')
      )
    ),
    el('div', { class: 'editor-scroll' },
      el('div', { id: 'pending-area' }),
      el('div', { id: 'form-area' })
    )
  );

  const backdrop = el('div', {
    class: 'sidebar-docs__backdrop',
    onclick: closeDocDrawer,
  });

  root.appendChild(typeSidebar);
  root.appendChild(backdrop);
  root.appendChild(docSidebar);
  root.appendChild(main);

  wireDocDrawerDismiss();
  wireKeyboardInset();
}

/**
 * Boot the editor SPA: build shell, load schema, render.
 */
async function boot() {
  buildShell();
  render(); // Show initial empty state

  setState({ isLoadingSchema: true }, false);
  renderTypeList(selectType);

  try {
    const schema = await fetchSchema();
    setState({ schema, isLoadingSchema: false });
  } catch (err) {
    setState({ isLoadingSchema: false });
    showToast('error', 'Failed to load schema', err.message);
    render();
  }

  // Mount ChatPanel — reads config from __EDITOR_CONFIG__ injected by the shell template.
  // cmsBase is "" when the shell is served same-origin (routes.py injects "" for relative
  // API calls). Resolve to origin so ChatPanel can build absolute URLs for the chat API.
  // ChatAPI is mounted at /chat, so chatBase = resolvedBase + '/chat'.
  const cfg = window.__EDITOR_CONFIG__ || {}
  const resolvedCmsBase = cfg.cmsBase || window.location.origin
  const chatPanel = new ChatPanel(resolvedCmsBase + '/chat', null, {
    apiKey: cfg.apiKey || null,
    getDocContext: () => {
      const conn = state.activeDocId
        ? (Object.values(state.collabConnections)[0] || null)
        : null
      return {
        doc_id: state.activeDocId ?? null,
        version: conn?.currentVersion?.() ?? 0,
        draft_body: conn?.currentDoc?.() ?? null,
        selection: conn?.currentSelection?.() ?? null,
        active_changeset_id: state.activeChangesetId ?? null,
      }
    },
  })
  chatPanel.mount()
  setState({ chatPanel }, false)  // store without re-render (header re-renders on doc select)

  // Subscribe to live document-list events (create/update/delete/publish) so the
  // list stays in sync with any writer — human in another tab, or the AI chat.
  // Uses resolvedCmsBase (not the /chat base) since /api/events is a CMS route.
  const eventsSubscriber = new DocumentEventsSubscriber(resolvedCmsBase, cfg.apiKey || null)
  setState({ eventsSubscriber }, false)

  // Mount ChangesetPanel. "Go to" is injected because it means something
  // different per surface — selecting a document here, following a URL on the
  // live site.
  const changesetPanel = new ChangesetPanel({
    variant: 'shell',
    onNavigate: async (docType, docId) => {
      await selectType(docType)
      await selectDoc(docId)
    },
  })
  changesetPanel.mount()

  // The header shows the active changeset's title and count. The panel writes
  // active-changeset state to changeset-store and nothing else, so the shell
  // derives what it needs from the same place rather than being pushed into.
  onActiveChangesetChange(csId => { syncActiveChangesetInfo(csId) })

  const initialCsId = getActiveChangesetId()
  setState({ changesetPanel, activeChangesetId: initialCsId }, false)
  if (initialCsId) syncActiveChangesetInfo(initialCsId)

  // Pending view — the landing until a type or document is chosen. Publishing
  // routes through the changeset panel's review step rather than happening here.
  const pendingView = new PendingView({
    onOpen: async (docType, docId) => {
      await selectType(docType)
      await selectDoc(docId)
    },
    onPublish: () => changesetPanel.toggle(),
  })
  document.getElementById('pending-area')?.appendChild(pendingView.mount())
  setState({ pendingView }, false)
  pendingView.refresh()

  // Mount floating toolbar pill
  const editorToolbar = new EditorToolbar({ changesetPanel, chatPanel, actions: cfg.actions || [] })
  editorToolbar.mount()
  setState({ editorToolbar }, false)
}

// Start when the DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
