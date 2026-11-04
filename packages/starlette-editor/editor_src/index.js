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
  openDocDrawer,
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
  publishChanges,
  deleteActiveDoc,
  hasUnsavedBodyEdits,
} from './standard/actions.js'
import { el } from './utils.js'
import { icon } from './icons.js'
import { buildThemeToggle } from './theme.js'
import { createLayout } from './components/layout.js'
import { startFieldSync, unsavedFields } from './field-sync.js'
import { hasMedia } from './media/media-api.js'
import { MediaSection } from './media/media-library.js'

/** The Media section, made in buildShell when the editor has a library. */
let mediaSection = null

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
  closeMedia();
  await selectType(typeKey);
  if (state.activeType === typeKey) showDocRoute();
}

/** The URL fragment that opens Media, so the portal and a bookmark can link to it. */
const MEDIA_HASH = '#media'

/**
 * Point the URL at the section showing, without a history entry (the drawer
 * uses history for the back gesture). The app bar listens for hashchange.
 *
 * @param {boolean} media
 */
function syncMediaHash(media) {
  if ((location.hash === MEDIA_HASH) === media) return;
  try {
    history.replaceState(history.state, '', media ? MEDIA_HASH : location.pathname + location.search);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } catch { /* history unavailable */ }
}

/**
 * Show the Media section in place of a type's documents. The open document,
 * if any, stays loaded behind it, so going back to the type finds it as it was.
 */
function openMedia() {
  if (!mediaSection) return;
  if (!state.mediaOpen) setState({ mediaOpen: true });
  mediaSection.show();
  showDocRoute();
  syncMediaHash(true);
}

function closeMedia() {
  if (!state.mediaOpen) return;
  mediaSection?.hide();
  setState({ mediaOpen: false });
  syncMediaHash(false);
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

  // Media takes over the document column and the main pane; editor.css hides
  // the document parts under #app.is-media.
  const app = document.getElementById('app');
  app?.classList.toggle('is-media', state.mediaOpen);
  document.getElementById('media-nav-item')?.classList.toggle('is-active', state.mediaOpen);
  if (state.mediaOpen) {
    for (const item of document.querySelectorAll('#type-list .is-active')) item.classList.remove('is-active');
    const listTitle = document.getElementById('doc-list-title');
    if (listTitle) listTitle.textContent = 'Media';
    const headerTitle = document.getElementById('header-title');
    if (headerTitle) headerTitle.textContent = 'Media';
    return;
  }

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
  const fieldsPending = state.activeDocId
    ? Object.keys(unsavedFields()).length > 0
    : state.isDirty;
  if (!fieldsPending && !hasUnsavedBodyEdits()) return;

  e.preventDefault();
  e.returnValue = '';
});

/**
 * Build the initial HTML shell structure inside #app.
 */
