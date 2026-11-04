/**
 * media/media-picker.js — Choose an image from the library, or upload one.
 *
 * The same grid, search and upload as the shell's Media section, in a dialog.
 * A file uploaded here lands in the shared library and is chosen at once.
 */

import { el } from '../utils.js'
import { icon } from '../icons.js'
import { filterAssets } from './media-api.js'
import {
  buildAssetGrid,
  buildUploadButton,
  loadMedia,
  media,
  onMediaChange,
  startUploads,
  wireDropTarget,
} from './media-library.js'

/**
 * Open the picker. Resolves with the chosen asset, or null when closed.
 *
 * @param {{title?: string}} [opts]
 * @returns {Promise<object|null>}
 */
export function openMediaPicker({ title = 'Choose an image' } = {}) {
  return new Promise(resolve => {
    let filter = ''
    let done = false

    const finish = asset => {
      if (done) return
      done = true
      unsubscribe()
      document.removeEventListener('keydown', onKey, true)
      backdrop.remove()
      resolve(asset)
    }
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); finish(null) } }

    const upload = files => startUploads(files, asset => finish(asset))

    const body = el('div', { class: 'media-picker__body' })
    const search = el('input', {
      class: 'sidebar-docs__search media-picker__search',
      type: 'search',
      placeholder: 'Filter by name, alt text or tag',
      'aria-label': 'Filter media',
      oninput: e => { filter = e.target.value; renderBody() },
    })

    function renderBody() {
      body.innerHTML = ''
      if (media.error) {
        body.appendChild(el('div', { class: 'sidebar-docs__empty' }, `Couldn't load media: ${media.error}`))
        return
      }
      if (!media.loaded) {
        body.appendChild(el('div', { class: 'sidebar-docs__empty' },
          el('div', { class: 'loading-spinner', style: 'margin: 0 auto;' })))
        return
      }
      body.appendChild(buildAssetGrid({
        assets: filterAssets(media.assets, filter),
        uploads: media.uploads,
        onSelect: finish,
        empty: filter ? `Nothing matches "${filter}"` : 'No images yet. Upload one, or drop files here.',
      }))
    }

    const dialog = el('div', { class: 'media-picker', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      el('header', { class: 'media-picker__header' },
        el('h2', { class: 'media-picker__title' }, title),
        buildUploadButton(upload, { label: 'Upload' }),
        el('button', {
          type: 'button',
          class: 'icon-btn',
          title: 'Close',
          'aria-label': 'Close',
          onclick: () => finish(null),
        }, icon('x', { size: 20 })),
      ),
      search,
      body,
    )
    const backdrop = el('div', {
      class: 'overlay-backdrop media-picker__backdrop',
      onclick: e => { if (e.target === backdrop) finish(null) },
    }, dialog)

    wireDropTarget(dialog, upload)
    const unsubscribe = onMediaChange(renderBody)
    document.addEventListener('keydown', onKey, true)
    document.body.appendChild(backdrop)
    renderBody()
    loadMedia()
    search.focus()
  })
}
