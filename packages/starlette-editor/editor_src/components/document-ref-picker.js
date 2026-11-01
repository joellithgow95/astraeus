/**
 * components/document-ref-picker.js — Choose a referenced document.
 *
 * A DocumentRef stores the target's id. Until now the editor rendered that as
 * a plain text input, so setting one meant finding a document id by hand and
 * typing it — and a typo produced a validation failure at save time rather
 * than at the point of choosing.
 *
 * `DocumentRef(block_type=...)` publishes `ref_block_type` in the field meta,
 * which is enough to list the candidates. An untyped ref has no list to offer,
 * so it keeps the text input.
 */

import { fetchDocuments } from '../api.js'
import { docTitle, el } from '../utils.js'

/**
 * Build the control for a document_ref field.
 *
 * Returns a select that fills in asynchronously: the options need a request,
 * and blocking the whole form render on it would be worse than a control that
 * is briefly disabled.
 *
 * @param {string}   name          Field name.
 * @param {object}   meta          cms:field_meta for the field.
 * @param {*}        currentValue  Currently referenced document id, or null.
 * @param {boolean}  isRequired
 * @param {(name: string, value: *) => void} onFieldChange
 * @returns {HTMLElement}
 */
export function buildDocumentRefPicker(name, meta, currentValue, isRequired, onFieldChange) {
  const targetType = meta?.ref_block_type

  // No declared target type means no list worth offering.
  if (!targetType) {
    return el('input', {
      class: 'field-input',
      id: `field-${name}`,
      type: 'text',
      value: currentValue ?? '',
      placeholder: 'Document ID',
      oninput: e => onFieldChange(name, e.target.value || null),
    })
  }

  const sel = el('select', {
    class: 'field-select',
    id: `field-${name}`,
    disabled: 'disabled',
    onchange: e => onFieldChange(name, e.target.value || null),
  })
  sel.appendChild(el('option', { value: '' }, 'Loading…'))

  fetchDocuments(targetType)
    .then(result => {
      const documents = result.documents || []
      sel.innerHTML = ''

      if (!isRequired) sel.appendChild(el('option', { value: '' }, '— None —'))

      for (const doc of documents) {
        const opt = el('option', { value: doc.id }, docTitle(doc, meta.ref_label_field))
        if (currentValue === doc.id) opt.setAttribute('selected', 'true')
        sel.appendChild(opt)
      }

      // A reference to something deleted or filtered out would otherwise vanish
      // silently and be saved away on the next write.
      if (currentValue && !documents.some(d => d.id === currentValue)) {
        const orphan = el('option', { value: currentValue }, `${currentValue} (missing)`)
        orphan.setAttribute('selected', 'true')
        sel.appendChild(orphan)
      }

      if (!documents.length && !currentValue) {
        sel.innerHTML = ''
        sel.appendChild(el('option', { value: '' }, `No ${targetType} documents yet`))
        return
      }

      sel.removeAttribute('disabled')
    })
    .catch(() => {
      // Fall back to the id rather than leaving a control that cannot be used.
      sel.innerHTML = ''
      sel.appendChild(el('option', { value: currentValue ?? '' }, currentValue ?? 'Unavailable'))
    })

  return sel
}
