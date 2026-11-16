/**
 * Drafts on a listing page, while editing.
 *
 * A static site is built from published documents, so a post that has never
 * been published has no card in the page for the editor to attach to. This
 * module renders those cards in the browser, from a prototype the site supplies,
 * so the draft shows up in the feed and edits like any other card.
 *
 * A site opts in with two attributes:
 *
 *   <div data-cms-list="blog_post definition"> …cards… </div>
 *   <template data-cms-draft-template="blog_post"> <article>…</article> </template>
 *   <template data-cms-draft-template="definition"> <article>…</article> </template>
 *
 * A feed may name several document types, space-separated, one template each.
 * A type with no template is simply not drawn. A template holds ONE card, built
 * from the same markup and classes as a real one so the site's own styles
 * apply. Inside it:
 *   data-cms-field="title"      the field the editor makes editable (as on real cards)
 *   data-cms-fill="title"       the element's text is set from that field
 *   data-cms-fill-format="date" | "excerpt"   how to show it (optional)
 *
 * Which drafts appear is the "lens": prod (none), all drafts, or one changeset.
 * The lens decides which not-yet-live posts are added, and which live posts are
 * shown with their unpublished edits.
 *
 * A template can only fill text. What else a card shows (slices of data, a beat
 * form, images, tags) is the site's to draw, with its own code, so the preview
 * matches what the build will make. After drawing a draft card, and for a live
 * card with unpublished edits in the lens, the editor dispatches
 * `astraeus:preview` on the card (bubbling), with `detail`:
 *   doc        the document with its draft applied, or null: show the card as built
 *   cmsBase    the CMS origin
 *   lens       the lens
 *   inLens(id) whether an unpublished change to document `id` is in the lens
 *              (so a staged link a draft post picks shows as staged)
 *   changed    ids whose content changed since the last preview, for the site's caches
 *   waitUntil(promise)  the site hands back its rendering, which the editor awaits
 * A site that does not listen gets the template's text alone.
 */

const LENS_KEY = 'astraeus:draftLens'

export const LENS_PROD = 'prod'
export const LENS_ALL = 'all'
const CHANGESET_PREFIX = 'cs:'

/** What a first visit shows. `LENS_PROD` here would start editing on live posts only. */
export const DEFAULT_LENS = LENS_ALL

const PAGE_LIMIT = 100

export const changesetLens = (id) => `${CHANGESET_PREFIX}${id}`

/** @returns {string|null} the changeset a lens names, or null for prod / all */
export function changesetOfLens(lens) {
  return lens.startsWith(CHANGESET_PREFIX) ? lens.slice(CHANGESET_PREFIX.length) : null
}

export function getLens() {
  try {
    return localStorage.getItem(LENS_KEY) || DEFAULT_LENS
  } catch {
    return DEFAULT_LENS
  }
}

export function setLens(lens) {
  try {
    localStorage.setItem(LENS_KEY, lens)
  } catch { /* private mode — the lens just won't persist */ }
}

// ────────────────────────────────────────────────────────────────────────────
// Page contract
// ────────────────────────────────────────────────────────────────────────────

/**
 * Each (list, document type) on this page that has a card template to render
 * drafts from. A list naming two types with a template each is two slots.
 */
export function findDraftSlots(root = document) {
  return findLists(root).filter((slot) => slot.template)
}

/**
 * Each (list, document type) on this page, with its card template or null. A
 * list with no template (an older page of a feed, say) gets no drawn drafts,
 * but its live cards still show their unpublished edits in the lens.
 */
export function findLists(root = document) {
  const lists = []
  for (const list of root.querySelectorAll('[data-cms-list]')) {
    for (const docType of list.dataset.cmsList.split(/\s+/).filter(Boolean)) {
      const template = root.querySelector(`template[data-cms-draft-template="${docType}"]`)
      lists.push({ list, template, docType })
    }
  }
  return lists
}

// ────────────────────────────────────────────────────────────────────────────
// Loading
// ────────────────────────────────────────────────────────────────────────────

async function getJson(url) {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`${url} → ${res.status}`)
  return res.json()
}

/** A document with its draft applied, the way the editor will load it. */
const withDraft = (cmsBase, id) => getJson(`${cmsBase}/api/documents/${id}?draft=true`)

/**
 * What a lens covers: `inLens(id)` says whether an unpublished change to a
 * document is part of it, and `changeset` is the changeset it names, if any.
 */
