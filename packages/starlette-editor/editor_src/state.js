/**
 * state.js — Shared application state and setState() mutator.
 */

/** @type {function(): void} — render orchestrator, injected at boot. */
let _render = () => {};

/**
 * Register the top-level render function so setState can call it.
 * Called once from index.js before boot().
 *
 * @param {function(): void} fn
 */
export function setRenderFn(fn) {
  _render = fn;
}

/** Shared application state — never mutate directly, use setState(). */
export const state = {
  schema: null,           // { [doc_type]: { block_type, schema, field_meta } }
  activeType: null,       // string doc_type key
  mediaOpen: false,       // the Media section is showing instead of a type
  documents: [],          // array of document objects for the active type
  docsTotal: 0,
  docFilter: '',          // client-side filter over the loaded document list
  activeDocId: null,      // string id, or null for "new"
  activeDoc: null,        // full document object from API, or null
  formData: {},           // current form field values (in-memory)
  isDirty: false,
  isLoadingSchema: false,
  isLoadingDocs: false,
  isLoadingDoc: false,
  isSaving: false,
  pmInstances: {},        // { fieldName: ProseMirrorView } — keyed by field name
  collabConnections: {}, // { fieldName: CollabConnection } — mirrors pmInstances
  activeChangesetId: null, // string id of the active changeset, or null
  activeChangesetTitle: null, // title of the active changeset
  activeChangesetDocCount: 0, // number of docs in the active changeset
  activeChangesetDocs: [],    // [{id, doc_type, slug, has_draft}] for tooltip
  chatPanel: null,        // ChatPanel instance once mounted
  changesetPanel: null,   // ChangesetPanel instance once mounted
  pendingView: null,      // PendingView instance once mounted
};

/**
 * Merge patch into state, then optionally trigger a full re-render.
 *
 * @param {Partial<typeof state>} patch
 * @param {boolean} [rerender=true]
 */
export function setState(patch, rerender = true) {
  Object.assign(state, patch);
  if (rerender) _render();
}
