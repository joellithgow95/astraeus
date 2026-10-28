/**
 * components/block-canvas.js — ListField/BlockField interactive card canvas.
 */

import { el, humanizeFieldName, getDefaultValue } from '../utils.js'
import { buildDocumentRefList } from './document-ref-list.js'
import { buildDocumentRefPicker } from './document-ref-picker.js'
import { buildDateInput, fieldWidget } from './fields.js'
import { buildTagInput, buildTagsInput } from './tags-input.js'
import { state } from '../state.js'

/**
 * Extract block type metadata for a list/block field from the JSON Schema.
 * Returns [{name, registeredType, schema}].
 *
 * Handles both homogeneous ($ref) and polymorphic (anyOf) lists.
 *
 * @param {object} prop       — JSON Schema property
 * @param {object} typeSchema — parent document schema (for $defs resolution)
 * @returns {Array<{name: string, registeredType: string, schema: object}>}
 */
export function getBlockTypesMeta(prop, typeSchema) {
  const defs = typeSchema?.$defs || {};
  const result = [];

  function resolveRef(ref) {
    const name = ref?.replace(/^#\/\$defs\//, '');
    return name ? defs[name] : null;
  }

  function extractEntry(className, schemaDef) {
    if (!schemaDef) return null;
    const constProp = schemaDef?.properties?.block_type?.const;
    return {
      name: className,
      registeredType: constProp || className,
      schema: schemaDef,
    };
  }

  // An optional list is `anyOf: [{type: 'array', items}, {type: 'null'}]`.
  const listSchema = prop?.items ? prop : (prop?.anyOf || []).find(v => v?.items);
  const items = listSchema?.items;
  if (!items) return result;
  // Pydantic writes a discriminated union (ListField(blocks=[...])) as oneOf.
  const variants = items.anyOf || items.oneOf;

  if (items.$ref) {
    // Homogeneous
    const className = items.$ref.replace(/^#\/\$defs\//, '');
    const entry = extractEntry(className, resolveRef(items.$ref));
    if (entry) result.push(entry);
  } else if (Array.isArray(variants)) {
    // Polymorphic
    for (const variant of variants) {
      if (variant.$ref) {
        const className = variant.$ref.replace(/^#\/\$defs\//, '');
        const entry = extractEntry(className, resolveRef(variant.$ref));
        if (entry) result.push(entry);
      }
    }
  } else if (items.properties) {
    // Inline schema — treat as single anonymous block type
    result.push({ name: 'Block', registeredType: 'block', schema: items });
  }

  return result;
}

/**
 * Create a new empty block and append it to the field array.
 *
 * @param {string} fieldName
 * @param {{registeredType: string, schema: object}} typeInfo
 * @param {function} render — trigger re-render
 */
export function addBlock(fieldName, typeInfo, render) {
  const current = Array.isArray(state.formData[fieldName]) ? [...state.formData[fieldName]] : [];
  const defaults = {};
  const props = typeInfo.schema?.properties || {};
  for (const [k, v] of Object.entries(props)) {
    if (k === 'block_type') continue;
    if (v.type === 'boolean') defaults[k] = false;
    else if (v.type === 'number' || v.type === 'integer') defaults[k] = null;
    else if (v.type === 'array') defaults[k] = [];
    else if (v.type === 'object') defaults[k] = {};
    else defaults[k] = '';
  }
  current.push({ block_type: typeInfo.registeredType, ...defaults });
  openSet(fieldName).add(current.length - 1);  // a new block opens, ready to fill in
  state.formData = { ...state.formData, [fieldName]: current };
  state.isDirty = true;
  render();
}

/**
 * Remove the block at the given index from the field array.
 *
 * @param {string} fieldName
 * @param {number} index
 * @param {function} render
 */
export function removeBlock(fieldName, index, render) {
  const current = Array.isArray(state.formData[fieldName]) ? [...state.formData[fieldName]] : [];
  current.splice(index, 1);
  reindexOpen(fieldName, index, -1);
  state.formData = { ...state.formData, [fieldName]: current };
  state.isDirty = true;
  render();
}

/**
 * Move a block from one index to another within the field array.
 *
 * @param {string} fieldName
 * @param {number} fromIndex
 * @param {number} toIndex
 * @param {function} render
 */
export function moveBlock(fieldName, fromIndex, toIndex, render) {
  if (fromIndex === toIndex) return;
  const current = Array.isArray(state.formData[fieldName]) ? [...state.formData[fieldName]] : [];
  const [item] = current.splice(fromIndex, 1);
  current.splice(toIndex, 0, item);
  reindexOpen(fieldName, fromIndex, toIndex);
  state.formData = { ...state.formData, [fieldName]: current };
  state.isDirty = true;
  render();
}

/**
 * Update a single field within a block at the given index.
 * Does a partial DOM update only (no full re-render) to preserve focus.
 *
 * @param {string} fieldName
 * @param {number} index
 * @param {string} blockFieldName
 * @param {*} value
 */
export function updateBlockField(fieldName, index, blockFieldName, value) {
  const current = Array.isArray(state.formData[fieldName]) ? [...state.formData[fieldName]] : [];
  current[index] = { ...current[index], [blockFieldName]: value };
  state.formData = { ...state.formData, [fieldName]: current };
  state.isDirty = true;
  // Only update the dirty indicator — no full re-render to preserve focus
  const dot = document.getElementById('dirty-dot');
  if (dot) dot.classList.add('is-visible');
}

/**
 * Build the block type picker dropdown for a field.
 *
 * @param {string} fieldName
 * @param {Array} availableTypes
 * @param {function} render
 * @returns {HTMLElement}
 */
function buildBlockTypePicker(fieldName, availableTypes, render) {
  const picker = el('div', {
    class: 'block-type-picker',
    role: 'listbox',
    'aria-label': 'Select block type',
  });
  picker.style.display = 'none';

  for (const typeInfo of availableTypes) {
    const item = el('div', {
      class: 'block-type-picker__item',
      role: 'option',
      tabindex: '-1',
      onclick: () => {
        addBlock(fieldName, typeInfo, render);
        picker.style.display = 'none';
      },
      onkeydown: e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          addBlock(fieldName, typeInfo, render);
          picker.style.display = 'none';
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          const next = item.nextElementSibling;
          if (next) next.focus();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const prev = item.previousElementSibling;
          if (prev) prev.focus();
          else picker.previousElementSibling?.focus();
        } else if (e.key === 'Escape') {
          picker.style.display = 'none';
          picker.previousElementSibling?.focus();
        }
      },
    },
      el('span', { class: 'block-type-picker__name' }, humanizeFieldName(typeInfo.registeredType)),
      typeInfo.schema?.description
        ? el('span', { class: 'block-type-picker__description' }, typeInfo.schema.description)
        : null,
    );
    picker.appendChild(item);
  }

  return picker;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** A block's fields (block_type aside), in display order, each with its cms:field_meta. */
function blockFields(blockSchema) {
  return Object.entries(blockSchema?.properties || {})
    .filter(([k]) => k !== 'block_type')
    .map(([k, prop]) => [k, prop, prop['cms:field_meta'] || {}])
    .sort((a, b) => (a[2].display_order ?? a[1].display_order ?? 9999) - (b[2].display_order ?? b[1].display_order ?? 9999))
}

/**
 * One line saying what a block holds, for its collapsed card: a slice reads
 * "2026-10-01 – 2026-10-31", "Songs: 3" or "developer-blog" without being
 * opened. Dates run together as a range; picked references are counted;
 * other short text is shown as it is. '' when nothing is filled in.
 *
 * @param {object} blockData
 * @param {object} blockSchema
 * @returns {string}
 */
export function blockSummary(blockData, blockSchema) {
  const parts = []
  const dates = []
  for (const [k, prop, meta] of blockFields(blockSchema)) {
    const v = blockData?.[k]
    if (meta.field_type === 'date' || (typeof v === 'string' && ISO_DATE.test(v))) {
      if (typeof v === 'string' && v) dates.push(v)
    } else if (Array.isArray(v)) {
      if (v.length) parts.push(`${meta.label || humanizeFieldName(k)}: ${v.length}`)
    } else if (typeof v === 'string' && v.trim() && meta.field_type !== 'document_ref') {
      const text = v.trim()
      parts.push(text.length > 40 ? `${text.slice(0, 39)}…` : text)
    }
  }
  if (dates.length) parts.unshift(dates.join(' – '))
  return parts.join(' · ')
}

/**
 * Which cards are open, per field, by index. Adding, moving or removing a
 * block re-renders the canvas, which used to fold every card shut, so a
 * slice you had just added opened closed and had to be found again.
 */
const openCards = new Map()

// Keyed by document too, so opening another post does not inherit them.
const openKey = fieldName => `${state.activeDocId ?? 'new'}|${fieldName}`

function openSet(fieldName) {
  const key = openKey(fieldName)
  if (!openCards.has(key)) openCards.set(key, new Set())
  return openCards.get(key)
}

/** Shift the open indices after a block moves from `from` to `to` (to = -1 removes it). */
function reindexOpen(fieldName, from, to) {
  const order = [...Array(Math.max(from, to, ...openSet(fieldName)) + 1).keys()]
  const [moved] = order.splice(from, 1)
  if (to >= 0) order.splice(to, 0, moved)
  const next = new Set()
  order.forEach((old, i) => { if (openSet(fieldName).has(old)) next.add(i) })
  openCards.set(openKey(fieldName), next)
}

/**
 * The control for one field inside a block card. Reads the field's own
 * cms:field_meta, so a date gets a date picker, a TagField its suggestions
 * and a reference list its search, as they would at the top level.
 *
 * @returns {HTMLElement}
 */
function buildNestedInput(id, prop, meta, value, onChange) {
  const ft = meta.field_type
  if (ft === 'document_ref_list') return buildDocumentRefList(id, meta, value, (_n, v) => onChange(v), { inputId: id })
  if (ft === 'document_ref') {
    const picker = buildDocumentRefPicker(id, meta, value, false, (_n, v) => onChange(v))
    picker.id = id
    return picker
  }
  if (ft === 'date') return buildDateInput(id, value, onChange)
  if (ft === 'tag') return buildTagInput({ id, value, meta, onChange })
  if (ft === 'tags') return buildTagsInput({ id, value, onChange })

  let widget = fieldWidget(id, prop, meta)
  // No nested canvases or rich text inside a card: edit those as JSON.
  if (['block_canvas', 'prosemirror', 'links'].includes(widget)) widget = 'json'
  const choices = meta.choices || prop.enum || []

  switch (widget) {
    case 'textarea': {
      const ta = el('textarea', { class: 'field-textarea', id, placeholder: meta.placeholder || '', oninput: e => onChange(e.target.value) })
      ta.value = value || ''
      return ta
    }
    case 'boolean': {
      const toggleLabel = el('label', { class: 'toggle-switch', for: id })
      const input = el('input', { type: 'checkbox', id, onchange: e => onChange(e.target.checked) })
      if (value) input.setAttribute('checked', 'true')
      toggleLabel.appendChild(input)
      toggleLabel.appendChild(el('span', { class: 'toggle-track' }))
      toggleLabel.appendChild(el('span', { class: 'toggle-thumb' }))
      return el('div', { class: 'field-bool' }, toggleLabel, el('span', { class: 'field-bool__label' }, meta.label || ''))
    }
    case 'number':
      return el('input', {
        class: 'field-input', id, type: 'number',
        value: value != null ? String(value) : '',
        oninput: e => onChange(e.target.value === '' ? null : Number(e.target.value)),
      })
    case 'select': {
      const sel = el('select', { class: 'field-select', id, onchange: e => onChange(e.target.value) })
      for (const choice of choices) {
        const opt = el('option', { value: choice }, choice)
        if (value === choice) opt.setAttribute('selected', 'true')
        sel.appendChild(opt)
      }
      return sel
    }
    case 'json': {
      const ta = el('textarea', {
        class: 'field-textarea field-textarea--json', id,
        oninput: e => { try { onChange(JSON.parse(e.target.value)) } catch { /* ignore */ } },
      })
      ta.value = value != null ? JSON.stringify(value, null, 2) : ''
      return ta
    }
    default: // input / image_picker → plain input in nested context
      return el('input', {
        class: 'field-input', id, type: 'text',
        placeholder: meta.placeholder || '',
        value: typeof value === 'string' ? value : (value != null ? String(value) : ''),
        oninput: e => onChange(e.target.value),
      })
  }
}

/**
 * Build a single block card element.
 *
 * @param {string} fieldName
 * @param {object} blockData
 * @param {object} blockSchema
 * @param {number} index
 * @param {number} total          — block count, for disabling the move controls at the ends
 * @param {Array}  availableTypes
 * @param {function} render
 * @returns {HTMLElement}
 */
function buildBlockCard(fieldName, blockData, blockSchema, index, total, availableTypes, render) {
  const card = el('div', { class: `block-card${openSet(fieldName).has(index) ? ' is-open' : ''}` });

  // Blocks are an ordered sequence, so the position is real information rather
  // than decoration — it doubles as the label for the move controls.
  const position = el('span', { class: 'block-card__position' }, String(index + 1));

  const moveUp = el('button', {
    class: 'block-card__move',
    type: 'button',
    title: 'Move up',
    'aria-label': `Move block ${index + 1} up`,
    disabled: index === 0 ? 'disabled' : null,
    onclick: e => { e.stopPropagation(); moveBlock(fieldName, index, index - 1, render); },
  }, '↑');

  const moveDown = el('button', {
    class: 'block-card__move',
    type: 'button',
    title: 'Move down',
    'aria-label': `Move block ${index + 1} down`,
    disabled: index === total - 1 ? 'disabled' : null,
    onclick: e => { e.stopPropagation(); moveBlock(fieldName, index, index + 1, render); },
  }, '↓');

  const summary = el('span', { class: 'block-card__summary' }, blockSummary(blockData, blockSchema));

  // Header
  const header = el('div', { class: 'block-card__header', title: 'Click to expand/collapse' },
    position,
    el('span', { class: 'block-card__heading' },
      el('span', { class: 'block-card__type-label' }, humanizeFieldName(blockData.block_type || 'Block')),
      summary,
    ),
    el('div', { class: 'block-card__controls' },
      moveUp,
      moveDown,
      el('button', {
        class: 'block-card__delete',
        type: 'button',
        title: 'Remove block',
        'aria-label': `Remove block ${index + 1}`,
        onclick: e => { e.stopPropagation(); removeBlock(fieldName, index, render); },
      }, '✕')
    )
  );
  card.appendChild(header);

  // Body (collapsible)
  const body = el('div', { class: 'block-card__body' });

  // The block's docstring says what it does ("Spotify likes whose liked_at
  // falls between ..."), which the type name alone does not.
  if (blockSchema?.description) {
    body.appendChild(el('p', { class: 'field-help block-card__description' }, blockSchema.description));
  }

  const required = blockSchema?.required || [];
  for (const [bFieldName, bProp, bMeta] of blockFields(blockSchema)) {
    const id = `block-${fieldName}-${index}-${bFieldName}`;
    const bLabel = bMeta.label || humanizeFieldName(bFieldName);
    const bVal = blockData[bFieldName] ?? getDefaultValue(bProp, bMeta);
    const onChange = value => {
      updateBlockField(fieldName, index, bFieldName, value);
      summary.textContent = blockSummary(state.formData[fieldName]?.[index], blockSchema);
    };

    const bGroup = el('div', { class: 'field-group field-group--nested' });
    const labelEl = el('label', {
      class: `field-label${required.includes(bFieldName) ? ' field-label--required' : ''}`,
      for: id,
    }, bLabel);
    bGroup.appendChild(labelEl);
    const input = buildNestedInput(id, bProp, { ...bMeta, label: bLabel }, bVal, onChange);
    if (input.classList.contains('field-bool')) labelEl.hidden = true;  // the toggle carries its own label
    bGroup.appendChild(input);
    if (bMeta.help_text) bGroup.appendChild(el('span', { class: 'field-help' }, bMeta.help_text));
    body.appendChild(bGroup);
  }

  card.appendChild(body);

  // Toggle open/collapse on header click
  header.addEventListener('click', e => {
    if (e.target.closest('.block-card__controls')) return;
    const open = card.classList.toggle('is-open');
    if (open) openSet(fieldName).add(index);
    else openSet(fieldName).delete(index);
  });

  return card;
}

/**
 * Build the full block canvas widget for a ListField/BlockField.
 *
 * @param {string}   fieldName
 * @param {object}   prop
 * @param {object}   meta
 * @param {object}   typeSchema    — parent document JSON Schema (for $defs)
 * @param {*}        currentValue
 * @param {function} render        — trigger full re-render
 * @returns {HTMLElement}
 */
export function buildBlockCanvas(fieldName, prop, meta, typeSchema, currentValue, render) {
  const availableTypes = getBlockTypesMeta(prop, typeSchema);
  const blocks = Array.isArray(currentValue) ? currentValue : (currentValue ? [currentValue] : []);

  const canvas = el('div', { class: 'block-canvas' });

  // Render existing block cards
  for (let i = 0; i < blocks.length; i++) {
    const blockData = blocks[i];
    const typeInfo = availableTypes.find(t => t.registeredType === blockData.block_type)
      || availableTypes[0];
    const blockSchema = typeInfo?.schema || {};
    const card = buildBlockCard(fieldName, blockData, blockSchema, i, blocks.length, availableTypes, render);
    canvas.appendChild(card);
  }

  // "Add block" section
  const addWrap = el('div', { class: 'block-canvas__add-wrap' });

  if (availableTypes.length === 1) {
    // Single type — direct add button, no picker
    const addBtn = el('button', {
      class: 'block-canvas__add btn btn--ghost',
      onclick: () => addBlock(fieldName, availableTypes[0], render),
    }, '+ Add block');
    addWrap.appendChild(addBtn);
  } else if (availableTypes.length > 1) {
    // Multiple types — show picker dropdown
    const picker = buildBlockTypePicker(fieldName, availableTypes, render);
    const addBtn = el('button', {
      class: 'block-canvas__add btn btn--ghost',
      'aria-haspopup': 'listbox',
      onclick: () => {
        picker.style.display = picker.style.display === 'none' ? 'block' : 'none';
        if (picker.style.display === 'block') {
          const first = picker.querySelector('.block-type-picker__item');
          if (first) first.focus();
        }
      },
      onkeydown: e => {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          picker.style.display = 'block';
          const first = picker.querySelector('.block-type-picker__item');
          if (first) first.focus();
        }
      },
    }, '+ Add block ▾');
    addWrap.appendChild(addBtn);
    addWrap.appendChild(picker);

    // Close picker when clicking outside
    document.addEventListener('click', e => {
      if (!addWrap.contains(e.target)) {
        picker.style.display = 'none';
      }
    }, { capture: false });
  }

  if (availableTypes.length > 0) {
    canvas.appendChild(addWrap);
  }

  return canvas;
}
