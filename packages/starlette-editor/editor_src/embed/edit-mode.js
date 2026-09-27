/**
 * Activates inline edit mode for a [data-cms-id] element.
 * Fetches the draft body, replaces static content with live editable fields.
 */
import { getActiveChangesetId, setActiveChangesetId } from '../changeset-store.js'
import { getFieldWidget } from '../field-widgets.js'
import { openFloatingPanel } from '../components/floating-panel.js'

let _navSuppressed = false

/**
 * While editing, stop clicks on an editable field from following an ancestor
 * link. On listing pages the title sits inside its card's <a href>, so a click
 * to edit would otherwise navigate away. Focus happens on mousedown, so
 * preventing the click still lets the field take the caret. Installed once;
 * the page reloads on discard/publish, so it never needs removing.
 */
export function suppressAnchorNavigationWhileEditing() {
  if (_navSuppressed) return
  _navSuppressed = true
  document.addEventListener(
    'click',
    (e) => {
      const target = /** @type {Element} */ (e.target)
      if (target.closest && target.closest('[data-cms-field], [contenteditable="true"]')) {
        const anchor = target.closest('a')
        if (anchor) e.preventDefault()
      }
    },
    true,
  )
}

export async function activateEditMode(element, { cmsBase, toolbar }) {
  const docId = element.dataset.cmsId

  // Fetch draft body
  const res = await fetch(`${cmsBase}/api/documents/${docId}?draft=true`, {
    credentials: 'include',
  })
  if (!res.ok) return
  const doc = await res.json()
  const body = doc.body || {}

  // Activate each annotated field within the element
  const fieldEls = element.querySelectorAll('[data-cms-field]')
  for (const fieldEl of fieldEls) {
    const fieldName = fieldEl.dataset.cmsField
    let fieldValue = body[fieldName]
    if (fieldValue === undefined) {
      // A field's key can be absent from a document's stored body — most
      // often because the field type was added to the schema after this
      // particular document was last saved (nothing backfills existing rows).
      // Registry-dispatched field types are exactly the mechanism meant to
      // support that: a plugin field type shouldn't require every existing
      // document to be re-saved before it becomes editable. Untyped/legacy
      // fields keep the original behavior — genuinely absent means skip.
      if (!getFieldWidget(fieldEl.dataset.cmsFieldType)) continue
      fieldValue = null
    }

    // On a listing, the body is shown only as a truncated preview. Don't drop
    // the full editor into the card straight away — gate it behind an
    // Expand/Collapse toggle so the card stays compact until you choose to edit
    // the whole body inline.
    if (fieldEl.dataset.cmsCollapsible !== undefined) {
      attachCollapsibleField(fieldEl, { fieldName, fieldValue, docId, cmsBase, toolbar })
      continue
    }

    await activateField(fieldEl, {
      fieldName,
      fieldValue,
      docId,
      cmsBase,
      toolbar,
    })
  }

  // Individual fields get their own edit affordance (dashed outline, etc.) in
  // activateField. Outlining the whole container as well was misleading — on a
  // listing card it framed the entire card as editable when only the title and
  // description are.

  // On a listing card the title links to the post; nav is suppressed so the
  // click edits instead. Offer an explicit way to open the post — to edit the
  // body there — since edits carry across pages in one changeset.
  const link = element.querySelector('a[href]')
  const href = link?.getAttribute('href')
  if (href && !element.querySelector('[data-cms-open-link]')) {
    if (getComputedStyle(element).position === 'static') element.style.position = 'relative'
    const open = document.createElement('a')
    open.setAttribute('data-cms-open-link', '')
    open.href = href
    open.textContent = 'Open ↗'
    open.style.cssText = `
      position: absolute; top: 6px; right: 6px; z-index: 5;
      padding: 4px 10px; border-radius: 999px;
      background: #1a1a1a; color: #f0f0f0; border: 1px solid #2a2a2a;
      font: 500 12px/1 -apple-system, system-ui, sans-serif;
      text-decoration: none;
    `
    element.appendChild(open)
  }
}

/**
 * Gate a body field behind an Expand/Collapse toggle on a listing card.
 *
 * The card keeps its truncated preview for reading; clicking "Expand" mounts the
 * full field editor (e.g. ProseMirror) into a hidden host beside the preview and
 * hides the preview. "Collapse" swaps them back. The editor is mounted once and
 * only its visibility toggles, so edits survive collapsing and re-expanding.
 */
