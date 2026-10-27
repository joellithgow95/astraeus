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
 */

import { fetchDocumentsByIds, searchDocuments } from '../api.js'
import { docTitle, el } from '../utils.js'

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
  root.appendChild(el('div', { class: 'ref-list__add' }, input, menu))

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

  function refreshMenu() {
    const options = results.filter(d => !ids.includes(d.id))
    menu.innerHTML = ''
    if (document.activeElement !== input || !targetType) { closeMenu(); return }
    if (!options.length) {
      menu.appendChild(el('li', { class: 'tags-input__option ref-list__none' }, input.value.trim() ? 'No matches' : 'Nothing left to add'))
    }
    if (active >= options.length) active = options.length - 1
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
    menu.removeAttribute('hidden')
    input.setAttribute('aria-expanded', 'true')
    return options
  }

  function pick(doc) {
    add(doc)
    input.value = ''
    search()
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
      const doc = options[active >= 0 ? active : 0]
      if (doc) pick(doc)
    } else if (e.key === 'ArrowDown' && options.length) {
      e.preventDefault()
      active = (active + 1) % options.length
      refreshMenu()
    } else if (e.key === 'ArrowUp' && options.length) {
      e.preventDefault()
      active = active <= 0 ? options.length - 1 : active - 1
      refreshMenu()
    } else if (e.key === 'Escape') {
      closeMenu()
    }
  })
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