export async function loadLensContext(cmsBase, lens) {
  if (lens === LENS_PROD) return { lens, changeset: null, inLens: () => false }
  const changesetId = changesetOfLens(lens)
  if (!changesetId) return { lens, changeset: null, inLens: () => true }
  const changeset = await getJson(`${cmsBase}/api/changesets/${changesetId}`)
  const ids = new Set((changeset.documents ?? []).map((d) => d.id))
  return { lens, changeset, inLens: (id) => ids.has(id) }
}

/**
 * Posts of `docType` the lens asks for that are not live yet.
 *
 * The list endpoint returns the published body, so a draft that has been edited
 * is fetched again for its draft body.
 */
async function loadUnpublished(cmsBase, docType, context) {
  const { lens, changeset } = context
  if (lens === LENS_PROD) return []

  let docs
  if (changeset) {
    const ids = (changeset.documents ?? []).filter((d) => d.doc_type === docType).map((d) => d.id)
    docs = await Promise.all(ids.map((id) => withDraft(cmsBase, id)))
  } else {
    const params = new URLSearchParams({ type: docType, published: 'false', limit: PAGE_LIMIT })
    const { documents = [] } = await getJson(`${cmsBase}/api/documents?${params}`)
    docs = await Promise.all(documents.map((d) => (d.has_draft ? withDraft(cmsBase, d.id) : d)))
  }
  // A changeset also holds live posts with edits; those already have a card.
  // A post staged for deletion is on its way out, so it is not offered for editing.
  return docs.filter((d) => d.published === false && d.draft_deleted !== true)
}

/** Ids of live posts of `docType` that carry unpublished edits. */
async function loadEditedIds(cmsBase, docType) {
  const params = new URLSearchParams({ type: docType, published: 'true', has_draft: 'true', limit: PAGE_LIMIT })
  const { documents = [] } = await getJson(`${cmsBase}/api/documents?${params}`)
  return new Set(documents.map((d) => d.id))
}

// ────────────────────────────────────────────────────────────────────────────
// Rendering
// ────────────────────────────────────────────────────────────────────────────

/** Plain text of a ProseMirror doc (or a string), cut for a listing preview. */
function excerptOf(value, max = 300) {
  const parts = []
  const walk = (node) => {
    if (node == null) return
    if (typeof node.text === 'string') parts.push(node.text)
    for (const child of node.content ?? []) walk(child)
    if (['paragraph', 'heading', 'blockquote', 'list_item'].includes(node.type)) parts.push(' ')
  }
  if (typeof value === 'string') parts.push(value)
  else walk(value)
  const text = parts.join('').replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text
}

function formatDate(value) {
  const d = new Date(value)
  // UTC, like the site: dates are stored as UTC midnight and local time would
  // roll the first of a month back into the previous one.
  return Number.isNaN(d.valueOf())
    ? 'No date'
    : d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC' })
}

function fill(el, value, format) {
  if (format === 'date') {
    el.textContent = formatDate(value)
    const d = new Date(value)
    if (el.tagName === 'TIME' && !Number.isNaN(d.valueOf())) el.setAttribute('datetime', d.toISOString())
  } else if (format === 'excerpt') {
    el.textContent = excerptOf(value)
  } else {
    el.textContent = typeof value === 'string' ? value : ''
  }
}

const PILL_STYLE = {
  draft: 'background:#f5c542;color:#1a1a1a;border:1px solid #f5c542;',
  edited: 'background:transparent;color:#8a6a00;border:1px solid #f5c542;',
}

/** A small label above a card saying it is not what visitors see. Idempotent. */
function addPill(card, kind, text) {
  if (card.querySelector('[data-cms-draft-pill]')) return
  const pill = document.createElement('span')
  pill.setAttribute('data-cms-draft-pill', kind)
  pill.textContent = text
  pill.style.cssText = `
    display: table; margin-bottom: 6px; padding: 3px 9px; border-radius: 999px;
    font: 600 11px/1.2 -apple-system, system-ui, sans-serif;
    letter-spacing: 0.02em; text-transform: uppercase;
    ${PILL_STYLE[kind]}
  `
  card.prepend(pill)
}

/** Build a card for `doc` from the site's template. */
export function renderDraftCard(template, doc) {
  const card = template.content.firstElementChild?.cloneNode(true)
  if (!card) throw new Error('draft template has no card element')

  card.setAttribute('data-cms-id', doc.id)
  card.setAttribute('data-cms-type', doc.doc_type)
  card.setAttribute('data-cms-draft-card', '')

  const body = doc.body ?? {}
  for (const el of card.querySelectorAll('[data-cms-fill]')) {
    const field = el.dataset.cmsFill
    fill(el, body[field], el.dataset.cmsFillFormat)
  }
  // What names the document: a post's title, a definition's term.
  const title = card.querySelector('[data-cms-fill="title"], [data-cms-fill="term"]')
  if (title && !title.textContent) title.textContent = 'Untitled draft'

  addPill(card, 'draft', 'Draft · not live')
  return card
}

