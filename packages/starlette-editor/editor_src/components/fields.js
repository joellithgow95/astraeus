/**
 * components/fields.js — Field widget dispatch and form field builders.
 */

import { humanizeFieldName, getDefaultValue, $ as domId, el } from '../utils.js'
import { buildDocumentRefList } from './document-ref-list.js'
import { buildDocumentRefPicker } from './document-ref-picker.js'
import { buildLinksInput } from './links-input.js'
import { buildTagInput, buildTagsInput } from './tags-input.js'

/**
 * Determine what kind of UI widget to use for a field.
 *
 * Returns one of: 'prosemirror' | 'textarea' | 'input' | 'number'
 *                 | 'boolean' | 'select' | 'json' | 'block_canvas'
 *                 | 'image_picker' | 'document_ref' | 'document_ref_list'
 *                 | 'tags' | 'tag' | 'date' | 'links'
 *
 * Explicit field_type from cms:field_meta takes precedence over all
 * heuristics. Heuristic fallbacks are kept for schemas that pre-date
 * the field_type tag or for fields with no explicit type annotation.
 *
 * @param {string} name   — field name
 * @param {object} prop   — JSON Schema property definition
 * @param {object} meta   — field_meta entry
 * @returns {string}
 */
export function fieldWidget(name, prop, meta) {
  // --- Authoritative field_type from cms:field_meta (set by Python field classes) ---
  const ft = meta?.field_type;
  if (ft === 'rich_text')    return 'prosemirror';
  if (ft === 'block_list')   return 'block_canvas';
  if (ft === 'block')        return 'block_canvas';  // single nested block — same canvas, 1 card
  if (ft === 'image')        return 'image_picker';
  if (ft === 'select')       return 'select';
  if (ft === 'number')       return 'number';
  if (ft === 'boolean')      return 'boolean';
  if (ft === 'json')         return 'json';
  if (ft === 'document_ref') return 'document_ref';
  if (ft === 'document_ref_list') return 'document_ref_list';
  if (ft === 'tags')         return 'tags';
  if (ft === 'tag')          return 'tag';
  if (ft === 'date')         return 'date';
  if (ft === 'links')        return 'links';
  // url, text → fall through to heuristics below

  // --- Legacy heuristics (backwards compat for fields without field_type) ---
  // Explicit ProseMirror fields by name convention
  if (name === 'body_markdown' || name.includes('markdown')) return 'prosemirror';

  const type = prop.type;
  const anyOf = prop.anyOf;

  // SelectField has choices in meta
  if (meta?.choices?.length) return 'select';

  if (type === 'boolean') return 'boolean';
  if (type === 'number' || type === 'integer') return 'number';

  // JSON-like: object/array, or anyOf with null
  if (type === 'object' || type === 'array') return 'json';
  if (Array.isArray(anyOf)) {
    const types = anyOf.map(s => s.type).filter(Boolean);
    if (types.includes('object') || types.includes('array')) return 'json';
    if (types.includes('null') && !types.includes('string') && !types.includes('number')) return 'json';
  }

  // Long text heuristics
  const maxLen = prop.maxLength || meta?.max_length;
  const longNames = ['body', 'description', 'overview', 'summary', 'content', 'excerpt', 'bio', 'text'];
  const isLong = (maxLen && maxLen > 300) || longNames.some(n => name.includes(n));
  if (type === 'string' && isLong) return 'textarea';

  return 'input';
}

/**
 * A native date picker for a DateField. The value stays a `YYYY-MM-DD`
 * string, which is what `<input type="date">` reads and writes; cleared, it
 * is null, so an optional date is left out rather than stored as ''.
 *
 * @param {string} id
 * @param {*} value
 * @param {(value: string|null) => void} onChange
 * @returns {HTMLInputElement}
 */
export function buildDateInput(id, value, onChange) {
  return el('input', {
    class: 'field-input field-input--date',
    id,
    type: 'date',
    value: typeof value === 'string' ? value.slice(0, 10) : '',
    oninput: e => onChange(e.target.value || null),
  });
}

/**
 * Build the slug input field group.
 *
 * @param {object} state  — current app state
 * @param {function} onFieldChange
 * @returns {HTMLElement}
 */
