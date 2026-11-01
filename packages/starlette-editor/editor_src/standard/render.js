/**
 * standard/render.js — Top-level render functions for each UI panel.
 */

import { $, el, humanizeType, docTitle, formatDate, getOrderedFields } from '../utils.js'
import { buildSlugField, buildFieldGroup } from '../components/fields.js'
import { buildProseMirrorPlaceholder } from '../components/fields.js'
import { buildBlockCanvas } from '../components/block-canvas.js'
import { buildImagePickerField } from '../components/image-picker.js'
import { buildMetaPanel } from '../components/meta-panel.js'
import { mountProseMirrorEditors } from '../prosemirror/mount.js'
import { state } from '../state.js'

/** Simple emoji-based icons based on common type names. */
function typeIcon(typeKey) {
  if (typeKey.includes('blog') || typeKey.includes('post')) return '📝';
  if (typeKey.includes('page')) return '📄';
  if (typeKey.includes('project')) return '🗂';
  if (typeKey.includes('experience') || typeKey.includes('job')) return '💼';
  if (typeKey.includes('product') || typeKey.includes('item')) return '📦';
  if (typeKey.includes('user') || typeKey.includes('author')) return '👤';
  if (typeKey.includes('category') || typeKey.includes('tag')) return '🏷';
  if (typeKey.includes('media') || typeKey.includes('image')) return '🖼';
  return '◻';
}

/**
 * The name shown for a document type: its schema `label` if it has one, else
 * one made from the type key ("blog_post" → "Blog Posts").
 *
 * @param {string} typeKey
 * @returns {string}
 */
function typeLabel(typeKey) {
  return state.schema?.[typeKey]?.label || humanizeType(typeKey);
}

/**
 * Build a single sidebar type item element.
 *
 * @param {string} typeKey
 * @param {function} selectType
 * @returns {HTMLElement}
 */
function buildTypeItem(typeKey, selectType) {
  return el('div', {
    class: `sidebar-types__item${state.activeType === typeKey ? ' is-active' : ''}`,
    onclick: () => selectType(typeKey),
  },
    el('span', { class: 'sidebar-types__item-icon' }, typeIcon(typeKey)),
    el('span', {}, typeLabel(typeKey))
  );
}

/**
 * Render the type sidebar list.
 *
 * Ungrouped types appear first as a flat list. Types with a `group` value are
 * rendered under labelled `<details open>` collapsibles, one per group.
 *
 * @param {function} selectType
 */
export function renderTypeList(selectType) {
  const container = $('type-list');
  if (!container) return;
  container.innerHTML = '';

  if (state.isLoadingSchema) {
    container.appendChild(el('div', { class: 'sidebar-types__item' },
      el('div', { class: 'loading-spinner' })
    ));
    return;
  }

  if (!state.schema) {
    container.appendChild(el('div', { class: 'sidebar-types__item' }, 'No types found'));
    return;
  }

  // Partition into ungrouped (render first) and by-group maps.
  const ungrouped = [];
  /** @type {Map<string, string[]>} group name → ordered type keys */
  const grouped = new Map();

  for (const [typeKey, typeInfo] of Object.entries(state.schema)) {
    const group = typeInfo?.group;
    if (!group) {
      ungrouped.push(typeKey);
    } else {
      if (!grouped.has(group)) grouped.set(group, []);
      grouped.get(group).push(typeKey);
    }
  }

  // Render ungrouped types first
  for (const typeKey of ungrouped) {
    container.appendChild(buildTypeItem(typeKey, selectType));
  }

  // Render each group as a collapsible <details> section
  for (const [groupName, typeKeys] of grouped) {
    const items = typeKeys.map(k => buildTypeItem(k, selectType));
    const section = el('details', { class: 'sidebar-types__group', open: true },
      el('summary', { class: 'sidebar-types__group-label' }, groupName),
      ...items
    );
    container.appendChild(section);
  }
}

/**
 * Render the document list panel.
 *
 * @param {function} selectDoc
 * @param {function} openNewDoc
 */
/**
 * Filter the loaded documents by slug or title.
 *
 * Client-side over what is already loaded, which is the right scope while the
 * list is a single page — it costs no request and stays responsive per
 * keystroke. A type with more documents than one page needs a server-side
 * search instead.
 *
 * @param {Array<object>} documents
 * @param {string} filter
 * @returns {Array<object>}
 */
