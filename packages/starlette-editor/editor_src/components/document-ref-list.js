/**
 * components/document-ref-list.js — Pick an ordered list of documents.
 *
 * A `ListField(DocumentRef(block_type=...))` stores a list of document ids.
 * Without this it fell through to the JSON textarea, so adding a song to a
 * post meant finding its id by hand. Here each chosen document is a row
 * (title, slug, move up/down, remove), and the box below searches the target
 * type by title (`GET /api/documents?q=`) to add more.
 *
 * The rows' titles come from one `?ids=` request. An id whose document is
 * gone stays in the list, marked missing, so it is not dropped silently; the
 * CMS refuses to save it, which says what is wrong. An untyped ref list has
 * nothing to search, so it takes pasted ids.
 *
 * When the target type has a URL field (a `link`), pasting a URL that no
 * document holds yet offers to create one: a small form with the URL, a
 * title made from it, today's date and any other required field, saved as a
 * draft in the changeset you are working in (else the default one), so it
 * publishes with the post that picked it.
 */

import { addDocToChangeset, createDocument, fetchDocumentsByIds, searchDocuments } from '../api.js'
import { state } from '../state.js'
import { docTitle, el, humanizeFieldName } from '../utils.js'

const SEARCH_DELAY_MS = 200

/**
 * Move the item at `i` by `delta`, returning a new list (unchanged if out of range).
 *
 * @param {string[]} list
 * @param {number} i
 * @param {number} delta
 * @returns {string[]}
 */
export function moveItem(list, i, delta) {
  const j = i + delta
  if (j < 0 || j >= list.length) return list
  const next = [...list];
  [next[i], next[j]] = [next[j], next[i]]
  return next
}

const URL_RE = /^https?:\/\/\S+\.\S+$/i