export function buildSlugField(state, onFieldChange) {
  const group = el('div', { class: 'field-group' });
  group.appendChild(el('label', { class: 'field-label', for: 'field-slug' }, 'Slug'));
  const input = el('input', {
    class: 'field-input',
    id: 'field-slug',
    type: 'text',
    placeholder: 'my-document-slug',
    value: state.activeDoc?.slug || state.formData.__slug || '',
    oninput: e => onFieldChange('__slug', e.target.value),
  });
  group.appendChild(input);
  group.appendChild(el('span', { class: 'field-help' }, 'URL-safe identifier for this document'));
  return group;
}

/**
 * Build a ProseMirror editor placeholder div.
 * Actual mounting happens after DOM insertion via mountProseMirrorEditors().
 *
 * @param {string} name         — field name
 * @param {*}      currentValue — initial value (PM JSON doc or markdown string)
 * @returns {HTMLElement}
 */
export function buildProseMirrorPlaceholder(name, currentValue) {
  const wrap = el('div', { class: 'pm-editor-wrap' });
  const toolbar = el('div', { class: 'pm-toolbar', id: `pm-toolbar-${name}` },
    el('button', { class: 'pm-toolbar__btn', title: 'Bold', 'data-cmd': 'toggleBold' }, 'B'),
    el('button', { class: 'pm-toolbar__btn', title: 'Italic', 'data-cmd': 'toggleItalic' }, 'I'),
    el('button', { class: 'pm-toolbar__btn pm-toolbar__btn--mono', title: 'Inline code', 'data-cmd': 'toggleCode' }, '`'),
    el('span', { class: 'pm-toolbar__sep' }),
    el('button', { class: 'pm-toolbar__btn', title: 'Heading 1', 'data-cmd': 'h1' }, 'H1'),
    el('button', { class: 'pm-toolbar__btn', title: 'Heading 2', 'data-cmd': 'h2' }, 'H2'),
    el('span', { class: 'pm-toolbar__sep' }),
    el('button', { class: 'pm-toolbar__btn', title: 'Bullet list', 'data-cmd': 'bulletList' }, '•≡'),
    el('button', { class: 'pm-toolbar__btn', title: 'Ordered list', 'data-cmd': 'orderedList' }, '1≡'),
    el('span', { class: 'pm-toolbar__sep' }),
    el('button', { class: 'pm-toolbar__btn', title: 'Blockquote', 'data-cmd': 'blockquote' }, '❝'),
  );
  const editorDiv = el('div', { class: 'pm-editor', id: `pm-mount-${name}`, 'data-field': name });
  wrap.appendChild(toolbar);
  wrap.appendChild(editorDiv);
  return wrap;
}

/**
 * Build a single field group element (label + widget).
 *
 * @param {string}   name          — field name
 * @param {object}   prop          — JSON Schema property
 * @param {object}   meta          — field_meta entry
 * @param {object}   state         — current app state
 * @param {function} onFieldChange
 * @param {function} buildBlockCanvas    — passed in to avoid circular import
 * @param {function} buildImagePickerField
 * @returns {HTMLElement|null}
 */
