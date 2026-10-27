/**
 * components/block-canvas.js — ListField/BlockField interactive card canvas.
 */

import { el, humanizeFieldName, getDefaultValue } from '../utils.js'
import { buildDocumentRefList } from './document-ref-list.js'
import { buildDocumentRefPicker } from './document-ref-picker.js'
import { fieldWidget } from './fields.js'
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
    }, humanizeFieldName(typeInfo.registeredType));
    picker.appendChild(item);
  }

  return picker;
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
  const card = el('div', { class: 'block-card' });

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

  // Header
  const header = el('div', { class: 'block-card__header', title: 'Click to expand/collapse' },
    position,
    el('span', { class: 'block-card__type-label' }, humanizeFieldName(blockData.block_type || 'Block')),
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

  const props = blockSchema?.properties || {};
  const orderedEntries = Object.entries(props)
    .filter(([k]) => k !== 'block_type')
    .sort((a, b) => {
      const oa = a[1].display_order ?? 9999;
      const ob = b[1].display_order ?? 9999;
      return oa - ob;
    });

  for (const [bFieldName, bProp] of orderedEntries) {
    const bMeta = {};  // block schema fields don't carry field_meta in this context
    const widget = fieldWidget(bFieldName, bProp, bMeta);
    const bLabel = humanizeFieldName(bFieldName);
    const bVal = blockData[bFieldName] ?? getDefaultValue(bProp, bMeta);

    const bGroup = el('div', { class: 'field-group field-group--nested' });
    bGroup.appendChild(el('label', { class: 'field-label', for: `block-${fieldName}-${index}-${bFieldName}` }, bLabel));

    // References are picked, not typed: a block's own field meta (in its
    // schema property) says what they point at, so a picked slice can search
    // its target type like a top-level field does.
    const refMeta = bProp['cms:field_meta'];
    const onRefChange = (_name, value) => updateBlockField(fieldName, index, bFieldName, value);
    if (refMeta?.field_type === 'document_ref_list') {
      bGroup.appendChild(buildDocumentRefList(`${fieldName}-${index}-${bFieldName}`, refMeta, bVal, onRefChange,
        { inputId: `block-${fieldName}-${index}-${bFieldName}` }));
      body.appendChild(bGroup);
      continue;
    }
    if (refMeta?.field_type === 'document_ref') {
      const picker = buildDocumentRefPicker(`${fieldName}-${index}-${bFieldName}`, refMeta, bVal, false, onRefChange);
      picker.id = `block-${fieldName}-${index}-${bFieldName}`;
      bGroup.appendChild(picker);
      body.appendChild(bGroup);
      continue;
    }

    // Render scalar widgets inline (no recursive block_canvas for nested blocks)
    switch (widget) {
      case 'textarea': {
        const ta = el('textarea', {
          class: 'field-textarea',
          id: `block-${fieldName}-${index}-${bFieldName}`,
          placeholder: '',
          oninput: e => updateBlockField(fieldName, index, bFieldName, e.target.value),
        });
        ta.value = bVal || '';
        bGroup.appendChild(ta);
        break;
      }
      case 'boolean': {
        const toggleLabel = el('label', { class: 'toggle-switch', for: `block-${fieldName}-${index}-${bFieldName}` });
        const input = el('input', {
          type: 'checkbox',
          id: `block-${fieldName}-${index}-${bFieldName}`,
          onchange: e => updateBlockField(fieldName, index, bFieldName, e.target.checked),
        });
        if (bVal) input.setAttribute('checked', 'true');
        toggleLabel.appendChild(input);
        toggleLabel.appendChild(el('span', { class: 'toggle-track' }));
        toggleLabel.appendChild(el('span', { class: 'toggle-thumb' }));
        bGroup.appendChild(el('div', { class: 'field-bool' }, toggleLabel, el('span', { class: 'field-bool__label' }, bLabel)));
        break;
      }
      case 'number': {
        const input = el('input', {
          class: 'field-input',
          id: `block-${fieldName}-${index}-${bFieldName}`,
          type: 'number',
          value: bVal != null ? String(bVal) : '',
          oninput: e => updateBlockField(fieldName, index, bFieldName, e.target.value === '' ? null : Number(e.target.value)),
        });
        bGroup.appendChild(input);
        break;
      }
      case 'select': {
        const sel = el('select', {
          class: 'field-select',
          id: `block-${fieldName}-${index}-${bFieldName}`,
          onchange: e => updateBlockField(fieldName, index, bFieldName, e.target.value),
        });
        for (const choice of (bMeta.choices || [])) {
          const opt = el('option', { value: choice }, choice);
          if (bVal === choice) opt.setAttribute('selected', 'true');
          sel.appendChild(opt);
        }
        bGroup.appendChild(sel);
        break;
      }
      case 'json': {
        const ta = el('textarea', {
          class: 'field-textarea field-textarea--json',
          id: `block-${fieldName}-${index}-${bFieldName}`,
          oninput: e => {
            try { updateBlockField(fieldName, index, bFieldName, JSON.parse(e.target.value)); } catch { /* ignore */ }
          },
        });
        ta.value = bVal != null ? JSON.stringify(bVal, null, 2) : '';
        bGroup.appendChild(ta);
        break;
      }
      default: { // input / image_picker / prosemirror → plain input in nested context
        const input = el('input', {
          class: 'field-input',
          id: `block-${fieldName}-${index}-${bFieldName}`,
          type: 'text',
          placeholder: '',
          value: typeof bVal === 'string' ? bVal : (bVal != null ? String(bVal) : ''),
          oninput: e => updateBlockField(fieldName, index, bFieldName, e.target.value),
        });
        bGroup.appendChild(input);
        break;
      }
    }

    body.appendChild(bGroup);
  }

  card.appendChild(body);

  // Toggle open/collapse on header click
  header.addEventListener('click', e => {
    if (e.target.closest('.block-card__controls')) return;
    card.classList.toggle('is-open');
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
