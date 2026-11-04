/**
 * media/media-api.js — The astraeus-media HTTP API, as the editor uses it.
 *
 * astraeus-media is mounted beside the CMS at `mediaBase` (Editor(media_base=…)),
 * on the same origin, so the CMS login cookie authorises every call: no key is
 * sent. Nothing here imports astraeus-media; it is a URL, as for the CMS.
 *
 *   GET    {base}/assets?limit&offset   list, newest first
 *   PATCH  {base}/assets/{key}          { alt_text?, tags? }
 *   DELETE {base}/assets/{key}          the original, its renditions and its entry
 *   POST   {base}/upload/prepare        → { upload_url, key }
 *   PUT    upload_url                   the bytes, straight to the bucket
 *   POST   {base}/upload/confirm        → the new asset
 */

const CONFIG = globalThis.__EDITOR_CONFIG__ || {}

/** Where astraeus-media is mounted, or '' when the editor was given none. */
export function mediaBase() {
  return (CONFIG.mediaBase || '').replace(/\/$/, '')
}

/** True when the shell has a media library to show. */
export function hasMedia() {
  return Boolean(CONFIG.mediaBase)
}

/** Keys are paths (`originals/ab/c.png`): keep their slashes, escape the rest. */
const keyPath = key => key.split('/').map(encodeURIComponent).join('/')

/**
 * A IIIF rendition URL for *key*. The library's own views use the renditions
 * made on upload (square 256 for tiles, 800 wide for the preview), so they load
 * without cutting anything new.
 *
 * @param {string} key
 * @param {{region?: string, size?: string, absolute?: boolean}} [opts]
 * @returns {string}
 */
export function iiifUrl(key, { region = 'full', size = '800,', absolute = false } = {}) {
  const path = `${mediaBase()}/iiif/${keyPath(key)}/${region}/${size}/0/default.webp`
  return absolute ? new URL(path, globalThis.location?.href).href : path
}

/** The tile rendition (square, 256 wide). */
export const thumbUrl = key => iiifUrl(key, { region: 'square', size: '256,' })

/**
 * The URL to put in a post: absolute, because the site is static and a
 * relative src would resolve against the site's host, and 1200 wide.
 *
 * @param {string} key
 * @returns {string}
 */
export const embedUrl = key => iiifUrl(key, { size: '1200,', absolute: true })

/**
 * The asset key inside a URL made by iiifUrl/embedUrl, or null.
 *
 * @param {string} url
 * @returns {string|null}
 */
export function keyFromUrl(url) {
  const match = /\/iiif\/(.+?)\/(?:full|square|\d+,\d+,\d+,\d+)\/[^/]+\/0\/default\.\w+$/.exec(url || '')
  if (!match) return null
  try {
    return match[1].split('/').map(decodeURIComponent).join('/')
  } catch {
    return match[1]
  }
}

async function call(path, opts = {}) {
  const res = await fetch(mediaBase() + path, {
    credentials: 'same-origin',
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = new Error(body.error || `${res.status} ${res.statusText}`)
    err.status = res.status
    throw err
  }
  return res.status === 204 ? null : res.json()
}

/**
 * Every asset, newest first, a page at a time until the list runs out.
 *
 * @param {{pageSize?: number, max?: number}} [opts]
 * @returns {Promise<object[]>}
 */
export async function listAllAssets({ pageSize = 200, max = 2000 } = {}) {
  const all = []
  for (let offset = 0; offset < max; offset += pageSize) {
    const { assets } = await call(`/assets?limit=${pageSize}&offset=${offset}`)
    all.push(...assets)
    if (assets.length < pageSize) break
  }
  return all
}

/**
 * @param {string} key
 * @param {{alt_text?: string, tags?: string[]}} patch
 * @returns {Promise<object>} the updated asset
 */
export function updateAsset(key, patch) {
  return call(`/assets/${keyPath(key)}`, { method: 'PATCH', body: JSON.stringify(patch) })
}

/** @param {string} key */
export function deleteAsset(key) {
  return call(`/assets/${keyPath(key)}`, { method: 'DELETE' })
}

/**
 * Upload one file: prepare, PUT to the bucket, confirm.
 *
 * @param {File} file
 * @param {(stage: 'preparing'|'uploading'|'confirming') => void} [onStage]
 * @returns {Promise<object>} the new asset
 */
export async function uploadFile(file, onStage = () => {}) {
  const contentType = file.type || 'application/octet-stream'
  const meta = { filename: file.name, content_type: contentType, size: file.size }

  onStage('preparing')
  const { upload_url: uploadUrl, key } = await call('/upload/prepare', {
    method: 'POST',
    body: JSON.stringify(meta),
  })

  onStage('uploading')
  const put = await fetch(uploadUrl, { method: 'PUT', headers: { 'Content-Type': contentType }, body: file })
  if (!put.ok) throw new Error(`Upload failed (${put.status})`)

  onStage('confirming')
  return call('/upload/confirm', { method: 'POST', body: JSON.stringify({ key, ...meta }) })
}

/**
 * An asset's tags as a list. The catalog stores them as a JSON string and the
 * API hands that string back as it is.
 *
 * @param {object} asset
 * @returns {string[]}
 */
export function assetTags(asset) {
  const raw = asset?.tags
  if (Array.isArray(raw)) return raw
  if (typeof raw !== 'string' || !raw) return []
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(t => typeof t === 'string') : []
  } catch {
    return []
  }
}

/**
 * Assets whose filename, alt text or tags contain *query* (case-insensitive).
 *
 * @param {object[]} assets
 * @param {string} query
 * @returns {object[]}
 */
export function filterAssets(assets, query) {
  const needle = (query || '').trim().toLowerCase()
  if (!needle) return assets
  return assets.filter(a =>
    `${a.filename || ''} ${a.alt_text || ''} ${assetTags(a).join(' ')}`.toLowerCase().includes(needle),
  )
}

/**
 * The tags already on assets, most used first: suggestions for the tag input.
 *
 * @param {object[]} assets
 * @returns {{tag: string, count: number}[]}
 */
export function knownTags(assets) {
  const counts = new Map()
  for (const a of assets) for (const t of assetTags(a)) counts.set(t, (counts.get(t) || 0) + 1)
  return [...counts].map(([tag, count]) => ({ tag, count })).sort((a, b) => b.count - a.count)
}
