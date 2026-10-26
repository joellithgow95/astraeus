/**
 * standard/actions.js — User-triggered async actions (select, save, publish, delete).
 */

import { state, setState } from '../state.js'
import {
  fetchDocuments,
  fetchDocument,
  createDocument,
  patchDocument,
  setDraftPublishState,
  setDraftDeleted,
  addDocToChangeset,
} from '../api.js'
import { setActiveChangesetId } from '../changeset-store.js'
import { clearDraft, draftKey, loadDraft, saveDraft } from '../draft-buffer.js'
import { showConfirm } from '../components/confirm.js'
import { showToast } from '../components/toast.js'
import { destroyPmInstances } from '../prosemirror/mount.js'
import { pmDocToMarkdown } from '../prosemirror/markdown.js'
import { docTitle } from '../utils.js'


/**
 * True while a rich-text field has typed steps the server has not confirmed.
 *
 * Those fields save over the collab socket, not through Save, so `isDirty`
 * never covers them; switching documents used to drop them without a word.
 *
 * @returns {boolean}
 */
export function hasUnsavedBodyEdits() {
  return Object.values(state.collabConnections || {})
    .some(conn => conn?.hasUnconfirmedSteps?.());
}

/**
 * Prompt before an action that would discard unsaved edits.
 *
 * @returns {Promise<boolean>} true when it is safe to proceed
 */
async function confirmDiscardIfDirty() {
  const bodyPending = hasUnsavedBodyEdits();
  if (!state.isDirty && !bodyPending) return true;

  const discarded = await showConfirm(
    'Discard unsaved changes?',
    bodyPending
      ? 'Some text you typed has not reached the server yet. Leaving now discards it.'
      : 'This document has edits that have not been saved. Leaving now discards them.',
    'Discard',
  );

  if (discarded) clearDraft(draftKey(state.activeDocId, state.activeType));

  return discarded;
}

/**
 * Offer back a buffer left behind by a tab that died mid-edit.
 *
 * Buffers are cleared on save and on confirmed discard, so one surviving here
 * was not deliberately abandoned.
 *
 * @param {string} key
 */
async function offerBufferedDraft(key) {
  const buffered = loadDraft(key);
  if (!buffered) return;

  const restore = await showConfirm(
    'Restore unsaved changes?',
    `This document has edits from ${new Date(buffered.savedAt).toLocaleString()} that were never saved.`,
    'Restore',
    'primary',
  );

  if (restore) {
    setState({ formData: buffered.formData, isDirty: true });
  } else {
    clearDraft(key);
  }
}

/**
 * Select a document type and load its document list.
 *
 * @param {string} typeKey
 */
export async function selectType(typeKey) {
  if (state.activeType === typeKey) return;

  if (!(await confirmDiscardIfDirty())) return;

  destroyPmInstances();

  setState({
    activeType: typeKey,
    documents: [],
    docsTotal: 0,
    docFilter: '',
    activeDocId: null,
    activeDoc: null,
    formData: {},
    isDirty: false,
    isLoadingDocs: true,
  });

  try {
    const result = await fetchDocuments(typeKey);
    setState({
      documents: result.documents || [],
      docsTotal: result.total || 0,
      isLoadingDocs: false,
    });
  } catch (err) {
    setState({ isLoadingDocs: false });
    showToast('error', 'Failed to load documents', err.message);
  }
}

/**
 * Select a document by ID and load its full body.
 *
 * @param {string} docId
 */
export async function selectDoc(docId) {
  if (state.activeDocId === docId) return;

  if (!(await confirmDiscardIfDirty())) return;

  destroyPmInstances();

  setState({
    activeDocId: docId,
    activeDoc: null,
    formData: {},
    isDirty: false,
    isLoadingDoc: true,
  });

  try {
    const doc = await fetchDocument(docId);
    setState({
      activeDoc: doc,
      formData: { ...(doc.body || {}), __slug: doc.slug || '' },
      isLoadingDoc: false,
      isDirty: false,
    });

    await offerBufferedDraft(draftKey(docId, state.activeType));
  } catch (err) {
    setState({ isLoadingDoc: false });
    showToast('error', 'Failed to load document', err.message);
  }
}

/**
 * Open the new document form (blank).
 */
export async function openNewDoc() {
  if (!state.activeType) return;

  if (!(await confirmDiscardIfDirty())) return;

  destroyPmInstances();
  setState({
    activeDocId: null,
    activeDoc: null,
    formData: { __slug: '' },
    isDirty: false,
  });

  await offerBufferedDraft(draftKey(null, state.activeType));
}

/**
 * Update a single field value in formData without triggering a full re-render.
 *
 * @param {string} name
 * @param {*} value
 */
export function onFieldChange(name, value) {
  state.formData = { ...state.formData, [name]: value };
  state.isDirty = true;

  saveDraft(draftKey(state.activeDocId, state.activeType), state.formData);

  // Only update the dirty indicator — no full re-render to preserve focus
  const dot = document.getElementById('dirty-dot');
  if (dot) dot.classList.add('is-visible');
}

/**
 * Save (create or update) the active document.
 */