function attachCollapsibleField(preview, { fieldName, fieldValue, docId, cmsBase, toolbar }) {
  // Empty host the full editor mounts into; sits right after the preview.
  const host = document.createElement('div')
  host.dataset.cmsField = fieldName
  host.style.display = 'none'

  const toggle = document.createElement('button')
  toggle.type = 'button'
  toggle.style.cssText = `
    display: inline-flex; align-items: center; gap: 4px;
    margin-top: 8px; padding: 4px 10px; border-radius: 999px;
    background: #1a1a1a; color: #f0f0f0; border: 1px solid #2a2a2a;
    font: 500 12px/1 -apple-system, system-ui, sans-serif;
    cursor: pointer;
  `

  let mounted = false
  const setLabel = (expanded) => {
    toggle.textContent = expanded ? 'Collapse ⌃ body' : 'Expand ⌄ body'
  }
  setLabel(false)

  toggle.addEventListener('click', async (e) => {
    // The toggle can sit inside a card link; never navigate on click.
    e.preventDefault()
    e.stopPropagation()
    const expanded = host.style.display !== 'none'
    if (expanded) {
      host.style.display = 'none'
      preview.style.display = ''
      setLabel(false)
      return
    }
    if (!mounted) {
      await activateField(host, { fieldName, fieldValue, docId, cmsBase, toolbar })
      mounted = true
    }
    preview.style.display = 'none'
    host.style.display = ''
    setLabel(true)
  })

  preview.after(toggle)
  toggle.after(host)
}

export async function activateField(el, { fieldName, fieldValue, docId, cmsBase, toolbar }) {
  const tag = el.tagName.toLowerCase()

  // ── 0. Registry-dispatched field types (ADR 020) ────────────────────────────
  // An explicit data-cms-field-type wins over every value-shape guess below —
  // this is what lets a field type whose value happens to be e.g. an array
  // (like doodles) avoid colliding with case 4's tags-editor guess. Nothing
  // built-in is registered here; only plugin-added field types (e.g. doodles)
  // are, so every existing field keeps working via the chain below unchanged.
  const widget = getFieldWidget(el.dataset.cmsFieldType)
  if (widget) {
    _addEditAffordance(el)
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      const { container, close } = openFloatingPanel(el, { className: '__cms-field-panel' })
      widget(fieldValue, {
        mode: 'overlay',
        container,
        anchorEl: el,
        onSave: async (newValue) => {
          fieldValue = newValue
          close()
          toolbar.setState('saving')
          await patchField(cmsBase, docId, fieldName, newValue, toolbar)
          toolbar.setState('editing')
        },
      })
    })
    return
  }

  // ── 1. Rich text fields (ProseMirror JSON doc) ──────────────────────────────
  if (typeof fieldValue === 'object' && fieldValue !== null && fieldValue.type === 'doc') {
    const { mountProseMirrorOnElement } = await import('./prosemirror-embed.js')
    await mountProseMirrorOnElement(el, { doc: fieldValue, docId, fieldName, cmsBase, toolbar })
    return
  }

  // ── 2. Markdown textarea modal ───────────────────────────────────────────────
  // Triggered when value is a plain string AND element has data-cms-field-type="markdown" OR is a <div>
  if (typeof fieldValue === 'string' && (el.dataset.cmsFieldType === 'markdown' || tag === 'div')) {
    _addEditAffordance(el)
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      _openMarkdownModal(fieldValue, async (newValue) => {
        fieldValue = newValue
        toolbar.setState('saving')
        await patchField(cmsBase, docId, fieldName, newValue, toolbar)
        toolbar.setState('editing')
      })
    })
    return
  }

  // ── 3. Simple text fields (h1–h6, p, span) ──────────────────────────────────
  if (typeof fieldValue === 'string' && ['h1','h2','h3','h4','h5','h6','p','span'].includes(tag)) {
    // The page was built from the published copy; show the draft being edited.
    // Only a leaf element — one with markup inside would lose it.
    if (el.children.length === 0 && el.textContent !== fieldValue) el.textContent = fieldValue
    el.contentEditable = 'true'
    el.style.outline = '1px dashed rgba(37, 99, 235, 0.5)'
    el.style.minHeight = '1em'

    // Debounced PATCH on input
    let debounceTimer
    el.addEventListener('input', () => {
      clearTimeout(debounceTimer)
      toolbar.setState('saving')
      debounceTimer = setTimeout(async () => {
        await patchField(cmsBase, docId, fieldName, el.textContent, toolbar)
        toolbar.setState('editing')
      }, 800)
    })
    return
  }

  // ── 4. Tags array editor ─────────────────────────────────────────────────────
  if (Array.isArray(fieldValue)) {
    _addEditAffordance(el)
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      _openTagsEditor(el, fieldValue, async (newTags) => {
        fieldValue = newTags
        toolbar.setState('saving')
        await patchField(cmsBase, docId, fieldName, newTags, toolbar)
        toolbar.setState('editing')
      })
    })
    return
  }

  // ── 5. Image picker ──────────────────────────────────────────────────────────
  if (fieldValue && typeof fieldValue === 'object' && 'src' in fieldValue) {
    _addEditAffordance(el)
    const CONFIG = window.__EDITOR_CONFIG__ || {}
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      _openImageEditor(el, fieldValue, CONFIG, async (newValue) => {
        fieldValue = newValue
        toolbar.setState('saving')
        await patchField(cmsBase, docId, fieldName, newValue, toolbar)
        // Update the live <img> in the element without a reload
        const img = el.querySelector('img')
        if (img && newValue.src) {
          img.src = newValue.src
          img.alt = newValue.alt || ''
        }
        toolbar.setState('editing')
      })
    })
    return
  }
}

