/**
 * components/tags-input.js — Chip input for a TagsField.
 *
 * Tags used to be a JSONField, edited as a raw `["ai", "tools"]` textarea:
 * a missing quote failed silently, and nothing told you a tag already existed
 * under another spelling. Here each tag is a chip, typing offers the tags
 * already used on the document type (from `GET /api/tags`), and what you type
 * is lowercased and hyphenated, so `Claude Code` becomes `claude-code`.
 *
 * Tags already stored are shown exactly as they are; only new input is
 * normalised.
 */

import { fetchTags } from '../api.js'
import { el } from '../utils.js'

const MAX_SUGGESTIONS = 8

/**
 * Lowercase, hyphenate and trim a typed tag. Returns '' for nothing usable.
 *
 * @param {string} raw
 * @returns {string}
 */
export function normalizeTag(raw) {
  return String(raw ?? '')
    .toLowerCase()
    .trim()
    .replace(/[\s_]+/g, '-')
    .replace(/[^\p{L}\p{N}.-]+/gu, '')
    .replace(/-{2,}/g, '-')
    .replace(/^[-.]+|[-.]+$/g, '')
}

/**
 * Rank known tags against what has been typed: prefix matches first, then
 * matches anywhere, each by use count. Tags already chosen are left out.
 *
 * @param {{tag: string, count: number}[]} known
 * @param {string} query     — normalised input
 * @param {string[]} chosen
 * @returns {{tag: string, count: number}[]}
 */
export function rankSuggestions(known, query, chosen) {
  const taken = new Set(chosen)
  const pool = known.filter(k => !taken.has(k.tag))
  if (!query) return pool.slice(0, MAX_SUGGESTIONS)
  const prefix = pool.filter(k => k.tag.startsWith(query))
  const inside = pool.filter(k => !k.tag.startsWith(query) && k.tag.includes(query))
  return [...prefix, ...inside].slice(0, MAX_SUGGESTIONS)
}

/**
 * Build the chip input.
 *
 * @param {object}   opts
 * @param {string}   opts.id          — id for the text input (label target)
 * @param {string[]} opts.value       — current tags
 * @param {(tags: string[]) => void} opts.onChange
 * @param {string}  [opts.docType]    — type whose tags to suggest
 * @param {string}  [opts.field]      — field (or dotted path) to read suggestions from
 * @param {string}  [opts.placeholder]
 * @param {Promise<{tag: string, count: number}[]>} [opts.known] — suggestions, if already fetched
 * @param {number}  [opts.max]        — most tags allowed; the text box hides once it is reached
 * @returns {HTMLElement}
 */
