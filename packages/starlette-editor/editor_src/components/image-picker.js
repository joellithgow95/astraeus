/**
 * components/image-picker.js — Image fields, picked from the media library.
 *
 * Two shapes of field use it:
 *
 *   - an ImageField, whose value is a URL string;
 *   - a JSONField whose schema has `src` and `alt` (a post's card image),
 *     whose value is an object. Picking sets both, and keeps any other keys
 *     (type, poster, link) as they were.
 *
 * The picker is the shell's own (media/media-picker.js), not a page in an
 * iframe. Without a media library the field is a plain URL input.
 */

import { el } from '../utils.js'
import { icon } from '../icons.js'
import { embedUrl, hasMedia, iiifUrl, keyFromUrl } from '../media/media-api.js'
import { openMediaPicker } from '../media/media-picker.js'

/**
 * A small preview for *src*: the library's 800-wide rendition when the URL is
 * one of ours (already cut, so it loads at once), else the URL itself.
 *
 * @param {string} src
 * @returns {string}
 */
export function previewSrc(src) {
  const key = keyFromUrl(src)
  return key ? iiifUrl(key) : src
}

function buildPreview(src, onPick) {
  const preview = el('button', {
    type: 'button',
    class: `image-picker__preview${src ? '' : ' is-empty'}`,
    title: hasMedia() ? 'Choose from the media library' : '',
    'aria-label': src ? 'Change image' : 'Choose an image',
    onclick: hasMedia() ? onPick : null,
    disabled: hasMedia() ? null : 'disabled',
  })
  if (src) preview.appendChild(el('img', { src: previewSrc(src), alt: '', decoding: 'async' }))
  else preview.appendChild(icon('image', { size: 24 }))
  return preview
}

/**
 * The widget for an ImageField (a URL string).
 *
 * @param {string} fieldName
 * @param {string} currentValue
 * @param {(name: string, value: string) => void} onFieldChange
 * @returns {HTMLElement}
 */
export function buildImagePickerField(fieldName, currentValue, onFieldChange) {
  let value = currentValue || ''
  const wrap = el('div', { class: 'image-picker' })

  const input = el('input', {
    class: 'field-input',
    id: `field-${fieldName}`,
    type: 'url',
    placeholder: 'https://…',
    value,
    oninput: e => set(e.target.value, { fromInput: true }),
  })

  function set(next, { fromInput = false } = {}) {
    value = next
    if (!fromInput) input.value = next
    onFieldChange(fieldName, next)
    draw()
  }

  async function pick() {
    const asset = await openMediaPicker()
    if (asset) set(embedUrl(asset.key))
  }

  // The input stays put while the preview and buttons are redrawn, so typing
  // in it keeps focus.
  const previewSlot = el('div', { class: 'image-picker__preview-slot' })
  const actionsSlot = el('div', { class: 'image-picker__actions-slot' })
  wrap.append(previewSlot, el('div', { class: 'image-picker__fields' }, input, actionsSlot))

  function draw() {
    previewSlot.replaceChildren(buildPreview(value, pick))
    actionsSlot.replaceChildren(buildButtons(value, pick, () => set('')))
  }

  draw()
  return wrap
}

function buildButtons(value, pick, clear) {
  return el('div', { class: 'image-picker__actions' },
    hasMedia()
      ? el('button', { type: 'button', class: 'btn btn--ghost btn--sm', onclick: pick },
          value ? 'Change image' : 'Choose image')
      : null,
    value ? el('button', { type: 'button', class: 'btn btn--ghost btn--sm', onclick: clear }, 'Remove') : null,
  )
}

/**
 * True when a JSONField's schema hint describes an image object ({src, alt}).
 *
 * @param {object} meta — the field's cms:field_meta entry
 * @returns {boolean}
 */
export function isImageObjectMeta(meta) {
  const props = meta?.schema?.properties
  return Boolean(props && props.src && props.alt)
}

/**
 * The widget for an image object ({src, alt, …}).
 *
 * Picking an image sets src, and fills alt from the library's alt text when
 * the field's alt is empty or was the old image's. Clearing src and alt sets
 * the field to null, which an optional field wants rather than {}.
 *
 * @param {string} fieldName
 * @param {object|null} currentValue
 * @param {(name: string, value: object|null) => void} onFieldChange
 * @returns {HTMLElement}
 */
export function buildImageObjectField(fieldName, currentValue, onFieldChange) {
  let value = currentValue && typeof currentValue === 'object' ? { ...currentValue } : {}
  let altFromLibrary = null
  const wrap = el('div', { class: 'image-picker image-picker--object' })

  const srcInput = el('input', {
    class: 'field-input',
    id: `field-${fieldName}`,
    type: 'url',
    placeholder: 'Image URL',
    'aria-label': 'Image URL',
    value: value.src || '',
    oninput: e => update({ src: e.target.value }, 'src'),
  })
  const altInput = el('input', {
    class: 'field-input',
    id: `field-${fieldName}-alt`,
    type: 'text',
    placeholder: 'Alt text: what the image shows',
    'aria-label': 'Alt text',
    value: value.alt || '',
    oninput: e => { altFromLibrary = null; update({ alt: e.target.value }, 'alt') },
  })

  function emit() {
    const empty = !value.src && !value.alt && Object.keys(value).every(k => k === 'src' || k === 'alt')
    onFieldChange(fieldName, empty ? null : { ...value })
  }

  function update(patch, typedIn) {
    value = { ...value, ...patch }
    if (typedIn !== 'src') srcInput.value = value.src || ''
    if (typedIn !== 'alt') altInput.value = value.alt || ''
    emit()
    draw()
  }

  async function pick() {
    const asset = await openMediaPicker()
    if (!asset) return
    const patch = { src: embedUrl(asset.key) }
    // Keep alt someone wrote; replace one that came from the previous pick.
    if (!value.alt || value.alt === altFromLibrary) {
      patch.alt = asset.alt_text || ''
      altFromLibrary = patch.alt
    }
    update(patch)
  }

  function clear() {
    const { src: _src, alt: _alt, ...rest } = value
    value = rest
    altFromLibrary = null
    update({})
  }

  const previewSlot = el('div', { class: 'image-picker__preview-slot' })
  const actionsSlot = el('div', { class: 'image-picker__actions-slot' })
  wrap.append(previewSlot, el('div', { class: 'image-picker__fields' }, srcInput, altInput, actionsSlot))

  function draw() {
    previewSlot.replaceChildren(buildPreview(value.src, pick))
    actionsSlot.replaceChildren(buildButtons(value.src, pick, clear))
  }

  draw()
  return wrap
}