// ────────────────────────────────────────────────────────────────────────────
// Sync
// ────────────────────────────────────────────────────────────────────────────

/**
 * Bring each draft-capable list on the page in line with the lens: add cards
 * for unpublished posts it asks for, drop the ones it no longer does, and mark
 * live posts that carry unpublished edits.
 *
 * Idempotent, so it can run again when the lens changes.
 *
 * @returns {Promise<{added: Element[]}>} cards created by this call
 */
export async function syncDraftCards({ cmsBase, lens, root = document }) {
  const added = []
  const context = await loadLensContext(cmsBase, lens)
  const previews = []

  for (const { list, template, docType } of findLists(root)) {
    const [docs, editedIds] = await Promise.all([
      template ? loadUnpublished(cmsBase, docType, context) : [],
      loadEditedIds(cmsBase, docType),
    ])

    // Live posts: their unpublished edits show when the lens includes them.
    for (const card of list.querySelectorAll(`[data-cms-id][data-cms-type="${docType}"]:not([data-cms-draft-card])`)) {
      const id = card.dataset.cmsId
      if (editedIds.has(id)) addPill(card, 'edited', 'Unpublished edits')
      if (editedIds.has(id) && context.inLens(id)) {
        previews.push(withDraft(cmsBase, id).then((doc) => previewCard(card, doc, { cmsBase, context })))
      } else if (card.hasAttribute('data-cms-previewed')) {
        previews.push(previewCard(card, null, { cmsBase, context }))
      }
    }

    if (!template) continue

    // Only this type's cards: a feed shared by several types syncs each in turn.
    const wanted = new Map(docs.map((d) => [d.id, d]))
    for (const card of list.querySelectorAll('[data-cms-draft-card]')) {
      if (card.dataset.cmsType === docType && !wanted.has(card.dataset.cmsId)) card.remove()
    }

    const onPage = new Set([...root.querySelectorAll('[data-cms-id]')].map((el) => el.dataset.cmsId))
    const dateOf = (d) => new Date(d.body?.publish_date ?? 0).valueOf() || 0
    const fresh = docs
      .filter((d) => !onPage.has(d.id))
      .sort((a, b) => dateOf(a) - dateOf(b)) // oldest first: each prepend lands above the last
    for (const doc of fresh) {
      const card = renderDraftCard(template, doc)
      list.prepend(card)
      added.push(card)
    }
    // Every draft card, new or already drawn: the site draws what the template can't.
    for (const card of list.querySelectorAll('[data-cms-draft-card]')) {
      const doc = wanted.get(card.dataset.cmsId)
      if (doc && card.dataset.cmsType === docType) previews.push(previewCard(card, doc, { cmsBase, context }))
    }
  }

  await Promise.allSettled(previews)
  return { added, context }
}

/**
 * Ask the site to draw `doc` into `card` (see the header: `astraeus:preview`).
 * `doc` null puts a live card back as built. Resolves once the site has drawn it.
 *
 * @param {Element} card
 * @param {object|null} doc  the document with its draft applied
 * @param {{cmsBase: string, context: {lens: string, inLens: (id: string) => boolean}, changed?: string[]}} options
 */
export async function previewCard(card, doc, { cmsBase, context, changed = [] }) {
  if (doc) card.setAttribute('data-cms-previewed', '')
  else card.removeAttribute('data-cms-previewed')
  const pending = []
  card.dispatchEvent(new CustomEvent('astraeus:preview', {
    bubbles: true,
    detail: {
      doc,
      cmsBase,
      lens: context.lens,
      inLens: context.inLens,
      changed,
      waitUntil: (promise) => pending.push(promise),
    },
  }))
  await Promise.allSettled(pending)
}

/**
 * Refill a drawn draft card's template text from `doc`, leaving alone any field
 * being edited in place (its own editor shows the live value).
 */
export function refillDraftCard(card, doc) {
  const body = doc.body ?? {}
  for (const el of card.querySelectorAll('[data-cms-fill]')) {
    if (el.isContentEditable || el.querySelector('[contenteditable="true"], .ProseMirror')) continue
    fill(el, body[el.dataset.cmsFill], el.dataset.cmsFillFormat)
  }
}

export { withDraft as loadDraft }