export function filterDocuments(documents, filter) {
  const needle = (filter || '').trim().toLowerCase();
  if (!needle) return documents;

  return documents.filter(doc =>
    `${doc.slug || ''} ${docTitle(doc)}`.toLowerCase().includes(needle),
  );
}

export function renderDocList(selectDoc, openNewDoc) {
  const titleEl = $('doc-list-title');
  const newBtn = $('doc-new-btn');
  const listEl = $('doc-list');

  if (titleEl) titleEl.textContent = state.activeType ? typeLabel(state.activeType) : '—';
  if (newBtn) newBtn.style.display = state.activeType ? '' : 'none';
  if (!listEl) return;

  listEl.innerHTML = '';

  if (!state.activeType) {
    listEl.appendChild(el('div', { class: 'sidebar-docs__empty' }, 'Select a type'));
    return;
  }

  if (state.isLoadingDocs) {
    listEl.appendChild(el('div', { class: 'sidebar-docs__empty' },
      el('div', { class: 'loading-spinner', style: 'margin: 0 auto;' })
    ));
    return;
  }

  if (!state.documents.length) {
    listEl.appendChild(el('div', { class: 'sidebar-docs__empty' }, 'No documents yet'));
    return;
  }

  const visible = filterDocuments(state.documents, state.docFilter);

  if (!visible.length) {
    listEl.appendChild(
      el('div', { class: 'sidebar-docs__empty' }, `Nothing matches "${state.docFilter}"`),
    );
    return;
  }

  for (const doc of visible) {
    const isActive = doc.id === state.activeDocId;
    const isDraftDeleted = doc.draft_deleted;

    let badge;
    if (isDraftDeleted) {
      badge = el('span', { class: 'badge badge--danger' }, 'Will delete');
    } else if (doc.draft_published != null) {
      badge = el('span', { class: 'badge badge--pending', style: 'font-style: italic' },
        doc.draft_published ? 'Will publish' : 'Will unpublish'
      );
    } else {
      badge = el('span', { class: `badge ${doc.published ? 'badge--published' : 'badge--draft'}` },
        doc.published ? 'Published' : 'Draft'
      );
    }

    const titleStyle = isDraftDeleted ? 'opacity: 0.5; text-decoration: line-through;' : '';

    const item = el('div', {
      class: `sidebar-docs__item${isActive ? ' is-active' : ''}`,
      onclick: () => selectDoc(doc.id),
    },
      el('span', { class: 'sidebar-docs__item-title', style: titleStyle }, docTitle(doc)),
      el('div', { class: 'sidebar-docs__item-meta' },
        badge,
        el('span', { class: 'sidebar-docs__item-date' }, formatDate(doc.updated_at || doc.created_at))
      )
    );
    listEl.appendChild(item);
  }
}

/**
 * Render the header bar — title, dirty dot, publish toggle, save/delete buttons.
 *
 * @param {function} togglePublish
 * @param {function} saveDocument
 * @param {function} deleteActiveDoc
 */
