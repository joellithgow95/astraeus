/**
 * api.js — Thin authenticated fetch wrapper for the CMS API.
 */

// globalThis rather than window: this module is imported transitively by
// field widgets, which are exercised in a plain node test environment where
// window does not exist. Reading a global at module load should not decide
// whether an import succeeds.
const CONFIG = globalThis.__EDITOR_CONFIG__ || { cmsBase: '', apiKey: null, mountPath: '/editor' };

/**
 * Custom error class for API errors with status + data payload.
 */
export class ApiError extends Error {
  /** @param {number} status  @param {object} data */
  constructor(status, data) {
    const msg = data?.error || data?.detail?.[0]?.msg || `HTTP ${status}`;
    super(msg);
    this.status = status;
    this.data = data;
  }
}

/**
 * Make an authenticated fetch call to the CMS API.
 *
 * @param {string} path      — e.g. "/api/schema"
 * @param {RequestInit} opts — standard fetch options
 * @returns {Promise<Response>}
 */
export async function apiFetch(path, opts = {}) {
  const url = CONFIG.cmsBase + path;
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (CONFIG.apiKey) {
    headers['Authorization'] = `Bearer ${CONFIG.apiKey}`;
  }
  // The embed runs on the published site, cross-origin to the CMS, and
  // authenticates with the cms_session cookie. Same-origin requests send
  // cookies either way, so this only changes the cross-origin case.
  return fetch(url, { credentials: 'include', ...opts, headers });
}

/** @returns {Promise<object>} */
export async function fetchSchema() {
  const res = await apiFetch('/api/schema');
  if (!res.ok) throw new Error(`Schema fetch failed: ${res.status}`);
  return res.json();
}

/**
 * @param {string} docType
 * @param {{limit?: number, offset?: number}} [options]
 * @returns {Promise<{documents: object[], total: number}>}
 */
export async function fetchDocuments(docType, { limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ type: docType, limit, offset });
  const res = await apiFetch(`/api/documents?${params}`);
  if (!res.ok) throw new Error(`Documents fetch failed: ${res.status}`);
  return res.json();
}

/**
 * Load a document for editing — its draft body if it has one.
 *
 * Without ?draft=true the API returns the published body, so reopening a
 * document with unpublished edits showed stale (or empty) fields, and typing
 * into the rich-text editor then overwrote the real draft over the socket.
 *
 * @param {string} id
 * @returns {Promise<object>}
 */
export async function fetchDocument(id) {
  const res = await apiFetch(`/api/documents/${id}?draft=true`);
  if (!res.ok) throw new Error(`Document fetch failed: ${res.status}`);
  return res.json();
}

/**
 * @param {string} docType
 * @param {object} body
 * @param {string} [slug]
 * @returns {Promise<object>}
 */