function buildShell() {
  const root = document.getElementById('app');
  root.innerHTML = '';

  // Columns are wired once the panels exist; the controls are made first so
  // the headers below can place them. createLayout only reads the panels'
  // widths when a drag starts.
  const columns = {};
  const layout = createLayout(root, columns);

  const typeSidebar = el('aside', { class: 'sidebar-types' },
    el('div', { class: 'sidebar-types__header' },
      el('span', { class: 'sidebar-types__logo' }, 'Astraeus'),
      el('div', { class: 'sidebar-types__tools' }, layout.navToggle),
      // At full width the panel covers the backdrop and the header toggle, so
      // without this there is no way out but the device back gesture.
      el('button', {
        class: 'sidebar-types__close icon-btn',
        type: 'button',
        title: 'Close',
        'aria-label': 'Close navigation',
        onclick: () => closeDocDrawer(),
      }, icon('x', { size: 20 }))
    ),
    el('nav', { class: 'sidebar-types__list', id: 'type-list' }),
    // Media is a library, not a document type, so it sits under the types
    // rather than among them.
    hasMedia()
      ? el('div', { class: 'sidebar-types__media' },
          el('button', {
            class: 'sidebar-types__item',
            id: 'media-nav-item',
            type: 'button',
            title: 'Media',
            onclick: openMedia,
          }, el('span', { class: 'sidebar-types__item-icon' }, icon('image')), el('span', { class: 'sidebar-types__item-label' }, 'Media')))
      : null,
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
      }, el('span', { class: 'sidebar-types__item-icon' }, icon('layers')), el('span', { class: 'sidebar-types__item-label' }, 'Changesets')),
      el('button', {
        class: 'sidebar-types__item',
        type: 'button',
        onclick: () => {
          closeDocDrawer();
          state.chatPanel?.toggle();
        },
      }, el('span', { class: 'sidebar-types__item-icon' }, icon('chat')), el('span', { class: 'sidebar-types__item-label' }, 'Chat'))
    ),
    // At the foot so it stays reachable when the column is an icon rail.
    el('div', { class: 'sidebar-types__footer' }, buildThemeToggle())
  );

  const docSidebar = el('aside', { class: 'sidebar-docs' },
    el('div', { class: 'sidebar-docs__header' },
      el('button', {
        class: 'sidebar-docs__back icon-btn',
        type: 'button',
        title: 'Back to types',
        'aria-label': 'Back to types',
        onclick: showTypeRoute,
      }, icon('chevron-left', { size: 20 })),
      el('span', { class: 'sidebar-docs__title', id: 'doc-list-title' }, 'Documents'),
      el('button', {
        class: 'sidebar-docs__new-btn icon-btn',
        id: 'doc-new-btn',
        type: 'button',
        title: 'New document',
        'aria-label': 'New document',
        style: 'display:none',
        onclick: openNewDocFromList,
      }, icon('plus')),
      el('span', { class: 'sidebar-docs__media-tools', id: 'media-tools' }),
      layout.docsToggle
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
      el('div', { class: 'sidebar-docs__empty' }, 'Choose a type to see its documents')
    ),
    el('div', { class: 'sidebar-docs__media', id: 'media-list' })
  );

  const main = el('main', { class: 'main-content' },
    el('header', { class: 'editor-header' },
      el('button', {
        class: 'editor-header__drawer-toggle icon-btn',
        type: 'button',
        title: 'Documents',
        'aria-label': 'Show document list',
        onclick: () => toggleDocDrawer(state.mediaOpen ? { activeType: 'media' } : state),
      }, icon('menu', { size: 20 })),
      layout.docsReveal,
      el('div', { class: 'editor-header__title-wrap' },
        el('h1', { class: 'editor-header__title', id: 'header-title' }, 'CMS Editor'),
        el('span', { class: 'editor-header__dirty-dot', id: 'dirty-dot', title: 'Unsaved changes' }),
        el('span', { class: 'save-status', id: 'save-status', style: 'display:none', 'aria-live': 'polite' })
      ),
      el('div', { class: 'editor-header__actions' },
        // A published document with saved edits the live site lacks (save-status.js).
        el('div', { class: 'draft-state', id: 'draft-state', style: 'display:none' },
          el('span', { class: 'draft-state__label', 'data-draft-label': '' }, 'Unpublished changes'),
          el('button', {
            class: 'btn btn--primary btn--sm',
            type: 'button',
            onclick: publishChanges,
          }, 'Publish changes')
        ),
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
      el('div', { id: 'form-area' }),
      el('div', { id: 'media-area' })
    )
  );

  const backdrop = el('div', {
    class: 'sidebar-docs__backdrop',
    onclick: closeDocDrawer,
  });

  columns.nav = typeSidebar;
  columns.docs = docSidebar;

  root.appendChild(typeSidebar);
  root.appendChild(layout.navResizer);
  root.appendChild(backdrop);
  root.appendChild(docSidebar);
  root.appendChild(layout.docsResizer);
  root.appendChild(main);

  if (hasMedia()) {
    mediaSection = new MediaSection({
      listEl: document.getElementById('media-list'),
      mainEl: document.getElementById('media-area'),
      onSelect: () => closeDocDrawer(),
    });
    document.getElementById('media-tools')?.appendChild(mediaSection.uploadButton);
  }

  wireDocDrawerDismiss();
  wireKeyboardInset();
}

/**
 * Boot the editor SPA: build shell, load schema, render.
 */
async function boot() {
  buildShell();
  startFieldSync();
  render(); // Show initial empty state

  // /editor/#media opens straight onto Media (the portal's Media card); the
  // app bar's Media link changes only the hash while the editor is open.
  if (location.hash === MEDIA_HASH) {
    openMedia();
    // On a phone the grid is in the drawer, so open it rather than land on an
    // empty pane.
    if (window.matchMedia?.('(max-width: 640px)').matches) openDocDrawer({ activeType: 'media' });
  }
  window.addEventListener('hashchange', () => {
    if (location.hash === MEDIA_HASH && !state.mediaOpen) openMedia();
  });

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