export function buildFieldGroup(name, prop, meta, state, onFieldChange, buildBlockCanvas, buildImagePickerField) {
  const widget = fieldWidget(name, prop, meta);
  const label = meta.label || humanizeFieldName(name);
  const isRequired = (prop.required || []).includes(name) ||
    (state.schema?.[state.activeType]?.schema?.required || []).includes(name);

  const group = el('div', { class: 'field-group' });
  const labelEl = el('label', {
    class: `field-label${isRequired ? ' field-label--required' : ''}`,
    for: `field-${name}`,
  }, label);
  group.appendChild(labelEl);

  if (meta.help_text) {
    group.appendChild(el('span', { class: 'field-help' }, meta.help_text));
  }

  const currentVal = state.formData[name] ?? getDefaultValue(prop, meta);

  switch (widget) {
    case 'block_canvas':
      group.appendChild(buildBlockCanvas(name, prop, meta, state.schema?.[state.activeType]?.schema, currentVal));
      break;

    case 'image_picker':
      group.appendChild(buildImagePickerField(name, currentVal));
      break;

    case 'prosemirror':
      group.appendChild(buildProseMirrorPlaceholder(name, currentVal));
      break;

    case 'textarea': {
      const ta = el('textarea', {
        class: 'field-textarea',
        id: `field-${name}`,
        placeholder: meta.placeholder || '',
        oninput: e => onFieldChange(name, e.target.value),
      });
      ta.value = currentVal || '';
      group.appendChild(ta);
      break;
    }

    case 'json': {
      const ta = el('textarea', {
        class: 'field-textarea field-textarea--json',
        id: `field-${name}`,
        placeholder: '{}',
        onblur: e => {
          try {
            const pretty = JSON.stringify(JSON.parse(e.target.value), null, 2);
            e.target.value = pretty;
            onFieldChange(name, JSON.parse(pretty));
          } catch {
            // Leave as-is if invalid JSON — validation will surface it on save
          }
        },
        oninput: e => {
          try { onFieldChange(name, JSON.parse(e.target.value)); } catch { /* ignore */ }
        },
      });
      const rawVal = typeof currentVal === 'string' ? currentVal
        : currentVal != null ? JSON.stringify(currentVal, null, 2) : '';
      ta.value = rawVal;
      group.appendChild(ta);
      break;
    }

    case 'document_ref': {
      group.appendChild(
        buildDocumentRefPicker(name, meta, currentVal, isRequired, onFieldChange),
      );
      break;
    }

    case 'document_ref_list': {
      group.appendChild(buildDocumentRefList(name, meta, currentVal, onFieldChange));
      labelEl.setAttribute('for', `field-${name}-search`);
      break;
    }

    case 'tags': {
      group.appendChild(buildTagsInput({
        id: `field-${name}`,
        value: currentVal,
        docType: meta.suggest_from || state.activeType,
        field: meta.suggest_from ? (meta.suggest_field || 'tags') : name,
        // [] rather than null: a site schema such as zod's .optional() takes
        // a missing or empty list but not null.
        onChange: tags => onFieldChange(name, tags),
      }));
      break;
    }

    case 'tag': {
      group.appendChild(buildTagInput({
        id: `field-${name}`,
        value: currentVal,
        meta,
        onChange: tag => onFieldChange(name, tag),
      }));
      break;
    }

    case 'date': {
      group.appendChild(buildDateInput(`field-${name}`, currentVal, v => onFieldChange(name, v)));
      break;
    }

    case 'links': {
      group.appendChild(buildLinksInput(name, meta, currentVal, state.activeType, onFieldChange));
      break;
    }

    case 'select': {
      const sel = el('select', {
        class: 'field-select',
        id: `field-${name}`,
        onchange: e => onFieldChange(name, e.target.value),
      });
      if (!isRequired) sel.appendChild(el('option', { value: '' }, '— Select —'));
      for (const choice of (meta.choices || [])) {
        const opt = el('option', { value: choice }, choice);
        if (currentVal === choice) opt.setAttribute('selected', 'true');
        sel.appendChild(opt);
      }
      group.appendChild(sel);
      break;
    }

    case 'boolean': {
      const wrap = el('div', { class: 'field-bool' });
      const toggleLabel = el('label', { class: 'toggle-switch', for: `field-${name}` });
      const input = el('input', {
        type: 'checkbox',
        id: `field-${name}`,
        onchange: e => onFieldChange(name, e.target.checked),
      });
      if (currentVal) input.setAttribute('checked', 'true');
      const track = el('span', { class: 'toggle-track' });
      const thumb = el('span', { class: 'toggle-thumb' });
      toggleLabel.appendChild(input);
      toggleLabel.appendChild(track);
      toggleLabel.appendChild(thumb);
      wrap.appendChild(toggleLabel);
      wrap.appendChild(el('span', { class: 'field-bool__label' }, label));
      // Replace the label we added above since bool shows it inline
      labelEl.textContent = '';
      labelEl.style.display = 'none';
      group.appendChild(wrap);
      break;
    }

    case 'number': {
      const input = el('input', {
        class: 'field-input',
        id: `field-${name}`,
        type: 'number',
        placeholder: meta.placeholder || '0',
        value: currentVal != null ? String(currentVal) : '',
        oninput: e => onFieldChange(name, e.target.value === '' ? null : Number(e.target.value)),
      });
      if (meta.min_value != null) input.setAttribute('min', meta.min_value);
      if (meta.max_value != null) input.setAttribute('max', meta.max_value);
      group.appendChild(input);
      break;
    }

    default: { // 'input'
      const input = el('input', {
        class: 'field-input',
        id: `field-${name}`,
        type: 'text',
        placeholder: meta.placeholder || '',
        value: currentVal || '',
        oninput: e => onFieldChange(name, e.target.value),
      });
      if (prop.maxLength) input.setAttribute('maxlength', prop.maxLength);
      group.appendChild(input);
      break;
    }
  }

  return group;
}