// ────────────────────────────────────────────────────────────────────────────
// PATCH helper
// ────────────────────────────────────────────────────────────────────────────

async function patchField(cmsBase, docId, fieldName, value, toolbar) {
  // Carry the active changeset so every edit in a session lands in one
  // publishable group. changeset-store persists it in localStorage, so it
  // survives navigating between pages on the site.
  const headers = { 'Content-Type': 'application/json' }
  const activeId = getActiveChangesetId()
  if (activeId) headers['X-Active-Changeset-Id'] = activeId

  let res
  try {
    res = await fetch(`${cmsBase}/api/documents/${docId}`, {
      method: 'PATCH',
      credentials: 'include',
      headers,
      body: JSON.stringify({ body: { [fieldName]: value } }),
    })
  } catch {
    // Network/CORS failure — the edit is not saved. Surface it rather than
    // letting the reload silently drop the work (see the toolbar error pill).
    toolbar?.setSaveError?.('⚠ Not saved — connection failed')
    return
  }

  if (!res.ok) {
    // A failed save was previously swallowed, so the editor looked fine right
    // up until a reload wiped the un-persisted edits. Make it visible.
    toolbar?.setSaveError?.(await _saveErrorMessage(res))
    return
  }

  toolbar?.clearSaveError?.()

  // Adopt a changeset the server auto-created when none was active, so the
  // rest of the session — and the changeset panel, via the store event —
  // groups into it too. Header reads are case-insensitive.
  const created = res.headers.get('X-Changeset-Id')
  if (created && created !== activeId) setActiveChangesetId(created)
}

/**
 * Turn a failed PATCH response into a toolbar message that says what to do.
 * A 422 names the failing field (from the validation `detail`), since a bare
 * status code gives no clue which part of the document is wrong.
 */
async function _saveErrorMessage(res) {
  if (res.status === 401) return '⚠ Not saved — session expired, log in again'

  if (res.status === 422) {
    const data = await res.json().catch(() => null)
    const field = data?.detail?.[0]?.loc?.at(-1) ?? data?.field
    if (field) return `⚠ Not saved — "${field}" is invalid`
  }

  return `⚠ Not saved — server error ${res.status}`
}

// ────────────────────────────────────────────────────────────────────────────
// Shared affordance helper
// ────────────────────────────────────────────────────────────────────────────

function _addEditAffordance(el) {
  el.style.outline = '1px dashed rgba(37, 99, 235, 0.5)'
  el.style.cursor = 'pointer'
  el.title = 'Click to edit'
  // A field's own render CSS may set pointer-events: none for its unactivated,
  // purely-decorative display (e.g. a doodle overlay, so it never blocks
  // clicks on real content beneath it) — activation always needs to override
  // that on this element specifically, or the click that's supposed to open
  // its editor can never reach it in the first place.
  el.style.pointerEvents = 'auto'
}

// ────────────────────────────────────────────────────────────────────────────
// Markdown modal
// ────────────────────────────────────────────────────────────────────────────

