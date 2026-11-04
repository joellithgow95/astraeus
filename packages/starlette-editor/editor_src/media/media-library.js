/**
 * media/media-library.js — The media library, built from the shell's own parts.
 *
 * One set of pieces serves two places: the Media section of the shell (a grid
 * in the document column, the chosen asset in the main pane) and the picker an
 * image field opens (media-picker.js). Both read one shared list, so a file
 * uploaded from the picker is in the section too, and the reverse.
 *
 * Media is not a document: an asset has no draft, changeset or collab session.
 * Alt text and tags save on their own when they change, like a document's
 * plain fields, and nothing here goes through the CMS.
 */

import { el } from '../utils.js'
import { icon } from '../icons.js'
import { showToast } from '../components/toast.js'
import { showConfirm } from '../components/confirm.js'
import { autoGrow } from '../components/autogrow.js'
import { buildTagsInput } from '../components/tags-input.js'
import {
  assetTags,
  deleteAsset,
  embedUrl,
  filterAssets,
  iiifUrl,
  knownTags,
  listAllAssets,
  thumbUrl,
  updateAsset,
  uploadFile,
} from './media-api.js'

// ---------------------------------------------------------------------------
// Shared store
// ---------------------------------------------------------------------------

/**
 * The library as loaded. `uploads` are files on their way in, shown as tiles
 * at the front of the grid until they land.
 */
export const media = {
  assets: [],
  loaded: false,
  error: null,
  /** @type {{id: number, name: string, stage: string}[]} */
  uploads: [],
}

const listeners = new Set()
let loading = null
let uploadSeq = 0

/** Call *fn* whenever the library changes. Returns the unsubscribe. */
export function onMediaChange(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function changed() {
  for (const fn of listeners) fn()
}

/**
 * Load the library once; later calls share the first load. `force` reloads.
 *
 * @param {{force?: boolean}} [opts]
 * @returns {Promise<void>}
 */
export function loadMedia({ force = false } = {}) {
  if (loading && !force) return loading
  loading = listAllAssets()
    .then(assets => {
      media.assets = assets
      media.loaded = true
      media.error = null
    })
    .catch(err => {
      media.error = err.message
      loading = null  // let the next open try again
    })
    .finally(changed)
  return loading
}

/** Replace one asset in the list (after a save). */
function putAsset(asset) {
  const i = media.assets.findIndex(a => a.key === asset.key)
  if (i >= 0) media.assets[i] = { ...media.assets[i], ...asset }
  else media.assets.unshift(asset)
  changed()
}

/**
 * Upload files; each becomes a tile at once and an asset when it lands.
 *
 * @param {Iterable<File>} files
 * @param {(asset: object) => void} [onUploaded] — called with each new asset
 */
export function startUploads(files, onUploaded = () => {}) {
  for (const file of files) {
    const upload = { id: ++uploadSeq, name: file.name, stage: 'preparing' }
    media.uploads = [upload, ...media.uploads]
    changed()

    uploadFile(file, stage => { upload.stage = stage; changed() })
      .then(asset => {
        media.uploads = media.uploads.filter(u => u !== upload)
        // The key is content-addressed: the same file twice is one asset.
        media.assets = [asset, ...media.assets.filter(a => a.key !== asset.key)]
        changed()
        onUploaded(asset)
      })
      .catch(err => {
        media.uploads = media.uploads.filter(u => u !== upload)
        changed()
        showToast('error', `Couldn't upload ${file.name}`, err.message, 6000)
      })
  }
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

const STAGE_LABEL = { preparing: 'Preparing…', uploading: 'Uploading…', confirming: 'Processing…' }

/**
 * A grid of asset tiles, uploads first.
 *
 * @param {{assets: object[], selectedKey?: string|null, onSelect: (asset: object) => void,
 *          uploads?: object[], empty?: string}} opts
 * @returns {HTMLElement}
 */
export function buildAssetGrid({ assets, selectedKey = null, onSelect, uploads = [], empty }) {
  const grid = el('div', { class: 'media-grid', role: 'list' })

  for (const u of uploads) {
    grid.appendChild(el('div', { class: 'media-tile media-tile--pending', role: 'listitem', title: u.name },
      el('div', { class: 'loading-spinner' }),
      el('span', { class: 'media-tile__status' }, STAGE_LABEL[u.stage] || 'Uploading…'),
    ))
  }

  for (const asset of assets) {
    const isActive = asset.key === selectedKey
    grid.appendChild(el('button', {
      type: 'button',
      class: `media-tile${isActive ? ' is-active' : ''}`,
      role: 'listitem',
      title: asset.filename,
      'aria-label': asset.alt_text || asset.filename,
      'aria-current': isActive ? 'true' : null,
      'data-key': asset.key,
      onclick: () => onSelect(asset),
    },
      el('img', { src: thumbUrl(asset.key), alt: '', loading: 'lazy', decoding: 'async' }),
      asset.alt_text ? null : el('span', { class: 'media-tile__flag', title: 'No alt text' }, 'No alt'),
    ))
  }

  if (!assets.length && !uploads.length && empty) {
    grid.appendChild(el('div', { class: 'media-grid__empty' }, empty))
  }
  return grid
}

/**
 * An Upload button with its hidden file input.
 *
 * @param {(files: FileList) => void} onFiles
 * @param {{label?: string, iconOnly?: boolean}} [opts]
 * @returns {HTMLElement}
 */
export function buildUploadButton(onFiles, { label = 'Upload', iconOnly = false } = {}) {
  const input = el('input', {
    type: 'file',
    accept: 'image/*',
    multiple: 'multiple',
    hidden: 'hidden',
    onchange: e => {
      if (e.target.files?.length) onFiles(e.target.files)
      e.target.value = ''  // the same file again still fires change
    },
  })
  const button = el('button', {
    type: 'button',
    class: iconOnly ? 'icon-btn' : 'btn btn--primary btn--sm',
    title: label,
    'aria-label': label,
    onclick: () => input.click(),
  }, icon('upload', { size: iconOnly ? 18 : 16 }), iconOnly ? null : el('span', {}, label))
  return el('span', { class: 'media-upload' }, button, input)
}

/**
 * Accept files dropped on *target*, with a highlight while one is over it.
 *
 * @param {HTMLElement} target
 * @param {(files: FileList) => void} onFiles
 */
export function wireDropTarget(target, onFiles) {
  let depth = 0
  const hasFiles = e => [...(e.dataTransfer?.types || [])].includes('Files')
  target.addEventListener('dragenter', e => {
    if (!hasFiles(e)) return
    e.preventDefault()
    depth++
    target.classList.add('is-drop-target')
  })
  target.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault() })
  target.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1)
    if (!depth) target.classList.remove('is-drop-target')
  })
  target.addEventListener('drop', e => {
    if (!hasFiles(e)) return
    e.preventDefault()
    depth = 0
    target.classList.remove('is-drop-target')
    if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files)
  })
}