export async function createDocument(docType, body, slug = '') {
  const res = await apiFetch('/api/documents', {
    method: 'POST',
    body: JSON.stringify({ doc_type: docType, body, slug }),
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} id
 * @param {object} patch
 * @param {{activeChangesetId?: string}} [options]
 * @returns {Promise<{data: object, headers: Headers}>}
 */
export async function patchDocument(id, patch, { activeChangesetId } = {}) {
  const extraHeaders = {};
  if (activeChangesetId) {
    extraHeaders['X-Active-Changeset-Id'] = activeChangesetId;
  }
  const res = await apiFetch(`/api/documents/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
    headers: extraHeaders,
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return { data, headers: res.headers };
}

/**
 * @param {string} id
 * @returns {Promise<object>}
 */
export async function publishDocument(id) {
  const res = await apiFetch(`/api/documents/${id}/publish`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} id
 * @returns {Promise<object>}
 */
export async function unpublishDocument(id) {
  const res = await apiFetch(`/api/documents/${id}/unpublish`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} id
 * @returns {Promise<null|object>}
 */
export async function deleteDocument(id) {
  const res = await apiFetch(`/api/documents/${id}`, { method: 'DELETE' });
  if (res.status === 204) return null;
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} id
 * @returns {Promise<object>}
 */
export async function discardDraft(id) {
  const res = await apiFetch(`/api/documents/${id}/discard-draft`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} id
 * @param {boolean|null} published
 * @param {string} [activeChangesetId]
 * @returns {Promise<{data: object, headers: Headers}>}
 */
export async function setDraftPublishState(id, published, activeChangesetId) {
  const extraHeaders = {};
  if (activeChangesetId) {
    extraHeaders['X-Active-Changeset-Id'] = activeChangesetId;
  }
  const res = await apiFetch(`/api/documents/${id}/draft-publish-state`, {
    method: 'POST',
    body: JSON.stringify({ published }),
    headers: extraHeaders,
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return { data, headers: res.headers };
}

/**
 * @param {string} id
 * @param {boolean|null} deleted
 * @param {string} [activeChangesetId]
 * @returns {Promise<{data: object, headers: Headers}>}
 */
export async function setDraftDeleted(id, deleted, activeChangesetId) {
  const extraHeaders = {};
  if (activeChangesetId) {
    extraHeaders['X-Active-Changeset-Id'] = activeChangesetId;
  }
  const res = await apiFetch(`/api/documents/${id}/draft-delete`, {
    method: 'POST',
    body: JSON.stringify({ deleted }),
    headers: extraHeaders,
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return { data, headers: res.headers };
}

// ── Changeset API helpers ──────────────────────────────────────────────────

/** @returns {Promise<{documents: object[]}>} */
export async function fetchDirtyDocs() {
  const res = await apiFetch('/api/documents?has_draft=true&exclude_types=chat_session,chat_message');
  if (!res.ok) throw new ApiError(res.status, await res.json());
  return res.json();
}

/** @returns {Promise<{documents: object[]}>} */
export async function fetchUnpublishedDocs() {
  const res = await apiFetch('/api/documents?published=false&exclude_types=chat_session,chat_message');
  if (!res.ok) throw new ApiError(res.status, await res.json());
  return res.json();
}

/** @returns {Promise<{changesets: object[]}>} */
export async function fetchOpenChangesets() {
  const res = await apiFetch('/api/changesets?status=open&include_documents=true');
  if (!res.ok) throw new ApiError(res.status, await res.json());
  return res.json();
}

/**
 * @param {string} title
 * @returns {Promise<object>}
 */
export async function createChangeset(title) {
  const res = await apiFetch('/api/changesets', {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} changesetId
 * @returns {Promise<object>}
 */
export async function fetchChangeset(changesetId) {
  const res = await apiFetch(`/api/changesets/${changesetId}`);
  if (!res.ok) throw new ApiError(res.status, await res.json());
  return res.json();
}

/**
 * @param {string} changesetId
 * @param {string} docId
 * @returns {Promise<null>}
 */
export async function addDocToChangeset(changesetId, docId) {
  const res = await apiFetch(`/api/changesets/${changesetId}/documents/${docId}`, {
    method: 'POST',
  });
  if (res.status === 204 || res.status === 201) return null;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data);
  }
  return null;
}

/**
 * @param {string} changesetId
 * @param {string} docId
 * @returns {Promise<null>}
 */
export async function removeDocFromChangeset(changesetId, docId) {
  const res = await apiFetch(`/api/changesets/${changesetId}/documents/${docId}`, {
    method: 'DELETE',
  });
  if (res.status === 204) return null;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data);
  }
  return null;
}

/**
 * @param {string} changesetId
 * @returns {Promise<object>}
 */
export async function publishChangeset(changesetId) {
  const res = await apiFetch(`/api/changesets/${changesetId}/publish`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} changesetId
 * @param {string} publishAt — ISO 8601 datetime
 * @returns {Promise<object>}
 */
export async function scheduleChangeset(changesetId, publishAt) {
  const res = await apiFetch(`/api/changesets/${changesetId}/schedule`, {
    method: 'POST',
    body: JSON.stringify({ publish_at: publishAt }),
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} changesetId
 * @param {{title?: string, status?: string}} fields
 * @returns {Promise<object>}
 */
export async function patchChangeset(changesetId, fields) {
  const res = await apiFetch(`/api/changesets/${changesetId}`, {
    method: 'PATCH',
    body: JSON.stringify(fields),
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} changesetId
 * @returns {Promise<object>}
 */
export async function fetchChangesetDiff(changesetId) {
  const res = await apiFetch(`/api/changesets/${changesetId}/diff`);
  if (!res.ok) throw new ApiError(res.status, await res.json());
  return res.json();
}

/**
 * @param {string} changesetId
 * @param {string} [docId] — if provided, revert only this doc
 * @returns {Promise<object>}
 */
export async function revertChangeset(changesetId, docId) {
  const path = docId
    ? `/api/changesets/${changesetId}/revert/${docId}`
    : `/api/changesets/${changesetId}/revert`;
  const res = await apiFetch(path, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/**
 * @param {string} changesetId
 * @returns {Promise<null>}
 */
export async function deleteChangeset(changesetId) {
  const res = await apiFetch(`/api/changesets/${changesetId}`, { method: 'DELETE' });
  if (res.status === 204) return null;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data);
  }
  return null;
}

/**
 * Tags already used on a document type, most used first.
 *
 * @param {string} docType
 * @param {string} [field]  — field name, or a dotted path such as `links.tags`
 * @returns {Promise<{tag: string, count: number}[]>}
 */
export async function fetchTags(docType, field = 'tags') {
  const params = new URLSearchParams({ type: docType, field });
  const res = await apiFetch(`/api/tags?${params}`);
  if (!res.ok) throw new Error(`Tags fetch failed: ${res.status}`);
  return (await res.json()).tags || [];
}
