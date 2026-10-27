/**
 * components/links-input.js — Card list for a LinksField.
 *
 * Each link is a small card (URL, title, description, date added, tags)
 * instead of one hand-written JSON array. Paste a URL into the box at the
 * bottom to add a link dated today; cards move up and down and can be removed.
 *
 * Keys the card has no input for (for example `collections`, or anything a
 * script wrote) are carried through untouched. A card with no URL is kept on
 * screen but left out of the saved value until it has one, since the API
 * refuses a link without a URL.
 */

import { fetchTags } from '../api.js'
import { el } from '../utils.js'
import { buildTagsInput } from './tags-input.js'

/** @returns {string} today as YYYY-MM-DD, in local time */
export function today() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/**
 * Host name of a URL for the card header, or '' if it does not parse.
 *
 * @param {string} url
 * @returns {string}
 */
export function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return '' }
}

/**
 * The value to save: blank strings dropped, cards without a URL left out,
 * every other key kept as it was.
 *
 * @param {object[]} items
 * @returns {object[]}
 */
export function linksValue(items) {
  return items
    .map(item => Object.fromEntries(Object.entries(item).filter(([, v]) =>
      !(typeof v === 'string' && v.trim() === '') && !(Array.isArray(v) && v.length === 0) && v != null)))
    .filter(item => typeof item.url === 'string' && item.url.trim())
}

/**
 * Build the links control.
 *
 * @param {string}   name           Field name.
 * @param {object}   meta           cms:field_meta (`required_keys` marks inputs required).
 * @param {*}        currentValue   Current list of link objects.
 * @param {string}   docType        Type whose link tags to suggest.
 * @param {(name: string, value: *) => void} onFieldChange
 * @returns {HTMLElement}
 */
export function buildLinksInput(name, meta, currentValue, docType, onFieldChange) {
  let items = Array.isArray(currentValue)
    ? currentValue.filter(i => i && typeof i === 'object').map(i => ({ ...i }))
    : []
  const required = new Set(meta?.required_keys || [])
  // One request for every card's tag suggestions.
  const knownTags = docType ? fetchTags(docType, `${name}.tags`).catch(() => []) : Promise.resolve([])

  const root = el('div', { class: 'links-input', id: `field-${name}` })
  const list = el('ol', { class: 'links-input__list' })
  root.appendChild(list)

  const emit = () => onFieldChange(name, linksValue(items))

  function update(i, key, value) {
    items[i] = { ...items[i], [key]: value }
    emit()
  }

  function move(i, delta) {
    const j = i + delta
    if (j < 0 || j >= items.length) return
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]]
    items = next
    renderList()
    emit()
  }

  function remove(i) {
    items = items.filter((_, j) => j !== i)
    renderList()
    emit()
  }

  function textInput(i, key, label, { type = 'text', multiline = false, placeholder = '' } = {}) {
    const inputId = `field-${name}-${i}-${key}`
    const attrs = {
      class: multiline ? 'field-textarea links-input__desc' : 'field-input',
      id: inputId,
      placeholder,
      oninput: e => {
        update(i, key, e.target.value)
        if (key === 'url' || key === 'title') refreshHeader(i)
      },
    }
    const control = multiline ? el('textarea', { ...attrs, rows: '2' }) : el('input', { ...attrs, type, value: items[i][key] ?? '' })
    if (multiline) control.value = items[i][key] ?? ''
    if (required.has(key) || key === 'url') control.setAttribute('required', 'required')
    return el('div', { class: `links-input__cell links-input__cell--${key}` },
      el('label', { class: `links-input__label${required.has(key) || key === 'url' ? ' field-label--required' : ''}`, for: inputId }, label),
      control,
    )
  }

  function refreshHeader(i) {
    const card = list.children[i]
    if (!card) return
    const title = card.querySelector('.links-input__title')
    const host = card.querySelector('.links-input__host')
    if (title) title.textContent = items[i].title || hostOf(items[i].url) || 'New link'
    if (host) host.textContent = hostOf(items[i].url)
  }

  function buildCard(item, i) {
    const header = el('div', { class: 'links-input__header' },
      el('span', { class: 'links-input__position' }, String(i + 1)),
      el('div', { class: 'links-input__heading' },
        el('span', { class: 'links-input__title' }, item.title || hostOf(item.url) || 'New link'),
        el('span', { class: 'links-input__host' }, hostOf(item.url)),
      ),
      el('div', { class: 'links-input__controls' },
        el('button', { class: 'block-card__move', type: 'button', title: 'Move up', 'aria-label': 'Move up', disabled: i === 0 ? 'disabled' : null, onclick: () => move(i, -1) }, '↑'),
        el('button', { class: 'block-card__move', type: 'button', title: 'Move down', 'aria-label': 'Move down', disabled: i === items.length - 1 ? 'disabled' : null, onclick: () => move(i, 1) }, '↓'),
        el('button', { class: 'block-card__move links-input__remove', type: 'button', title: 'Remove link', 'aria-label': 'Remove link', onclick: () => remove(i) }, '×'),
      ),
    )
    const tagsId = `field-${name}-${i}-tags`
    return el('li', { class: 'links-input__card' },
      header,
      el('div', { class: 'links-input__grid' },
        textInput(i, 'url', 'URL', { type: 'url', placeholder: 'https://' }),
        textInput(i, 'title', 'Title'),
        textInput(i, 'description', 'Description', { multiline: true }),
        textInput(i, 'date_added', 'Date added', { type: 'date' }),
        el('div', { class: 'links-input__cell links-input__cell--tags' },
          el('label', { class: 'links-input__label', for: tagsId }, 'Tags'),
          buildTagsInput({
            id: tagsId,
            value: item.tags,
            known: knownTags,
            onChange: tags => update(i, 'tags', tags),
          }),
        ),
      ),
    )
  }

  function renderList() {
    list.innerHTML = ''
    items.forEach((item, i) => list.appendChild(buildCard(item, i)))
  }

  function addLink(url = '') {
    items = [...items, { url: url.trim(), title: '', description: '', date_added: today() }]
    renderList()
    emit()
    const focusKey = url ? 'title' : 'url'
    document.getElementById(`field-${name}-${items.length - 1}-${focusKey}`)?.focus()
  }

  const paste = el('input', {
    class: 'field-input links-input__paste',
    type: 'url',
    placeholder: 'Paste a URL and press Enter',
    'aria-label': 'Add a link by URL',
    onkeydown: e => {
      if (e.key === 'Enter' && e.target.value.trim()) {
        e.preventDefault()
        const url = e.target.value
        e.target.value = ''
        addLink(url)
      }
    },
  })
  root.appendChild(el('div', { class: 'links-input__add' },
    paste,
    el('button', { class: 'btn btn--ghost', type: 'button', onclick: () => {
      if (paste.value.trim()) { const url = paste.value; paste.value = ''; addLink(url) } else addLink()
    } }, '+ Add link'),
  ))

  renderList()
  return root
}