function formatBytes(n) {
  if (!Number.isFinite(n)) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

async function copy(text, what) {
  try {
    await navigator.clipboard.writeText(text)
    showToast('success', `${what} copied`)
  } catch {
    showToast('error', `Couldn't copy the ${what.toLowerCase()}`, text, 6000)
  }
}

/**
 * The chosen asset: a large preview, its URL for posts, alt text and tags
 * (each saved when it changes), and Delete.
 *
 * @param {object} asset
 * @param {{onDeleted?: (key: string) => void}} [opts]
 * @returns {HTMLElement}
 */
export function buildAssetDetail(asset, { onDeleted = () => {} } = {}) {
  const status = el('span', { class: 'save-status', 'aria-live': 'polite' })
  const setStatus = text => { status.textContent = text }

  async function save(patch) {
    setStatus('Saving…')
    try {
      putAsset(await updateAsset(asset.key, patch))
      setStatus('Saved')
    } catch (err) {
      setStatus('')
      showToast('error', "Couldn't save", err.message, 6000)
    }
  }

  const alt = autoGrow(el('textarea', {
    class: 'field-textarea field-textarea--grow',
    id: 'media-alt',
    rows: '2',
    placeholder: 'What the image shows, for someone who can’t see it',
  }))
  alt.value = asset.alt_text || ''
  let savedAlt = alt.value
  const saveAlt = () => {
    if (alt.value === savedAlt) return
    savedAlt = alt.value
    save({ alt_text: alt.value })
  }
  alt.addEventListener('blur', saveAlt)
  alt.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); alt.blur() } })

  const tags = buildTagsInput({
    id: 'media-tags',
    value: assetTags(asset),
    known: Promise.resolve(knownTags(media.assets)),
    onChange: next => save({ tags: next }),
  })

  const url = embedUrl(asset.key)
  const urlInput = el('input', {
    class: 'field-input media-detail__url',
    type: 'text',
    readonly: 'readonly',
    value: url,
    'aria-label': 'Image URL',
    onfocus: e => e.target.select(),
  })

  const dims = asset.width && asset.height ? `${asset.width} × ${asset.height}` : ''
  const meta = [dims, formatBytes(asset.size), asset.content_type].filter(Boolean).join(' · ')

  return el('article', { class: 'media-detail' },
    el('figure', { class: 'media-detail__preview' },
      el('img', { src: iiifUrl(asset.key), alt: asset.alt_text || '', decoding: 'async' })
    ),
    el('div', { class: 'media-detail__fields' },
      el('div', { class: 'media-detail__head' },
        el('h2', { class: 'media-detail__name', title: asset.key }, asset.filename),
        status,
      ),
      el('p', { class: 'media-detail__meta' }, meta),

      el('div', { class: 'field-group' },
        el('label', { class: 'field-label', for: 'media-url' }, 'URL for posts'),
        el('span', { class: 'field-help' }, 'Absolute and 1200 wide. Change the number after full/ for another width.'),
        el('div', { class: 'media-detail__url-row' },
          urlInput,
          el('button', { type: 'button', class: 'btn btn--ghost btn--sm', onclick: () => copy(url, 'URL') }, 'Copy'),
          el('button', {
            type: 'button',
            class: 'btn btn--ghost btn--sm',
            title: 'Copy as Markdown',
            onclick: () => copy(`![${(alt.value || '').replace(/[[\]]/g, '')}](${url})`, 'Markdown'),
          }, 'Markdown'),
        ),
      ),

      el('div', { class: 'field-group' },
        el('label', { class: 'field-label', for: 'media-alt' }, 'Alt text'),
        alt,
      ),

      el('div', { class: 'field-group' },
        el('label', { class: 'field-label', for: 'media-tags' }, 'Tags'),
        tags,
      ),

      el('div', { class: 'media-detail__danger' },
        el('button', {
          type: 'button',
          class: 'btn btn--danger btn--sm',
          onclick: async () => {
            const ok = await showConfirm(
              'Delete this image?',
              `${asset.filename} and every size made from it are removed. A post that uses it will show a broken image.`,
            )
            if (!ok) return
            try {
              await deleteAsset(asset.key)
              media.assets = media.assets.filter(a => a.key !== asset.key)
              changed()
              onDeleted(asset.key)
              showToast('success', 'Image deleted')
            } catch (err) {
              showToast('error', "Couldn't delete", err.message, 6000)
            }
          },
        }, 'Delete image'),
      ),
    ),
  )
}

