/**
 * components/image-picker.js — astraeus-media iframe picker widget.
 */

import { el } from '../utils.js'
import { showToast } from './toast.js'

const CONFIG = window.__EDITOR_CONFIG__ || { cmsBase: '', apiKey: null, mountPath: '/editor' };

/** Tracks which field the open picker modal is for. */
let currentPickerField = null;

/** Close and remove the picker modal. */
function closePickerModal() {
  const modal = document.querySelector('.picker-modal');
  if (modal) modal.remove();
  window.removeEventListener('message', onPickerMessage);
  currentPickerField = null;
}

/**
 * Handle postMessage events from the astraeus-media picker iframe.
 *
 * @param {MessageEvent} event
 */
function onPickerMessage(event) {
  const data = event.data;
  if (!data) return;
  const type = typeof data === 'string' ? data : data.type;
  if (type === 'astraeus-media:asset-selected') {
    const value = (typeof data === 'object' ? (data.key || data.url) : null) || '';
    if (currentPickerField) {
      // Dispatch a custom DOM event so callers can react without tight coupling
      document.dispatchEvent(new CustomEvent('editor:image-selected', {
        detail: { fieldName: currentPickerField, value },
      }));
      // Update the preview thumbnail synchronously (if it's visible)
      const preview = document.getElementById(`img-preview-${currentPickerField}`);
      if (preview && value) { preview.src = value; preview.style.display = ''; }
      else if (preview) preview.style.display = 'none';
      const valEl = document.getElementById(`img-val-${currentPickerField}`);
      if (valEl) valEl.value = value;
    }
    closePickerModal();
  } else if (type === 'astraeus-media:picker-cancelled') {
    closePickerModal();
  }
}

/**
 * Open the astraeus-media picker iframe in a modal overlay.
 *
 * @param {string} fieldName
 */
export function openImagePicker(fieldName) {
  currentPickerField = fieldName;
  const iframeSrc = `${CONFIG.mediaBase}/admin?picker=1`;

  const modal = el('div', { class: 'picker-modal' },
    el('div', { class: 'picker-modal__inner' },
      el('button', {
        class: 'picker-modal__close btn btn--ghost',
        title: 'Close',
        onclick: closePickerModal,
      }, '✕'),
      el('iframe', { class: 'picker-modal__iframe', src: iframeSrc })
    )
  );

  document.body.appendChild(modal);
  window.addEventListener('message', onPickerMessage);
}

/**
 * Build the image picker widget for an ImageField.
 * Shows an iframe-based astraeus-media picker when media_base is configured,
 * otherwise falls back to a plain text input.
 *
 * @param {string} fieldName
 * @param {string} currentValue
 * @param {function} onFieldChange
 * @returns {HTMLElement}
 */
export function buildImagePickerField(fieldName, currentValue, onFieldChange) {
  const wrap = el('div', { class: 'image-picker' });

  if (CONFIG.mediaBase) {
    // Thumbnail preview
    const img = el('img', {
      class: 'image-picker__preview',
      id: `img-preview-${fieldName}`,
      src: currentValue || '',
      alt: '',
    });
    img.style.display = currentValue ? '' : 'none';

    // Hidden text input keeps the value in sync
    const hiddenInput = el('input', {
      type: 'hidden',
      id: `img-val-${fieldName}`,
      value: currentValue || '',
    });

    // Listen for picker selection event to update field
    document.addEventListener('editor:image-selected', e => {
      if (e.detail.fieldName === fieldName) {
        onFieldChange(fieldName, e.detail.value);
      }
    }, { once: true });

    const chooseBtn = el('button', {
      class: 'btn btn--ghost',
      onclick: () => openImagePicker(fieldName),
    }, 'Choose Image');

    const clearBtn = el('button', {
      class: 'btn btn--ghost',
      style: currentValue ? '' : 'display:none',
      onclick: () => {
        onFieldChange(fieldName, '');
        img.src = '';
        img.style.display = 'none';
        hiddenInput.value = '';
        clearBtn.style.display = 'none';
      },
    }, 'Clear');

    wrap.appendChild(img);
    wrap.appendChild(hiddenInput);
    wrap.appendChild(chooseBtn);
    wrap.appendChild(clearBtn);
  } else {
    // Fallback — plain text input
    const input = el('input', {
      class: 'field-input',
      id: `field-${fieldName}`,
      type: 'text',
      placeholder: 'https://…',
      value: currentValue || '',
      oninput: e => onFieldChange(fieldName, e.target.value),
    });
    wrap.appendChild(input);
    wrap.appendChild(el('span', { class: 'field-help' }, '(configure media_base on Editor to enable image picker)'));
  }

  return wrap;
}
