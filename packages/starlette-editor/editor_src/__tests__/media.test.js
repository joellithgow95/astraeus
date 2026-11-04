// @vitest-environment happy-dom
/**
 * Tests for the media library inside the shell: media/media-api.js helpers,
 * the shared grid, the image fields and their widget dispatch.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.hoisted(() => {
  globalThis.__EDITOR_CONFIG__ = { mediaBase: '/media/' }
})

vi.mock('../api.js', () => ({ fetchTags: vi.fn(async () => []) }))

import {
  assetTags,
  embedUrl,
  filterAssets,
  hasMedia,
  iiifUrl,
  keyFromUrl,
  knownTags,
  listAllAssets,
  mediaBase,
  thumbUrl,
  updateAsset,
  uploadFile,
} from '../media/media-api.js'
import { buildAssetGrid } from '../media/media-library.js'
import { buildImageObjectField, isImageObjectMeta, previewSrc } from '../components/image-picker.js'
import { fieldWidget } from '../components/fields.js'

const KEY = 'originals/ab/cat photo.png'

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('media-api URLs', () => {
  it('reads the base from the config, without a trailing slash', () => {
    expect(mediaBase()).toBe('/media')
    expect(hasMedia()).toBe(true)
  })

  it('builds IIIF rendition URLs', () => {
    expect(iiifUrl(KEY)).toBe("/media/iiif/originals/ab/cat%20photo.png/full/800,/0/default.webp")
    expect(thumbUrl(KEY)).toBe("/media/iiif/originals/ab/cat%20photo.png/square/256,/0/default.webp")
  })

  it('makes the post URL absolute and 1200 wide', () => {
    const url = embedUrl(KEY)
    expect(url.startsWith(location.origin)).toBe(true)
    expect(url).toMatch(/\/media\/iiif\/.+\/full\/1200,\/0\/default\.webp$/)
  })

  it('finds the key in any rendition URL, and nothing in other URLs', () => {
    expect(keyFromUrl(`https://cms.example.com/media/iiif/${KEY}/full/1200,/0/default.webp`)).toBe(KEY)
    expect(keyFromUrl(`/media/iiif/${KEY}/0,0,10,10/64,/0/default.jpg`)).toBe(KEY)
    expect(keyFromUrl(embedUrl(KEY))).toBe(KEY)
    expect(keyFromUrl('https://example.com/a.png')).toBeNull()
    expect(keyFromUrl('')).toBeNull()
  })
})

describe('media-api data helpers', () => {
  it('parses tags stored as a JSON string', () => {
    expect(assetTags({ tags: '["cat","garden"]' })).toEqual(['cat', 'garden'])
    expect(assetTags({ tags: ['a'] })).toEqual(['a'])
    expect(assetTags({ tags: 'not json' })).toEqual([])
    expect(assetTags({ tags: null })).toEqual([])
    expect(assetTags({ tags: '{"a":1}' })).toEqual([])
  })

  const assets = [
    { key: 'a', filename: 'Cat.png', alt_text: 'A cat asleep', tags: '["pets"]' },
    { key: 'b', filename: 'tree.jpg', alt_text: '', tags: '["garden","pets"]' },
    { key: 'c', filename: 'logo.svg', alt_text: null, tags: null },
  ]

  it('filters by filename, alt text or tag, ignoring case', () => {
    expect(filterAssets(assets, '').length).toBe(3)
    expect(filterAssets(assets, 'cat').map(a => a.key)).toEqual(['a'])
    expect(filterAssets(assets, 'ASLEEP').map(a => a.key)).toEqual(['a'])
    expect(filterAssets(assets, 'pets').map(a => a.key)).toEqual(['a', 'b'])
    expect(filterAssets(assets, 'nothing')).toEqual([])
  })

  it('counts known tags, most used first', () => {
    expect(knownTags(assets)).toEqual([{ tag: 'pets', count: 2 }, { tag: 'garden', count: 1 }])
  })
})

describe('media-api requests', () => {
  let fetchMock
  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('pages through the list until a short page', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ assets: [{ key: '1' }, { key: '2' }] }))
      .mockResolvedValueOnce(jsonResponse({ assets: [{ key: '3' }] }))
    const all = await listAllAssets({ pageSize: 2 })
    expect(all.map(a => a.key)).toEqual(['1', '2', '3'])
    expect(fetchMock.mock.calls[0][0]).toBe('/media/assets?limit=2&offset=0')
    expect(fetchMock.mock.calls[1][0]).toBe('/media/assets?limit=2&offset=2')
    expect(fetchMock.mock.calls[0][1].credentials).toBe('same-origin')
  })

  it('escapes a key but keeps its slashes', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ key: KEY, alt_text: 'x' }))
    await updateAsset(KEY, { alt_text: 'x' })
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/media/assets/originals/ab/cat%20photo.png')
    expect(opts.method).toBe('PATCH')
    expect(JSON.parse(opts.body)).toEqual({ alt_text: 'x' })
  })

  it('surfaces the API error message', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: 'Not allowed' }, 403))
    await expect(updateAsset(KEY, {})).rejects.toMatchObject({ message: 'Not allowed', status: 403 })
  })

  it('uploads in three steps: prepare, PUT, confirm', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ upload_url: 'https://bucket.example/put', key: KEY }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(jsonResponse({ key: KEY, filename: 'cat photo.png' }))
    const stages = []
    const file = new File(['png'], 'cat photo.png', { type: 'image/png' })
    const asset = await uploadFile(file, s => stages.push(s))

    expect(stages).toEqual(['preparing', 'uploading', 'confirming'])
    expect(asset.key).toBe(KEY)
    expect(fetchMock.mock.calls[1][0]).toBe('https://bucket.example/put')
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'PUT', headers: { 'Content-Type': 'image/png' } })
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toMatchObject({ key: KEY, filename: 'cat photo.png' })
  })

  it('fails when the PUT to the bucket fails', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ upload_url: 'https://bucket.example/put', key: KEY }))
      .mockResolvedValueOnce(new Response(null, { status: 403 }))
    await expect(uploadFile(new File(['x'], 'x.png'))).rejects.toThrow('Upload failed (403)')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('buildAssetGrid', () => {
  const assets = [
    { key: 'k1', filename: 'one.png', alt_text: 'One' },
    { key: 'k2', filename: 'two.png', alt_text: '' },
  ]

  it('draws a tile per asset, flags missing alt text, marks the chosen one', () => {
    const grid = buildAssetGrid({ assets, selectedKey: 'k2', onSelect: () => {} })
    const tiles = grid.querySelectorAll('.media-tile')
    expect(tiles.length).toBe(2)
    expect(tiles[0].querySelector('img').getAttribute('src')).toBe(thumbUrl('k1'))
    expect(tiles[0].querySelector('.media-tile__flag')).toBeNull()
    expect(tiles[1].querySelector('.media-tile__flag').textContent).toBe('No alt')
    expect(tiles[1].classList.contains('is-active')).toBe(true)
    expect(tiles[1].getAttribute('aria-current')).toBe('true')
    expect(tiles[0].hasAttribute('aria-current')).toBe(false)
  })

  it('calls onSelect with the asset', () => {
    const onSelect = vi.fn()
    const grid = buildAssetGrid({ assets, onSelect })
    grid.querySelectorAll('.media-tile')[1].click()
    expect(onSelect).toHaveBeenCalledWith(assets[1])
  })

  it('puts uploads first and shows the empty message only when there is nothing', () => {
    const grid = buildAssetGrid({ assets: [], uploads: [{ id: 1, name: 'x.png', stage: 'uploading' }], onSelect: () => {}, empty: 'None' })
    expect(grid.querySelector('.media-tile--pending .media-tile__status').textContent).toBe('Uploading…')
    expect(grid.querySelector('.media-grid__empty')).toBeNull()

    const empty = buildAssetGrid({ assets: [], onSelect: () => {}, empty: 'None' })
    expect(empty.querySelector('.media-grid__empty').textContent).toBe('None')
  })
})

describe('image fields', () => {
  const imageMeta = { field_type: 'json', schema: { type: 'object', properties: { src: { type: 'string' }, alt: { type: 'string' } } } }

  it('gives a JSONField with an {src, alt} schema the image widget', () => {
    expect(isImageObjectMeta(imageMeta)).toBe(true)
    expect(fieldWidget('image', {}, imageMeta)).toBe('image_object')
    // What JSONField actually sends: the hint, and no field_type.
    expect(fieldWidget('image', { anyOf: [{ type: 'object' }, { type: 'null' }] }, { schema: imageMeta.schema })).toBe('image_object')
    expect(fieldWidget('config', {}, { field_type: 'json', schema: { type: 'object' } })).toBe('json')
    expect(fieldWidget('config', {}, { field_type: 'json' })).toBe('json')
    expect(fieldWidget('cover', {}, { field_type: 'image' })).toBe('image_picker')
  })

  it('previews library URLs with the prerendered size, others as they are', () => {
    expect(previewSrc(embedUrl(KEY))).toBe(iiifUrl(KEY))
    expect(previewSrc('https://example.com/a.png')).toBe('https://example.com/a.png')
  })

  it('edits src and alt and keeps the other keys', () => {
    const onChange = vi.fn()
    const wrap = buildImageObjectField('image', { src: 'https://e.com/a.png', alt: 'A', type: 'image' }, onChange)
    const [src, alt] = wrap.querySelectorAll('input')
    expect(src.value).toBe('https://e.com/a.png')
    expect(alt.value).toBe('A')
    expect(wrap.querySelector('.image-picker__preview img')).not.toBeNull()

    alt.value = 'A cat'
    alt.dispatchEvent(new Event('input'))
    expect(onChange).toHaveBeenLastCalledWith('image', { src: 'https://e.com/a.png', alt: 'A cat', type: 'image' })
  })

  it('keeps focus in the URL input while the preview redraws', () => {
    const wrap = buildImageObjectField('image', null, () => {})
    document.body.appendChild(wrap)
    const src = wrap.querySelector('input')
    src.focus()
    src.value = 'https://e.com/b.png'
    src.dispatchEvent(new Event('input'))
    expect(document.activeElement).toBe(src)
    expect(wrap.querySelector('.image-picker__preview img').getAttribute('src')).toBe('https://e.com/b.png')
    wrap.remove()
  })

  it('sets the field to null when Remove empties it', () => {
    const onChange = vi.fn()
    const wrap = buildImageObjectField('image', { src: 'https://e.com/a.png', alt: 'A' }, onChange)
    const remove = [...wrap.querySelectorAll('button')].find(b => b.textContent === 'Remove')
    remove.click()
    expect(onChange).toHaveBeenLastCalledWith('image', null)
  })
})
