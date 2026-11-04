/**
 * MediaKit Admin — vanilla JS
 *
 * Handles three concerns:
 *  1. Picker mode  — postMessage on asset click
 *  2. Upload flow  — drop zone → /upload/prepare → PUT → /upload/confirm
 *  3. Delete asset — fetch DELETE, redirect on success
 */

'use strict';

// Where astraeus-media is mounted ("" at the root, "/media" under a host app).
const BASE = document.documentElement.dataset.mediaBase || '';

/* -------------------------------------------------------------------------
 * 1. Picker mode
 * ---------------------------------------------------------------------- */

/**
 * Called when a picker-mode asset card is clicked.
 * Posts a message to window.opener and closes the popup.
 *
 * @param {HTMLElement} btn - The card-picker-btn button that was clicked.
 */
function selectAsset(btn) {
    const card = btn.closest('[data-key]');
    if (!card) return;

    const asset = {
        key: card.dataset.key,
        filename: card.dataset.filename,
        contentType: card.dataset.contentType,
        width: card.dataset.width ? parseInt(card.dataset.width, 10) : null,
        height: card.dataset.height ? parseInt(card.dataset.height, 10) : null,
    };

    if (window.opener) {
        window.opener.postMessage({ type: 'astraeus-media:asset-selected', asset }, '*');
    }
    window.close();
}

/**
 * Cancel picker mode — post cancellation message and close popup.
 */
function cancelPicker() {
    if (window.opener) {
        window.opener.postMessage({ type: 'astraeus-media:picker-cancelled' }, '*');
    }
    window.close();
}

/* -------------------------------------------------------------------------
 * 2. Delete asset
 * ---------------------------------------------------------------------- */

/**
 * Delete an asset by key.  Prompts for confirmation, then issues a
 * DELETE request to /assets/{key}.  Redirects to /admin on success.
 *
 * @param {string} key - The asset storage key.
 */
async function deleteAsset(key) {
    if (!confirm(`Delete "${key}"? This cannot be undone.`)) return;

    try {
        const res = await fetch(`${BASE}/assets/${key}`, { method: 'DELETE' });
        if (res.ok || res.status === 204) {
            window.location.href = `${BASE}/admin`;
        } else {
            const body = await res.json().catch(() => ({}));
            alert(`Delete failed: ${body.error || res.statusText}`);
        }
    } catch (err) {
        alert(`Delete failed: ${err.message}`);
    }
}

/* -------------------------------------------------------------------------
 * 3. Upload flow
 * ---------------------------------------------------------------------- */

(function initUpload() {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const uploadQueue = document.getElementById('upload-queue');
    const uploadList = document.getElementById('upload-list');

    if (!dropZone || !fileInput) return;  // Not on the upload page

    // Open file picker on click / keyboard
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            fileInput.click();
        }
    });

    // Drag-over visual feedback
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) {
            handleFiles(e.dataTransfer.files);
        }
    });

    // File input change
    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) {
            handleFiles(fileInput.files);
            fileInput.value = '';  // Allow re-selecting the same file
        }
    });

    /**
     * Enqueue and upload a FileList.
     * @param {FileList} files
     */
    function handleFiles(files) {
        uploadQueue.hidden = false;
        for (const file of files) {
            uploadFile(file);
        }
    }

    /**
     * Upload a single file through the two-step presigned flow.
     * @param {File} file
     */
    async function uploadFile(file) {
        const li = createUploadItem(file.name);
        uploadList.appendChild(li);

        const fill = li.querySelector('.upload-progress-fill');
        const status = li.querySelector('.upload-status');
        const detail = li.querySelector('.upload-detail');

        const setProgress = (pct) => { fill.style.width = `${pct}%`; };
        const setStatus = (msg, cls) => {
            status.textContent = msg;
            status.className = `upload-status ${cls || ''}`;
        };

        try {
            // Step 1: prepare — get presigned upload URL
            setStatus('Preparing…');
            setProgress(10);
            const prepareRes = await fetch(`${BASE}/upload/prepare`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: file.name, content_type: file.type || 'application/octet-stream', size: file.size }),
            });
            if (!prepareRes.ok) {
                const body = await prepareRes.json().catch(() => ({}));
                throw new Error(body.error || `Prepare failed (${prepareRes.status})`);
            }
            const { upload_url: uploadUrl, key } = await prepareRes.json();
            setProgress(30);

            // Step 2: PUT directly to storage (presigned URL)
            setStatus('Uploading…');
            const putRes = await fetch(uploadUrl, {
                method: 'PUT',
                headers: { 'Content-Type': file.type || 'application/octet-stream' },
                body: file,
            });
            if (!putRes.ok) {
                throw new Error(`Upload failed (${putRes.status})`);
            }
            setProgress(75);

            // Step 3: confirm — register in catalog
            setStatus('Confirming…');
            const confirmRes = await fetch(`${BASE}/upload/confirm`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key, filename: file.name, content_type: file.type || 'application/octet-stream', size: file.size }),
            });
            if (!confirmRes.ok) {
                const body = await confirmRes.json().catch(() => ({}));
                throw new Error(body.error || `Confirm failed (${confirmRes.status})`);
            }
            const asset = await confirmRes.json();
            setProgress(100);
            setStatus('Done', 'done');

            // Show thumbnail + link to detail
            if (asset.key) {
                const thumbSrc = `${BASE}/iiif/${asset.key}/square/64,/0/default.webp`;
                detail.innerHTML = `
                    <a href="${BASE}/admin/assets/${asset.key}" class="upload-thumb-link">
                        <img src="${thumbSrc}" alt="${escapeHtml(file.name)}" loading="lazy">
                        View asset
                    </a>
                `;
            }
        } catch (err) {
            setProgress(100);
            setStatus(`Error: ${err.message}`, 'error');
        }
    }

    /**
     * Create a list item element for a pending upload.
     * @param {string} filename
     * @returns {HTMLLIElement}
     */
    function createUploadItem(filename) {
        const li = document.createElement('li');
        li.className = 'upload-item';
        li.innerHTML = `
            <div>
                <div class="upload-item-name">${escapeHtml(filename)}</div>
                <div class="upload-progress-bar">
                    <div class="upload-progress-fill"></div>
                </div>
                <div class="upload-detail"></div>
            </div>
            <span class="upload-status">Queued</span>
        `;
        return li;
    }

    /**
     * Escape HTML special characters for safe insertion into innerHTML.
     * @param {string} str
     * @returns {string}
     */
    function escapeHtml(str) {
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
})();