export async function saveDocument() {
  if (!state.activeType) return;
  if (state.isSaving) return;

  // Capture before the save — a new document is buffered under its type and
  // gains an id here, so the post-save key would not match what was written.
  const bufferKey = draftKey(state.activeDocId, state.activeType);

  setState({ isSaving: true });

  const { __slug, ...bodyFields } = state.formData;
  const slug = __slug || '';

  // Flush PM editor content to bodyFields before saving.
  // RichTextField fields with an active collab connection are already persisted via WS
  // steps, so they must not be sent at all. Skipping the flush was not enough:
  // formData still held the copy loaded when the document was opened, and Save
  // sent that, overwriting every edit typed since (the 2026-10-26 data loss).
  for (const [name, view] of Object.entries(state.pmInstances)) {
    const fieldMeta = (state.schema?.[state.activeType]?.field_meta || {})[name] || {};
    if (fieldMeta.field_type === 'rich_text' && state.collabConnections[name]) {
      delete bodyFields[name]
      continue
    }
    bodyFields[name] = fieldMeta.field_type === 'rich_text'
      ? view.state.doc.toJSON()
      : pmDocToMarkdown(view.state.doc);
  }

  try {
    let savedDoc;
    if (state.activeDocId) {
      const { data, headers } = await patchDocument(
        state.activeDocId,
        { body: bodyFields, slug },
        { activeChangesetId: state.activeChangesetId || undefined },
      );
      savedDoc = data;

      // Server auto-created a changeset — adopt it
      const newCsId = headers.get('x-changeset-id');
      if (newCsId) {
        const csTitle = headers.get('x-changeset-title') || 'Untitled';
        setActiveChangesetId(newCsId);
        setState({
          activeChangesetId: newCsId,
          activeChangesetTitle: csTitle,
          activeChangesetDocCount: 1,
        }, false);
        showToast('info', `Created changeset '${csTitle}'`, 'Your edits are tracked');
      }
    } else {
      savedDoc = await createDocument(state.activeType, bodyFields, slug);

      // Auto-link new doc to active changeset
      if (state.activeChangesetId) {
        try {
          await addDocToChangeset(state.activeChangesetId, savedDoc.id);
          setState({ activeChangesetDocCount: (state.activeChangesetDocCount || 0) + 1 }, false);
        } catch (_err) {
          showToast('error', 'Could not link to changeset', _err.message);
        }
      }
    }

    const result = await fetchDocuments(state.activeType);

    setState({
      isSaving: false,
      activeDocId: savedDoc.id,
      activeDoc: savedDoc,
      formData: { ...(savedDoc.body || {}), __slug: savedDoc.slug || '' },
      isDirty: false,
      documents: result.documents || [],
      docsTotal: result.total || 0,
    });

    clearDraft(bufferKey);

    showToast('success', 'Saved', docTitle(savedDoc));
  } catch (err) {
    setState({ isSaving: false });
    showToast('error', 'Save failed', err.message);
  }
}

/**
 * Toggle publish state of the active document.
 */
export async function togglePublish() {
  const doc = state.activeDoc;
  if (!doc) return;

  const intended = doc.draft_published ?? doc.published;
  const newIntended = !intended;
  const draftPublished = (newIntended !== doc.published) ? newIntended : null;

  try {
    const { data: updated, headers } = await setDraftPublishState(
      doc.id, draftPublished, state.activeChangesetId
    );

    const newCsId = headers.get('X-Changeset-Id');
    const newCsTitle = headers.get('X-Changeset-Title');
    if (newCsId) {
      setActiveChangesetId(newCsId);
      setState({ activeChangesetId: newCsId, activeChangesetTitle: newCsTitle });
    }

    const result = await fetchDocuments(state.activeType);
    setState({
      activeDoc: updated,
      formData: { ...(updated.body || {}), __slug: updated.slug || '' },
      documents: result.documents || [],
      docsTotal: result.total || 0,
    });

    if (draftPublished != null) {
      showToast('info', draftPublished ? 'Will publish with changeset' : 'Will unpublish with changeset', docTitle(doc));
    } else {
      showToast('info', 'Publish change cancelled', docTitle(doc));
    }
  } catch (err) {
    showToast('error', 'Failed to change publish state', err.message);
  }
}

/**
 * Toggle staged deletion of the active document.
 * First click stages the delete; second click cancels it.
 */
export async function deleteActiveDoc() {
  const doc = state.activeDoc;
  if (!doc) return;

  const willDelete = !doc.draft_deleted;

  try {
    const { data: updated, headers } = await setDraftDeleted(
      doc.id, willDelete ? true : null, state.activeChangesetId
    );

    const newCsId = headers.get('X-Changeset-Id');
    const newCsTitle = headers.get('X-Changeset-Title');
    if (newCsId) {
      setActiveChangesetId(newCsId);
      setState({ activeChangesetId: newCsId, activeChangesetTitle: newCsTitle });
    }

    const result = await fetchDocuments(state.activeType);
    setState({
      activeDoc: updated,
      formData: { ...(updated.body || {}), __slug: updated.slug || '' },
      documents: result.documents || [],
      docsTotal: result.total || 0,
    });

    if (willDelete) {
      showToast('info', 'Will delete with changeset', docTitle(doc));
    } else {
      showToast('info', 'Delete cancelled', docTitle(doc));
    }
  } catch (err) {
    showToast('error', 'Delete failed', err.message);
  }
}