/** Today as `YYYY-MM-DD`, in local time (toISOString would give UTC's date). */
export function today(now = new Date()) {
  const p = n => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`
}

/**
 * A readable title from a URL: host without `www.`, then the path's last
 * segment with dashes as spaces. `https://www.example.com/blog/my-post/`
 * becomes "example.com — my post".
 *
 * @param {string} url
 * @returns {string}
 */
export function titleFromUrl(url) {
  try {
    const u = new URL(url)
    const host = u.hostname.replace(/^www\./, '')
    const last = decodeURIComponent(u.pathname.split('/').filter(Boolean).pop() || '')
      .replace(/\.[a-z0-9]+$/i, '')
      .replace(/[-_]+/g, ' ')
      .trim()
    return last ? `${host} — ${last}` : host
  } catch {
    return url
  }
}

/** A URL-safe slug from a title: "example.com — my post" → "example-com-my-post". */
export function slugify(text) {
  return String(text ?? '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80)
    .replace(/-+$/, '')
}

/**
 * How to create a document of the target type from a URL, or null when it
 * cannot be done from a short form: the type has no URL field, or requires a
 * field that is not plain text or a date.
 *
 * @param {object} typeSchema  — JSON Schema of the target document type
 * @returns {{urlField: string, fields: {name: string, label: string, kind: 'url'|'date'|'text'}[]}|null}
 */
export function urlCreatePlan(typeSchema) {
  const props = typeSchema?.properties || {}
  const required = typeSchema?.required || []
  const metaOf = name => props[name]?.['cms:field_meta'] || {}
  const urlField = Object.keys(props).find(k => metaOf(k).format === 'url')
  if (!urlField) return null
  const isText = prop => prop.type === 'string' || (prop.anyOf || []).some(v => v.type === 'string')
  const fields = []
  for (const [name, prop] of Object.entries(props)) {
    if (name === 'block_type') continue
    if (name !== urlField && name !== 'title' && !required.includes(name)) continue
    const meta = metaOf(name)
    const kind = name === urlField ? 'url'
      : (meta.field_type === 'date' || /(^|_)date($|_)/.test(name)) ? 'date'
        : 'text'
    if (kind === 'text' && (!isText(prop) || ['tags', 'tag', 'links', 'json', 'rich_text'].includes(meta.field_type) || prop.enum)) {
      if (required.includes(name)) return null
      continue
    }
    fields.push({ name, label: meta.label || humanizeFieldName(name), kind })
  }
  return { urlField, fields }
}

/**
 * Build the control for a document_ref_list field.
 *
 * @param {string}   name          Field name.
 * @param {object}   meta          cms:field_meta (`ref_block_type`, `ref_label_field`).
 * @param {*}        currentValue  Current list of ids.
 * @param {(name: string, value: string[]) => void} onFieldChange
 * @param {{inputId?: string}} [opts]
 * @returns {HTMLElement}
 */
export function buildDocumentRefList(name, meta, currentValue, onFieldChange, { inputId } = {}) {
  const targetType = meta?.ref_block_type
  const labelField = meta?.ref_label_field
  let ids = Array.isArray(currentValue) ? currentValue.filter(v => typeof v === 'string') : []
  const known = new Map()   // id → document, once fetched
  let loaded = false        // the ?ids= lookup has answered
  let lookupFailed = false  // ...with an error, so absent ids are unknown, not missing
  let results = []
  let active = -1
  let timer = null
  let seq = 0
  const plan = targetType ? urlCreatePlan(state.schema?.[targetType]?.schema) : null

  const root = el('div', { class: 'ref-list', id: `field-${name}` })
  const list = el('ol', { class: 'ref-list__list' })
  const empty = el('p', { class: 'ref-list__empty' }, targetType ? `No ${targetType} documents chosen yet.` : 'No documents chosen yet.')
  const input = el('input', {
    class: 'field-input ref-list__search',
    id: inputId ?? `field-${name}-search`,
    type: 'text',
    autocomplete: 'off',
    role: targetType ? 'combobox' : null,
    'aria-autocomplete': targetType ? 'list' : null,
    'aria-expanded': 'false',
    placeholder: targetType ? `Search ${targetType} to add…` : 'Paste a document ID and press Enter',
  })
  const menu = el('ul', { class: 'tags-input__menu ref-list__menu', role: 'listbox', hidden: 'hidden' })
  root.appendChild(list)
  root.appendChild(empty)
  const createBox = el('div', { class: 'ref-list__create', hidden: 'hidden' })
  root.appendChild(el('div', { class: 'ref-list__add' }, input, menu))
  root.appendChild(createBox)

  const emit = () => onFieldChange(name, [...ids])

  function label(id) {
    const doc = known.get(id)
    if (doc) return docTitle(doc, labelField)
    return loaded ? id : '…'
  }

  function renderRows() {
    list.innerHTML = ''
    empty.hidden = ids.length > 0
    ids.forEach((id, i) => {
      const doc = known.get(id)
      const missing = loaded && !lookupFailed && !doc
      list.appendChild(el('li', { class: `ref-list__row${missing ? ' is-missing' : ''}` },
        el('span', { class: 'ref-list__position' }, String(i + 1)),
        el('div', { class: 'ref-list__heading' },
          el('span', { class: 'ref-list__title' }, label(id)),
          el('span', { class: 'ref-list__sub' }, missing ? 'missing' : (doc?.slug || '')),
        ),
        el('div', { class: 'ref-list__controls' },
          el('button', { class: 'block-card__move', type: 'button', title: 'Move up', 'aria-label': `Move ${label(id)} up`, disabled: i === 0 ? 'disabled' : null, onclick: () => { ids = moveItem(ids, i, -1); renderRows(); emit() } }, '↑'),
          el('button', { class: 'block-card__move', type: 'button', title: 'Move down', 'aria-label': `Move ${label(id)} down`, disabled: i === ids.length - 1 ? 'disabled' : null, onclick: () => { ids = moveItem(ids, i, 1); renderRows(); emit() } }, '↓'),
          el('button', { class: 'block-card__move ref-list__remove', type: 'button', title: 'Remove', 'aria-label': `Remove ${label(id)}`, onclick: () => remove(i) }, '×'),
        ),
      ))
    })
  }

  function add(doc) {
    const id = typeof doc === 'string' ? doc.trim() : doc?.id
    if (!id || ids.includes(id)) return
    if (typeof doc === 'object') known.set(id, doc)
    ids = [...ids, id]
    renderRows()
    emit()
  }

  function remove(i) {
    ids = ids.filter((_, j) => j !== i)
    renderRows()
    emit()
    refreshMenu()
  }

  function closeMenu() {
    menu.setAttribute('hidden', 'hidden')
    input.setAttribute('aria-expanded', 'false')
    active = -1
  }

  /** The pasted URL, when no result already holds it and the type can be made from one. */
  function creatableUrl() {
    const q = input.value.trim()
    if (!plan || !URL_RE.test(q)) return null
    const held = results.some(d => d.body?.[plan.urlField] === q)
    return held ? null : q
  }

  function refreshMenu() {
    const options = results.filter(d => !ids.includes(d.id))
    menu.innerHTML = ''
    if (document.activeElement !== input || !targetType) { closeMenu(); return }
    const url = creatableUrl()
    if (!options.length && !url) {
      menu.appendChild(el('li', { class: 'tags-input__option ref-list__none' }, input.value.trim() ? 'No matches' : 'Nothing left to add'))
    }
    const total = options.length + (url ? 1 : 0)
    if (active >= total) active = total - 1
    options.forEach((doc, i) => {
      menu.appendChild(el('li', {
        class: `tags-input__option${i === active ? ' is-active' : ''}`,
        role: 'option',
        'aria-selected': i === active ? 'true' : 'false',
        // mousedown, not click: click lands after the input's blur closed the menu
        onmousedown: e => { e.preventDefault(); pick(doc) },
      },
        el('span', { class: 'tags-input__option-tag' }, docTitle(doc, labelField)),
        el('span', { class: 'tags-input__option-note' }, doc.slug || ''),
      ))
    })
    if (url) {
      menu.appendChild(el('li', {
        class: `tags-input__option is-new${active === options.length ? ' is-active' : ''}`,
        role: 'option',
        onmousedown: e => { e.preventDefault(); openCreate(url) },
      },
        el('span', { class: 'tags-input__option-tag' }, `Create ${targetType} for this URL`),
        el('span', { class: 'tags-input__option-note' }, 'new'),
      ))
    }
    menu.removeAttribute('hidden')
    input.setAttribute('aria-expanded', 'true')
    return options
  }

  function pick(doc) {
    add(doc)
    input.value = ''
    search()
  }

  function closeCreate() {
    createBox.innerHTML = ''
    createBox.setAttribute('hidden', 'hidden')
  }

  /** Show the form that creates a `targetType` document from `url`, prefilled. */
  function openCreate(url) {
    closeMenu()
    const values = {}
    for (const f of plan.fields) {
      values[f.name] = f.kind === 'url' ? url : f.kind === 'date' ? today() : f.name === 'title' ? titleFromUrl(url) : ''
    }
    const error = el('p', { class: 'ref-list__create-error', role: 'alert', hidden: 'hidden' })
    const submit = el('button', { class: 'btn btn--primary', type: 'button', onclick: () => save() }, `Create and add`)
    const fieldsEl = plan.fields.map(f => {
      const id = `${input.id}-new-${f.name}`
      return el('div', { class: 'field-group field-group--nested' },
        el('label', { class: 'field-label', for: id }, f.label),
        el('input', {
          class: 'field-input',
          id,
          type: f.kind === 'date' ? 'date' : f.kind === 'url' ? 'url' : 'text',
          value: values[f.name],
          oninput: e => { values[f.name] = e.target.value },
          onkeydown: e => { if (e.key === 'Enter') { e.preventDefault(); save() } },
        }),
      )
    })

    async function save() {
      submit.disabled = true
      error.setAttribute('hidden', 'hidden')
      const body = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ''))
      try {
        const doc = await createDocument(targetType, body, slugify(body.title || body[plan.urlField]))
        if (state.activeChangesetId) {
          try { await addDocToChangeset(state.activeChangesetId, doc.id) } catch { /* it still exists; the default changeset holds it */ }
        }
        add(doc)
        input.value = ''
        closeCreate()
        input.focus()
      } catch (err) {
        error.textContent = err?.message || 'Could not create it'
        error.removeAttribute('hidden')
        submit.disabled = false
      }
    }

    createBox.innerHTML = ''
    createBox.appendChild(el('p', { class: 'ref-list__create-title' }, `New ${targetType}, saved as a draft in the changeset your edits go to`))
    fieldsEl.forEach(f => createBox.appendChild(f))
    createBox.appendChild(error)
    createBox.appendChild(el('div', { class: 'ref-list__create-actions' },
      submit,
      el('button', { class: 'btn btn--ghost', type: 'button', onclick: () => { closeCreate(); input.focus() } }, 'Cancel'),
    ))
    createBox.removeAttribute('hidden')
    createBox.querySelector('input:not([type=url])')?.focus()
  }

  function search() {
    if (!targetType) return
    clearTimeout(timer)
    const q = input.value.trim()
    const mine = ++seq
    timer = setTimeout(() => {
      searchDocuments(targetType, q, { limit: 8 + ids.length })
        .then(docs => { if (mine === seq) { results = docs; refreshMenu() } })
        .catch(() => { /* rows still work; only the search is down */ })
    }, q ? SEARCH_DELAY_MS : 0)
  }

  input.addEventListener('keydown', e => {
    const options = menu.hasAttribute('hidden') ? [] : results.filter(d => !ids.includes(d.id))
    if (e.key === 'Enter') {
      e.preventDefault()
      if (!targetType) { add(input.value); input.value = ''; return }
      const url = creatableUrl()
      const doc = options[active >= 0 ? active : 0]
      if (url && (!doc || active === options.length)) openCreate(url)
      else if (doc) pick(doc)
    } else if (e.key === 'ArrowDown' && count(options)) {
      e.preventDefault()
      active = (active + 1) % count(options)
      refreshMenu()
    } else if (e.key === 'ArrowUp' && count(options)) {
      e.preventDefault()
      active = active <= 0 ? count(options) - 1 : active - 1
      refreshMenu()
    } else if (e.key === 'Escape') {
      closeMenu()
    }
  })
  // The create option, when shown, comes after the matches.
  const count = options => options.length + (creatableUrl() ? 1 : 0)
  input.addEventListener('input', () => { active = -1; search() })
  input.addEventListener('focus', search)
  input.addEventListener('blur', closeMenu)

  renderRows()
  fetchDocumentsByIds(ids)
    .then(docs => { for (const d of docs) known.set(d.id, d) })
    .catch(() => { lookupFailed = true })  // titles unavailable: rows show ids
    .finally(() => { loaded = true; renderRows() })

  return root
}