// ---------------------------------------------------------------------------
// The shell's Media section
// ---------------------------------------------------------------------------

/**
 * The Media section: the grid in the document column, the chosen asset in the
 * main pane. The shell shows or hides the two containers; this fills them.
 */
export class MediaSection {
  /**
   * @param {{listEl: HTMLElement, mainEl: HTMLElement, onSelect?: () => void}} opts
   *   onSelect — after an asset is chosen (the shell closes the drawer on a phone)
   */
  constructor({ listEl, mainEl, onSelect = () => {} }) {
    this.listEl = listEl
    this.mainEl = mainEl
    this.onSelect = onSelect
    this.activeKey = null
    this.filter = ''
    this.open = false

    const upload = files => startUploads(files, asset => {
      if (this.open && !this.activeKey) this.select(asset)
    })

    this.search = el('input', {
      class: 'sidebar-docs__search',
      type: 'search',
      placeholder: 'Filter by name, alt text or tag',
      'aria-label': 'Filter media',
      oninput: e => { this.filter = e.target.value; this.renderList() },
    })
    this.uploadButton = buildUploadButton(upload, { label: 'Upload images', iconOnly: true })
    this.gridWrap = el('div', { class: 'sidebar-docs__list media-list__grid' })
    listEl.append(this.search, this.gridWrap)
    wireDropTarget(listEl, upload)
    wireDropTarget(mainEl, upload)
    this._upload = upload

    onMediaChange(() => { if (this.open) this.render() })
  }

  /** Show the section, loading the library the first time. */
  show() {
    this.open = true
    loadMedia()
    this.render()
  }

  hide() {
    this.open = false
  }

  /** @param {object} asset */
  select(asset) {
    this.activeKey = asset.key
    this.render({ force: true })
    this.onSelect()
  }

  /**
   * Redraw. The detail pane is kept when it already shows the chosen asset, so
   * an upload finishing or a save landing does not take the cursor out of the
   * alt text; `force` redraws it anyway.
   *
   * @param {{force?: boolean}} [opts]
   */
  render({ force = false } = {}) {
    this.renderList()
    this.renderMain({ force })
  }

  renderList() {
    this.gridWrap.innerHTML = ''
    if (media.error) {
      this.gridWrap.appendChild(el('div', { class: 'sidebar-docs__empty' }, `Couldn't load media: ${media.error}`))
      return
    }
    if (!media.loaded) {
      this.gridWrap.appendChild(el('div', { class: 'sidebar-docs__empty' },
        el('div', { class: 'loading-spinner', style: 'margin: 0 auto;' })))
      return
    }
    const visible = filterAssets(media.assets, this.filter)
    this.gridWrap.appendChild(buildAssetGrid({
      assets: visible,
      uploads: media.uploads,
      selectedKey: this.activeKey,
      onSelect: asset => this.select(asset),
      empty: this.filter ? `Nothing matches "${this.filter}"` : 'No images yet. Upload one, or drop files here.',
    }))
  }

  renderMain({ force = false } = {}) {
    const asset = media.assets.find(a => a.key === this.activeKey)
    const shown = asset ? asset.key : null
    if (!force && shown && this.shownKey === shown) return
    this.shownKey = shown
    this.mainEl.innerHTML = ''
    if (asset) {
      this.mainEl.appendChild(buildAssetDetail(asset, {
        onDeleted: () => { this.activeKey = null; this.render({ force: true }) },
      }))
      return
    }
    this.mainEl.appendChild(el('div', { class: 'media-empty' },
      el('span', { class: 'media-empty__icon' }, icon('image', { size: 32 })),
      el('p', {}, media.loaded
        ? `${media.assets.length} image${media.assets.length === 1 ? '' : 's'}. Choose one to see its URL, alt text and tags.`
        : 'Loading the library…'),
      el('p', { class: 'media-empty__hint' }, 'Drop images anywhere here to upload them.'),
      buildUploadButton(this._upload, { label: 'Upload images' }),
    ))
  }
}