export function renderHeader(togglePublish, saveDocument, deleteActiveDoc) {
  const titleEl = $('header-title');
  const dirtyDot = $('dirty-dot');
  const publishWrap = $('publish-wrap');
  const saveBtn = $('save-btn');
  const deleteBtn = $('delete-btn');

  if (!titleEl) return;

  if (!state.activeType) {
    titleEl.textContent = 'CMS Editor';
    if (dirtyDot) dirtyDot.classList.remove('is-visible');
    if (publishWrap) publishWrap.style.display = 'none';
    if (saveBtn) saveBtn.style.display = 'none';
    if (deleteBtn) deleteBtn.style.display = 'none';
    return;
  }

  const isNew = state.activeDocId === null && state.activeType !== null;
  const doc = state.activeDoc;

  titleEl.textContent = isNew ? `New ${typeLabel(state.activeType).replace(/s$/, '')}` : docTitle(doc || {});
  if (dirtyDot) dirtyDot.classList.toggle('is-visible', state.isDirty);
  if (publishWrap) publishWrap.style.display = doc ? '' : 'none';
  if (saveBtn) saveBtn.style.display = '';
  if (deleteBtn) deleteBtn.style.display = doc ? '' : 'none';

  const isDraftDeleted = doc && doc.draft_deleted;

  // Update publish toggle — reflect pending draft_published state
  const toggleInput = $('publish-toggle-input');
  const toggleLabel = $('publish-toggle-label');
  if (toggleInput && doc) {
    const effectivePublished = doc.draft_published ?? doc.published;
    toggleInput.checked = !!effectivePublished;
    toggleInput.disabled = !!isDraftDeleted;
  }
  if (toggleLabel && doc) {
    if (doc.draft_published != null) {
      toggleLabel.textContent = doc.draft_published ? 'Will publish' : 'Will unpublish';
      toggleLabel.style.fontStyle = 'italic';
    } else {
      toggleLabel.textContent = doc.published ? 'Published' : 'Draft';
      toggleLabel.style.fontStyle = '';
    }
  }

  // Save button state — disabled when staged for deletion
  if (saveBtn) {
    saveBtn.disabled = state.isSaving || !!isDraftDeleted;
    saveBtn.textContent = state.isSaving ? 'Saving…' : 'Save';
  }

  // Delete button — toggle between "Delete" and "Cancel delete"
  if (deleteBtn && doc) {
    if (isDraftDeleted) {
      deleteBtn.textContent = 'Cancel delete';
      deleteBtn.className = 'btn btn--ghost';
    } else {
      deleteBtn.textContent = 'Delete';
      deleteBtn.className = 'btn btn--danger';
    }
  }

}

/**
 * Render the document form area.
 *
 * @param {function} onFieldChange
 * @param {function} render         — trigger re-render (for block canvas)
 */
export function renderForm(onFieldChange, render) {
  const formArea = $('form-area');
  if (!formArea) return;
  formArea.innerHTML = '';

  if (!state.activeType) {
    formArea.appendChild(
      el('div', { class: 'empty-state' },
        el('div', { class: 'empty-state__icon' }, '◻'),
        el('div', { class: 'empty-state__title' }, 'Select a content type'),
        el('div', { class: 'empty-state__body' }, 'Choose a type from the left sidebar to browse and edit documents.')
      )
    );
    return;
  }

  if (state.isLoadingDoc) {
    formArea.appendChild(
      el('div', { class: 'empty-state' },
        el('div', { class: 'loading-spinner', style: 'margin: 0 auto;' })
      )
    );
    return;
  }

  if (state.activeDocId === null && Object.keys(state.formData).length === 0) {
    if (state.documents.length > 0) {
      formArea.appendChild(
        el('div', { class: 'empty-state' },
          el('div', { class: 'empty-state__icon' }, '←'),
          el('div', { class: 'empty-state__title' }, 'Select a document'),
          el('div', { class: 'empty-state__body' }, 'Or click + to create a new one.')
        )
      );
      return;
    }
  }

  const typeInfo = state.schema?.[state.activeType];
  if (!typeInfo) {
    formArea.appendChild(el('div', { class: 'empty-state' }, 'Schema not found for this type.'));
    return;
  }

  const form = el('div', { class: 'doc-form', id: 'doc-form' });

  // Slug field (always present)
  form.appendChild(buildSlugField(state, onFieldChange));

  // Schema-driven fields
  const fields = getOrderedFields(typeInfo);
  for (const { name, prop, meta } of fields) {
    // Wrap buildBlockCanvas and buildImagePickerField to thread in render + onFieldChange
    const wrappedBuildBlockCanvas = (fn, p, m, ts, cv) => buildBlockCanvas(fn, p, m, ts, cv, render);
    const wrappedBuildImagePickerField = (fn, cv) => buildImagePickerField(fn, cv, onFieldChange);
    const group = buildFieldGroup(name, prop, meta, state, onFieldChange, wrappedBuildBlockCanvas, wrappedBuildImagePickerField);
    if (group) form.appendChild(group);
  }

  // Meta panel — only for existing documents
  if (state.activeDoc) {
    form.appendChild(buildMetaPanel(state.activeDoc));
  }

  formArea.appendChild(form);

  // Mount ProseMirror editors after the DOM is in place.
  // Pass collab params from __EDITOR_CONFIG__ so rich_text fields use the collab WS path.
  const cfg = window.__EDITOR_CONFIG__ || {}
  mountProseMirrorEditors(fields, onFieldChange, {
    cmsBase: cfg.cmsBase || '',
    docId: state.activeDocId,
    apiKey: cfg.apiKey || null,
  });
}