export function buildTagsInput({ id, value, onChange, docType, field = 'tags', placeholder, known, max = Infinity }) {
  let tags = Array.isArray(value) ? value.filter(t => typeof t === 'string') : []
  let menuItems = []
  let active = -1
  let knownTags = []

  const root = el('div', { class: 'tags-input' })
  const chips = el('div', { class: 'tags-input__chips' })
  const input = el('input', {
    class: 'tags-input__text',
    id,
    type: 'text',
    autocomplete: 'off',
    autocapitalize: 'none',
    spellcheck: 'false',
    role: 'combobox',
    'aria-autocomplete': 'list',
    'aria-expanded': 'false',
    placeholder: placeholder ?? 'Add a tag…',
  })
  const list = el('ul', { class: 'tags-input__menu', role: 'listbox', hidden: 'hidden' })
  const box = el('div', { class: 'tags-input__box', onclick: () => input.focus() }, chips, input)
  root.appendChild(box)
  root.appendChild(list)

  const emit = () => onChange([...tags])

  function renderChips() {
    chips.innerHTML = ''
    input.hidden = tags.length >= max
    tags.forEach((tag, i) => {
      chips.appendChild(el('span', { class: 'tag-chip' },
        el('span', { class: 'tag-chip__label' }, tag),
        el('button', {
          class: 'tag-chip__remove',
          type: 'button',
          'aria-label': `Remove ${tag}`,
          onclick: e => {
            e.stopPropagation()
            removeAt(i)
            if (max !== Infinity) input.focus()  // single: the box is back, ready for the replacement
          },
        }, '×'),
      ))
    })
  }

  function add(raw) {
    const tag = normalizeTag(raw)
    input.value = ''
    if (!tag || tags.includes(tag) || tags.length >= max) { refreshMenu(); return }
    tags = [...tags, tag]
    renderChips()
    emit()
    refreshMenu()
  }

  function removeAt(i) {
    tags = tags.filter((_, j) => j !== i)
    renderChips()
    emit()
    refreshMenu()
  }

  function refreshMenu() {
    const query = normalizeTag(input.value)
    const suggestions = rankSuggestions(knownTags, query, tags)
    const isNew = query && !tags.includes(query) && !knownTags.some(k => k.tag === query)
    menuItems = suggestions.map(s => ({ tag: s.tag, note: String(s.count) }))
    if (isNew) menuItems.push({ tag: query, note: 'new' })
    list.innerHTML = ''
    if (document.activeElement !== input || !menuItems.length) {
      list.setAttribute('hidden', 'hidden')
      input.setAttribute('aria-expanded', 'false')
      active = -1
      return
    }
    if (active >= menuItems.length) active = menuItems.length - 1
    menuItems.forEach((item, i) => {
      list.appendChild(el('li', {
        class: `tags-input__option${i === active ? ' is-active' : ''}${item.note === 'new' ? ' is-new' : ''}`,
        role: 'option',
        'aria-selected': i === active ? 'true' : 'false',
        // mousedown, not click: click lands after the input's blur closed the menu
        onmousedown: e => { e.preventDefault(); add(item.tag) },
      },
        el('span', { class: 'tags-input__option-tag' }, item.note === 'new' ? `Add “${item.tag}”` : item.tag),
        el('span', { class: 'tags-input__option-note' }, item.note),
      ))
    })
    list.removeAttribute('hidden')
    input.setAttribute('aria-expanded', 'true')
  }

  input.addEventListener('keydown', e => {
    const open = !list.hasAttribute('hidden')
    const options = open ? menuItems : []
    if (e.key === 'Enter' || e.key === ',' || (e.key === 'Tab' && input.value.trim())) {
      e.preventDefault()
      const picked = active >= 0 ? options[active] : null
      active = -1
      add(picked ? picked.tag : input.value)
    } else if (e.key === 'Backspace' && !input.value && tags.length) {
      removeAt(tags.length - 1)
    } else if (e.key === 'ArrowDown' && options.length) {
      e.preventDefault()
      active = (active + 1) % options.length
      refreshMenu()
    } else if (e.key === 'ArrowUp' && options.length) {
      e.preventDefault()
      active = active <= 0 ? options.length - 1 : active - 1
      refreshMenu()
    } else if (e.key === 'Escape') {
      list.setAttribute('hidden', 'hidden')
    }
  })
  input.addEventListener('input', () => {
    // A pasted "a, b, c" becomes three chips.
    if (input.value.includes(',')) {
      const parts = input.value.split(',')
      const rest = parts.pop()
      parts.forEach(p => add(p))
      input.value = rest
    }
    active = -1
    refreshMenu()
  })
  input.addEventListener('focus', refreshMenu)
  input.addEventListener('blur', () => {
    // Typed and walked away: keep it rather than lose it.
    if (input.value.trim()) add(input.value)
    list.setAttribute('hidden', 'hidden')
    input.setAttribute('aria-expanded', 'false')
  })

  const source = known ?? (docType ? fetchTags(docType, field) : Promise.resolve([]))
  source.then(k => { knownTags = k; refreshMenu() }).catch(() => { /* chips still work without suggestions */ })

  renderChips()
  return root
}

/**
 * Build a chip input that holds one tag, for a TagField: the value is a
 * string (`''` when empty), and suggestions come from the type named in
 * `meta.suggest_from`.
 *
 * @param {object} opts
 * @param {string} opts.id
 * @param {string} opts.value
 * @param {(tag: string) => void} opts.onChange
 * @param {object} [opts.meta]          — cms:field_meta (`suggest_from`, `suggest_field`, `placeholder`)
 * @returns {HTMLElement}
 */
export function buildTagInput({ id, value, onChange, meta = {} }) {
  const root = buildTagsInput({
    id,
    value: typeof value === 'string' && value ? [value] : [],
    onChange: tags => onChange(tags[0] ?? ''),
    docType: meta.suggest_from,
    field: meta.suggest_field || 'tags',
    placeholder: meta.placeholder ? `e.g. ${meta.placeholder}` : 'Pick a tag…',
    max: 1,
  })
  root.classList.add('tags-input--single')
  return root
}