function _openMarkdownModal(currentValue, onSave) {
  const overlay = document.createElement('div')
  Object.assign(overlay.style, {
    position: 'fixed', inset: '0', zIndex: '99999',
    background: 'rgba(0,0,0,0.75)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    padding: '24px',
  })

  const panel = document.createElement('div')
  Object.assign(panel.style, {
    background: '#fff', borderRadius: '8px',
    width: '100%', maxWidth: '800px',
    display: 'flex', flexDirection: 'column',
    gap: '12px', padding: '24px',
    boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
    maxHeight: '90vh',
  })

  const label = document.createElement('p')
  label.textContent = 'Edit body markdown (Cmd/Ctrl+Enter to save, Esc to cancel)'
  Object.assign(label.style, { margin: '0', fontSize: '13px', color: '#555' })

  const textarea = document.createElement('textarea')
  textarea.value = currentValue
  Object.assign(textarea.style, {
    width: '100%', flex: '1', minHeight: '400px',
    fontFamily: 'monospace', fontSize: '14px', lineHeight: '1.6',
    padding: '12px', border: '1px solid #ccc', borderRadius: '4px',
    resize: 'vertical', boxSizing: 'border-box',
    color: '#1a1a1a', background: '#fafafa',
  })

  const btnRow = document.createElement('div')
  Object.assign(btnRow.style, { display: 'flex', gap: '8px', justifyContent: 'flex-end' })

  const cancelBtn = _makeBtn('Cancel', '#6b7280', () => overlay.remove())
  const saveBtn = _makeBtn('Save', '#2563eb', async () => {
    overlay.remove()
    await onSave(textarea.value)
  })

  btnRow.appendChild(cancelBtn)
  btnRow.appendChild(saveBtn)
  panel.appendChild(label)
  panel.appendChild(textarea)
  panel.appendChild(btnRow)
  overlay.appendChild(panel)
  document.body.appendChild(overlay)
  textarea.focus()

  // Keyboard shortcuts
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { overlay.remove() }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      overlay.remove()
      onSave(textarea.value)
    }
  })

  // Click outside panel to cancel
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove()
  })
}

// ────────────────────────────────────────────────────────────────────────────
// Tags editor (floating panel)
// ────────────────────────────────────────────────────────────────────────────

function _openTagsEditor(anchorEl, currentTags, onSave) {
  const tags = [...currentTags]
  const { container: panel, close } = openFloatingPanel(anchorEl, { className: '__cms-tags-panel' })

  function render() {
    panel.innerHTML = ''

    const chipsRow = document.createElement('div')
    Object.assign(chipsRow.style, { display: 'flex', flexWrap: 'wrap', gap: '6px' })

    tags.forEach((tag, i) => {
      const chip = document.createElement('span')
      chip.style.cssText = 'display:inline-flex;align-items:center;gap:4px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:999px;padding:2px 10px;font-size:13px;color:#1d4ed8;'
      chip.textContent = `#${tag}`

      const removeBtn = document.createElement('button')
      removeBtn.textContent = '✕'
      removeBtn.style.cssText = 'background:none;border:none;cursor:pointer;font-size:11px;color:#6b7280;padding:0;line-height:1;'
      removeBtn.addEventListener('click', () => {
        tags.splice(i, 1)
        render()
      })
      chip.appendChild(removeBtn)
      chipsRow.appendChild(chip)
    })
    panel.appendChild(chipsRow)

    const inputRow = document.createElement('div')
    Object.assign(inputRow.style, { display: 'flex', gap: '6px' })

    const input = document.createElement('input')
    input.type = 'text'
    input.placeholder = 'Add tag…'
    input.style.cssText = 'flex:1;padding:4px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:13px;outline:none;'

    const addBtn = _makeBtn('Add', '#2563eb', () => {
      const val = input.value.trim().replace(/^#+/, '')
      if (val && !tags.includes(val)) { tags.push(val); render() }
      else input.focus()
    })
    addBtn.style.padding = '4px 10px'
    addBtn.style.fontSize = '13px'

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { addBtn.click(); e.preventDefault() }
    })

    inputRow.appendChild(input)
    inputRow.appendChild(addBtn)
    panel.appendChild(inputRow)

    const btnRow = document.createElement('div')
    Object.assign(btnRow.style, { display: 'flex', gap: '6px', justifyContent: 'flex-end', borderTop: '1px solid #f3f4f6', paddingTop: '8px' })

    const cancelBtn = _makeBtn('Cancel', '#6b7280', () => close())
    cancelBtn.style.padding = '4px 10px'
    cancelBtn.style.fontSize = '13px'

    const saveBtn = _makeBtn('Save', '#2563eb', async () => {
      close()
      await onSave([...tags])
    })
    saveBtn.style.padding = '4px 10px'
    saveBtn.style.fontSize = '13px'

    btnRow.appendChild(cancelBtn)
    btnRow.appendChild(saveBtn)
    panel.appendChild(btnRow)

    input.focus()
  }

  render()
}

// ────────────────────────────────────────────────────────────────────────────
// Image editor
// ────────────────────────────────────────────────────────────────────────────

function _openImageEditor(anchorEl, currentValue, CONFIG, onSave) {
  if (CONFIG.mediaBase) {
    // Use mediakit iframe picker
    const overlay = document.createElement('div')
    Object.assign(overlay.style, {
      position: 'fixed', inset: '0', zIndex: '99999',
      background: 'rgba(0,0,0,0.75)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    })

    const closeOverlay = () => {
      overlay.remove()
      window.removeEventListener('message', onMessage)
    }

    const closeBtn = document.createElement('button')
    closeBtn.textContent = '✕'
    closeBtn.style.cssText = 'position:absolute;top:16px;right:16px;background:#fff;border:none;cursor:pointer;font-size:20px;width:36px;height:36px;border-radius:50%;z-index:1;'
    closeBtn.addEventListener('click', closeOverlay)

    const iframe = document.createElement('iframe')
    iframe.src = `${CONFIG.mediaBase}/admin?picker=1`
    Object.assign(iframe.style, { width: '90vw', height: '85vh', border: 'none', borderRadius: '8px' })

    overlay.appendChild(closeBtn)
    overlay.appendChild(iframe)
    document.body.appendChild(overlay)

    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeOverlay() })

    function onMessage(event) {
      const data = event.data
      if (!data) return
      const type = typeof data === 'string' ? data : data.type
      if (type === 'mediakit:asset-selected') {
        const asset = typeof data === 'object' ? data : {}
        closeOverlay()
        onSave({ src: asset.url || asset.key || '', alt: asset.alt_text || '' })
      } else if (type === 'mediakit:picker-cancelled') {
        closeOverlay()
      }
    }
    window.addEventListener('message', onMessage)
  } else {
    // Fallback: small URL + alt form modal
    const overlay = document.createElement('div')
    Object.assign(overlay.style, {
      position: 'fixed', inset: '0', zIndex: '99999',
      background: 'rgba(0,0,0,0.75)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '24px',
    })

    const panel = document.createElement('div')
    Object.assign(panel.style, {
      background: '#fff', borderRadius: '8px',
      width: '100%', maxWidth: '480px',
      padding: '24px', display: 'flex', flexDirection: 'column', gap: '12px',
      boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
    })

    const title = document.createElement('p')
    title.textContent = 'Update hero image'
    Object.assign(title.style, { margin: '0', fontWeight: '600', fontSize: '15px' })

    const srcInput = _makeTextInput('Image URL', currentValue.src || '')
    const altInput = _makeTextInput('Alt text', currentValue.alt || '')

    const btnRow = document.createElement('div')
    Object.assign(btnRow.style, { display: 'flex', gap: '8px', justifyContent: 'flex-end' })

    const cancelBtn = _makeBtn('Cancel', '#6b7280', () => overlay.remove())
    const saveBtn = _makeBtn('Save', '#2563eb', async () => {
      overlay.remove()
      await onSave({ src: srcInput.value.trim(), alt: altInput.value.trim() })
    })

    btnRow.appendChild(cancelBtn)
    btnRow.appendChild(saveBtn)

    panel.appendChild(title)
    panel.appendChild(srcInput)
    panel.appendChild(altInput)
    panel.appendChild(btnRow)
    overlay.appendChild(panel)
    document.body.appendChild(overlay)
    srcInput.focus()

    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove() })
    panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') overlay.remove() })
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Small DOM helpers
// ────────────────────────────────────────────────────────────────────────────

function _makeBtn(text, bgColor, onClick) {
  const btn = document.createElement('button')
  btn.textContent = text
  btn.style.cssText = `background:${bgColor};color:#fff;border:none;border-radius:4px;padding:8px 16px;font-size:14px;cursor:pointer;font-weight:500;`
  btn.addEventListener('click', onClick)
  return btn
}

function _makeTextInput(placeholder, value) {
  const input = document.createElement('input')
  input.type = 'text'
  input.placeholder = placeholder
  input.value = value
  input.style.cssText = 'width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:4px;font-size:14px;box-sizing:border-box;outline:none;'
  return input
}
