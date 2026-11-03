/**
 * components/changeset-panel.js — Changesets, for both editing surfaces.
 *
 * Tree-view layout: changesets as collapsible groups with child doc rows.
 * Orphaned drafts (in no changeset) listed at the bottom.
 *
 * This was two components — one per surface, 1,810 lines between them, neither
 * a superset of the other. A user moving between the shell and the live site
 * lost capabilities in both directions depending which way they went. This is
 * their union (ADR 020 §3).
 *
 * Surfaces differ only in mounting:
 *   variant 'shell' — docked panel, drag and resize on a precise pointer
 *   variant 'embed' — overlay on the published page, fixed position
 *
 * Navigation is injected, because "go to this document" means selecting it in
 * the shell and following a URL on the live site. Everything else is shared.
 */

import {
  fetchDirtyDocs,
  fetchUnpublishedDocs,
  fetchOpenChangesets,
  createChangeset,
  addDocToChangeset,
  removeDocFromChangeset,
  publishChangeset,
  fetchChangesetDiff,
  scheduleChangeset,
  deleteChangeset,
  setDraftDeleted,
  discardDraft,
  patchChangeset,
} from '../api.js';
import {
  getActiveChangesetId,
  setActiveChangesetId,
  onActiveChangesetChange,
} from '../changeset-store.js';
import { showToast } from './toast.js';

const LS_GEOMETRY_KEY = 'cms-changeset-panel-geometry';
const MIN_WIDTH = 280;
const MIN_HEIGHT = 200;

const PANEL_STYLES = `
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 9998;
  width: 340px;
  height: 420px;
  background: var(--bg-elevated);
  color: var(--text-primary);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 13px;
  box-shadow: var(--shadow-md, 0 4px 24px rgba(0,0,0,0.5));
  display: flex;
  flex-direction: column;
  overflow: hidden;
`;

const SECTION_HEADING = `
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
  padding: 8px 14px 4px;
  margin: 0;
`;

const ITEM = `
  padding: 6px 14px;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
`;

const BTN = `
  border: none;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  font-family: system-ui, -apple-system, sans-serif;
`;

const SELECT_STYLE = `
  background: var(--border-default); color: var(--text-primary); border: 1px solid var(--border-strong);
  border-radius: 6px; padding: 3px 6px; font-size: 11px; cursor: pointer;
`;

export class ChangesetPanel {
  /**
   * @param {object}   [opts]
   * @param {'shell'|'embed'} [opts.variant]   Mounting style only.
   * @param {(docType: string, docId: string) => void|Promise<void>} [opts.onNavigate]
   *   Open a document. Omitted on surfaces with nowhere to go, which hides
   *   the "Go to" action rather than offering one that does nothing.
   */
  constructor({ variant = 'shell', onNavigate = null } = {}) {
    this.variant = variant;
    this.onNavigate = onNavigate;
    this.el = null;
    this.visible = false;
    this.dirtyDocs = [];
    this.openChangesets = [];
    this.expanded = new Set();
    this.openMenuId = null;
    this.selectedDocId = null;
    this.activeChangesetId = getActiveChangesetId();

    // changeset-store is the single source of active-changeset state across
    // every surface (ED-4C). Surfaces that need it elsewhere — the shell
    // mirrors it into its own state for the header — subscribe separately
    // rather than having this component push into them.
    onActiveChangesetChange((newId) => {
      this.activeChangesetId = newId;
      if (this.visible) this._render();
    });

    window.addEventListener('cms:chat-turn-done', () => this.refresh());
  }

  mount() {
    this.el = document.createElement('div');
    this.el.setAttribute('data-cms-changeset-panel', '');
    this.el.style.cssText = PANEL_STYLES;
    this.el.style.display = 'none';

    this._scrollContainer = document.createElement('div');
    this._scrollContainer.style.cssText = 'flex: 1; overflow-y: auto;';
    this.el.appendChild(this._scrollContainer);

    if (this.variant === 'shell') {
      this.el.appendChild(this._buildResizeHandle());
      this._restoreGeometry();
    }
    document.body.appendChild(this.el);
  }

  async toggle() {
    this.visible = !this.visible;
    if (this.el) {
      // 'flex', not 'block': the panel is a flex column (PANEL_STYLES) and
      // its scroll container relies on flex: 1 to stay inside the fixed
      // height. 'block' dropped that, so content overflowed and was clipped.
      this.el.style.display = this.visible ? 'flex' : 'none';
    }
    if (this.visible) {
      await this.refresh();
    }
  }

  async refresh() {
    try {
      const [dirtyResult, unpubResult, csResult] = await Promise.all([
        fetchDirtyDocs(),
        fetchUnpublishedDocs(),
        fetchOpenChangesets(),
      ]);
      const seen = new Set();
      this.dirtyDocs = [];
      for (const doc of (dirtyResult.documents ?? [])) {
        if (!seen.has(doc.id)) { seen.add(doc.id); this.dirtyDocs.push(doc); }
      }
      for (const doc of (unpubResult.documents ?? [])) {
        if (!seen.has(doc.id)) { seen.add(doc.id); this.dirtyDocs.push(doc); }
      }
      this.openChangesets = csResult.changesets ?? [];
    } catch (_err) {
      this.dirtyDocs = [];
      this.openChangesets = [];
    }

    const activeId = this.activeChangesetId;
    if (activeId) {
      const active = this.openChangesets.find(cs => cs.id === activeId);
      if (active) {
      }
    }

    // Auto-expand active changeset on first load
    if (this.expanded.size === 0 && activeId) {
      this.expanded.add(activeId);
    }

    this._render();
  }

  _computeOrphans() {
    const assignedIds = new Set();
    for (const cs of this.openChangesets) {
      for (const doc of (cs.documents || [])) {
        assignedIds.add(doc.id);
      }
    }
    return this.dirtyDocs.filter(d => !assignedIds.has(d.id));
  }

  // ── Rendering ──────────────────────────────────────────────────────────────

  _render() {
    if (!this.el || !this._scrollContainer) return;
    this._scrollContainer.innerHTML = '';

    const activeId = this.activeChangesetId;

    // Title bar (draggable)
    const titleBar = document.createElement('div');
    titleBar.style.cssText = `
      padding: 12px 14px 10px;
      border-bottom: 1px solid var(--border-default);
      display: flex; align-items: center; justify-content: space-between;
      cursor: grab; user-select: none;
    `;
    const title = document.createElement('span');
    title.style.cssText = 'font-weight: 700; color: var(--text-primary);';
    title.textContent = '\u{1F4CB} Changesets';

    const closeBtn = document.createElement('button');
    closeBtn.style.cssText = `${BTN} background: transparent; color: var(--text-muted); font-size: 16px; padding: 0 4px;`;
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', () => this.toggle());

    if (this.variant === 'shell') {
      titleBar.addEventListener('mousedown', (e) => this._startDrag(e, closeBtn));
    }

    titleBar.appendChild(title);
    titleBar.appendChild(closeBtn);
    this._scrollContainer.appendChild(titleBar);

    // ── Changeset groups ──────────────────────────────────────────────────
    this.openChangesets.forEach(cs => {
      const isActive = cs.id === activeId;
      const isExpanded = this.expanded.has(cs.id);
      const docs = cs.documents || [];

      // Header row
      const header = document.createElement('div');
      header.dataset.csHeader = '';
      header.dataset.csId = cs.id;
      header.style.cssText = `
        padding: 8px 14px;
        display: flex; align-items: center; gap: 8px;
        cursor: pointer;
        border-bottom: 1px solid var(--border-subtle);
        ${isActive ? 'background: var(--border-subtle);' : ''}
      `;

      const caret = document.createElement('span');
      caret.style.cssText = 'font-size: 10px; color: var(--text-muted); width: 12px; flex-shrink: 0; user-select: none;';
      caret.textContent = isExpanded ? '▾' : '▸';

      const label = document.createElement('span');
      label.style.cssText = `
        flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        color: ${isActive ? 'var(--accent)' : 'var(--text-primary)'};
      `;
      const activeMark = isActive ? ' (active)' : '';
      label.textContent = `${cs.title || 'Untitled'}${activeMark} — ${docs.length} change${docs.length !== 1 ? 's' : ''}`;
      label.title = cs.title || 'Untitled';

      // Click header to toggle expand
      const headerClickArea = document.createElement('div');
      headerClickArea.style.cssText = 'display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; cursor: pointer;';
      headerClickArea.appendChild(caret);
      headerClickArea.appendChild(label);
      headerClickArea.addEventListener('click', () => {
        if (this.expanded.has(cs.id)) {
          this.expanded.delete(cs.id);
        } else {
          this.expanded.add(cs.id);
        }
        this._render();
      });

      // Action buttons (right side)
      const actions = document.createElement('div');
      actions.style.cssText = 'display: flex; gap: 4px; flex-shrink: 0; align-items: center;';

      const publishBtn = document.createElement('button');
      publishBtn.style.cssText = `${BTN} background: var(--accent); color: var(--accent-on); padding: 3px 8px; font-size: 11px;`;
      publishBtn.textContent = 'Publish';
      publishBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._handlePublish(cs.id);
      });

      const menuBtn = document.createElement('button');
      menuBtn.style.cssText = `${BTN} background: var(--border-default); color: var(--text-primary); padding: 3px 6px; font-size: 13px; position: relative;`;
      menuBtn.textContent = '⋮';
      menuBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.openMenuId = this.openMenuId === cs.id ? null : cs.id;
        this._render();
      });

      actions.appendChild(publishBtn);
      actions.appendChild(menuBtn);

      header.appendChild(headerClickArea);
      header.appendChild(actions);
      this._scrollContainer.appendChild(header);

      // Overflow menu
      if (this.openMenuId === cs.id) {
        const menu = document.createElement('div');
        menu.style.cssText = `
          background: var(--border-default); border: 1px solid var(--border-strong); border-radius: 8px;
          padding: 4px 0; margin: 0 14px 4px; box-shadow: var(--shadow-sm, 0 2px 8px rgba(0,0,0,0.3));
        `;

        const menuItemStyle = `
          display: block; width: 100%; text-align: left; padding: 6px 12px;
          background: none; border: none; color: var(--text-primary); font-size: 12px;
          cursor: pointer; font-family: system-ui, -apple-system, sans-serif;
        `;

        if (!isActive) {
          const setActiveItem = document.createElement('button');
          setActiveItem.style.cssText = menuItemStyle;
          setActiveItem.textContent = 'Set active';
          setActiveItem.addEventListener('click', () => {
            this.openMenuId = null;
            this._setActive(cs.id);
          });
          menu.appendChild(setActiveItem);
        } else {
          const clearActiveItem = document.createElement('button');
          clearActiveItem.style.cssText = menuItemStyle;
          clearActiveItem.textContent = 'Clear active';
          clearActiveItem.addEventListener('click', () => {
            this.openMenuId = null;
            setActiveChangesetId(null);
            this._render();
          });
          menu.appendChild(clearActiveItem);
        }

        const renameItem = document.createElement('button');
        renameItem.style.cssText = menuItemStyle;
        renameItem.textContent = 'Rename…';
        renameItem.addEventListener('click', async () => {
          this.openMenuId = null;
          const newTitle = window.prompt('New name:', cs.title || '');
          if (newTitle == null || newTitle === cs.title) return;
          try {
            await patchChangeset(cs.id, { title: newTitle });
            await this.refresh();
          } catch (err) {
            showToast('error', 'Failed to rename', err.message);
          }
        });
        menu.appendChild(renameItem);

        const scheduleItem = document.createElement('button');
        scheduleItem.style.cssText = menuItemStyle;
        scheduleItem.textContent = 'Schedule…';
        scheduleItem.addEventListener('click', () => {
          this.openMenuId = null;
          this._render();
          const h = this.el.querySelector(`[data-cs-id="${cs.id}"]`);
          if (h) this._handleSchedule(cs.id, h);
        });
        menu.appendChild(scheduleItem);

        const deleteItem = document.createElement('button');
        deleteItem.style.cssText = `${menuItemStyle} color: var(--pending);`;
        deleteItem.textContent = 'Delete';
        deleteItem.addEventListener('click', () => {
          this.openMenuId = null;
          this._handleDelete(cs.id);
        });
        menu.appendChild(deleteItem);

        this._scrollContainer.appendChild(menu);
      }

      // Child doc rows (when expanded)
      if (isExpanded && docs.length > 0) {
        docs.forEach(doc => {
          const isSelected = this.selectedDocId === doc.id;

          const row = document.createElement('div');
          row.style.cssText = `
            padding: 5px 14px 5px 34px;
            border-bottom: 1px solid var(--border-subtle);
            font-size: 12px;
            cursor: pointer;
            ${isSelected ? 'background: var(--border-default);' : ''}
          `;

          // Top line — label, always visible
          const topLine = document.createElement('div');
          topLine.style.cssText = 'display: flex; align-items: center; gap: 6px;';

          const docLabel = document.createElement('span');
          docLabel.style.cssText = 'flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary);';
          const conflict = (doc.also_in && doc.also_in.length > 0) ? '⚠ ' : '';
          docLabel.textContent = `${conflict}${doc.doc_type}: ${doc.slug || '(no slug)'}`;
          docLabel.title = doc.also_in?.length
            ? `Also in: ${doc.also_in.map(c => c.title || 'Untitled').join(', ')}`
            : `${doc.doc_type}: ${doc.slug}`;

          topLine.appendChild(docLabel);
          row.appendChild(topLine);

          // Click row to toggle selection
          row.addEventListener('click', () => {
            this.selectedDocId = this.selectedDocId === doc.id ? null : doc.id;
            this._render();
          });

          // Action bar — only when selected
          if (isSelected) {
            const actionBar = document.createElement('div');
            actionBar.style.cssText = 'display: flex; gap: 4px; align-items: center; padding-top: 5px;';

            // "Move to…" select
            const otherCs = this.openChangesets.filter(other => other.id !== cs.id);
            const moveSelect = document.createElement('select');
            moveSelect.style.cssText = SELECT_STYLE;

            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Move to…';
            placeholder.disabled = true;
            placeholder.selected = true;
            moveSelect.appendChild(placeholder);

            otherCs.forEach(target => {
              const opt = document.createElement('option');
              opt.value = target.id;
              opt.textContent = `${target.id === activeId ? '● ' : ''}${target.title || 'Untitled'}`;
              moveSelect.appendChild(opt);
            });

            const newOpt = document.createElement('option');
            newOpt.value = '__new__';
            newOpt.textContent = 'Create new…';
            moveSelect.appendChild(newOpt);

            moveSelect.addEventListener('click', (e) => e.stopPropagation());
            moveSelect.addEventListener('change', async (e) => {
              e.stopPropagation();
              const val = moveSelect.value;
              if (!val) return;
              this.selectedDocId = null;
              if (val === '__new__') {
                const newTitle = window.prompt('Changeset name (optional):') ?? '';
                if (newTitle === null) return;
                try {
                  const newCs = await createChangeset(newTitle);
                  await this._handleMove(doc.id, cs.id, newCs.id);
                } catch (err) {
                  showToast('error', 'Failed to create changeset', err.message);
                }
              } else {
                await this._handleMove(doc.id, cs.id, val);
              }
            });

            // Go to button
            const goToBtn = document.createElement('button');
            goToBtn.style.cssText = `${BTN} background: none; color: var(--accent); padding: 2px 6px; font-size: 11px;`;
            goToBtn.textContent = 'Go to';
            goToBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              this._handleGoToDoc(doc.doc_type, doc.id);
            });

            // View changes button
            const viewBtn = document.createElement('button');
            viewBtn.style.cssText = `${BTN} background: none; color: var(--text-secondary); padding: 2px 6px; font-size: 11px;`;
            viewBtn.textContent = 'View changes';
            viewBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              this._handleViewDocDiff(cs.id, doc.id, row);
            });

            // Remove button
            const removeBtn = document.createElement('button');
            removeBtn.style.cssText = `${BTN} background: none; color: var(--pending); padding: 2px 6px; font-size: 11px;`;
            removeBtn.textContent = 'Remove';
            removeBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              this.selectedDocId = null;
              this._handleRemoveFromChangeset(cs.id, doc.id);
            });

            actionBar.appendChild(moveSelect);
            if (this.onNavigate) actionBar.appendChild(goToBtn);
            actionBar.appendChild(viewBtn);
            actionBar.appendChild(removeBtn);
            row.appendChild(actionBar);
          }

          this._scrollContainer.appendChild(row);
        });
      } else if (isExpanded && docs.length === 0) {
        const emptyRow = document.createElement('div');
        emptyRow.style.cssText = 'padding: 5px 14px 5px 34px; color: var(--text-muted); font-size: 12px; border-bottom: 1px solid var(--border-subtle);';
        emptyRow.textContent = 'No documents';
        this._scrollContainer.appendChild(emptyRow);
      }
    });

    // New changeset button
    const newBtnWrap = document.createElement('div');
    newBtnWrap.style.cssText = 'padding: 6px 14px;';
    const newBtn = document.createElement('button');
    newBtn.style.cssText = `${BTN} background: var(--border-default); color: var(--text-primary); width: 100%; text-align: left;`;
    newBtn.textContent = '+ New changeset';
    newBtn.addEventListener('click', () => this._promptAndCreate(null));
    newBtnWrap.appendChild(newBtn);
    this._scrollContainer.appendChild(newBtnWrap);

    // ── Orphaned drafts ───────────────────────────────────────────────────
    const orphans = this._computeOrphans();

    const divider = document.createElement('div');
    divider.style.cssText = 'border-top: 1px solid var(--border-default); margin: 4px 0 0;';
    this._scrollContainer.appendChild(divider);

    const orphanHeading = document.createElement('p');
    orphanHeading.style.cssText = SECTION_HEADING;
    orphanHeading.textContent = 'Orphaned drafts';
    this._scrollContainer.appendChild(orphanHeading);

    if (orphans.length === 0) {
      const empty = document.createElement('div');
      empty.style.cssText = 'padding: 4px 14px 10px; color: var(--text-muted); font-size: 12px;';
      empty.textContent = 'No orphaned drafts';
      this._scrollContainer.appendChild(empty);
    } else {
      orphans.forEach(doc => {
        const isSelected = this.selectedDocId === doc.id;

        const row = document.createElement('div');
        row.style.cssText = `
          padding: 5px 14px 5px 28px;
          border-bottom: 1px solid var(--border-subtle);
          font-size: 12px;
          cursor: pointer;
          ${isSelected ? 'background: var(--border-default);' : ''}
        `;

        const topLine = document.createElement('div');
        topLine.style.cssText = 'display: flex; align-items: center; gap: 6px;';

        const docLabel = document.createElement('span');
        docLabel.style.cssText = `flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary);${doc.draft_deleted ? ' opacity: 0.5; text-decoration: line-through;' : ''}`;
        docLabel.textContent = `${doc.doc_type}: ${doc.slug || '(no slug)'}`;
        docLabel.title = `${doc.doc_type}: ${doc.slug}`;

        topLine.appendChild(docLabel);

        if (doc.draft_deleted) {
          const delBadge = document.createElement('span');
          delBadge.style.cssText = 'font-size: 10px; padding: 1px 5px; border-radius: 4px; background: rgba(218,54,51,0.15); color: var(--pending); font-weight: 500; flex-shrink: 0;';
          delBadge.textContent = 'Will delete';
          topLine.appendChild(delBadge);
        }

        row.appendChild(topLine);

        row.addEventListener('click', () => {
          this.selectedDocId = this.selectedDocId === doc.id ? null : doc.id;
          this._render();
        });

        if (isSelected) {
          const actionBar = document.createElement('div');
          actionBar.style.cssText = 'display: flex; gap: 4px; align-items: center; padding-top: 5px;';

          // "Add to…" select
          const addSelect = document.createElement('select');
          addSelect.style.cssText = SELECT_STYLE;

          const placeholder = document.createElement('option');
          placeholder.value = '';
          placeholder.textContent = 'Add to…';
          placeholder.disabled = true;
          placeholder.selected = true;
          addSelect.appendChild(placeholder);

          this.openChangesets.forEach(cs => {
            const opt = document.createElement('option');
            opt.value = cs.id;
            opt.textContent = `${cs.id === activeId ? '● ' : ''}${cs.title || 'Untitled'}`;
            addSelect.appendChild(opt);
          });

          const newOpt = document.createElement('option');
          newOpt.value = '__new__';
          newOpt.textContent = 'Create new…';
          addSelect.appendChild(newOpt);

          addSelect.addEventListener('click', (e) => e.stopPropagation());
          addSelect.addEventListener('change', async (e) => {
            e.stopPropagation();
            const val = addSelect.value;
            if (!val) return;
            this.selectedDocId = null;
            if (val === '__new__') {
              await this._promptAndCreate(doc.id);
            } else {
              await this._handleAddToChangeset(doc.id, val);
            }
          });

          // Discard draft button
          const discardBtn = document.createElement('button');
          discardBtn.style.cssText = `${BTN} background: none; color: var(--pending); padding: 2px 6px; font-size: 11px;`;
          discardBtn.textContent = 'Discard';
          discardBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._handleDiscardDraft(doc.id, doc.slug);
          });

          // Delete document button (staged toggle)
          const deleteBtn = document.createElement('button');
          deleteBtn.style.cssText = `${BTN} background: none; color: ${doc.draft_deleted ? 'var(--accent)' : 'var(--pending)'}; padding: 2px 6px; font-size: 11px;`;
          deleteBtn.textContent = doc.draft_deleted ? 'Cancel delete' : 'Delete';
          deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._handleDeleteDocument(doc.id, doc.slug, doc.draft_deleted);
          });

          // Go to button
          const goToBtn = document.createElement('button');
          goToBtn.style.cssText = `${BTN} background: none; color: var(--accent); padding: 2px 6px; font-size: 11px;`;
          goToBtn.textContent = 'Go to';
          goToBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._handleGoToDoc(doc.doc_type, doc.id);
          });

          actionBar.appendChild(addSelect);
          if (this.onNavigate) actionBar.appendChild(goToBtn);
          actionBar.appendChild(discardBtn);
          actionBar.appendChild(deleteBtn);
          row.appendChild(actionBar);
        }

        this._scrollContainer.appendChild(row);
      });
    }

    // Footer: Clear active
    if (activeId) {
      const footer = document.createElement('div');
      footer.style.cssText = 'padding: 8px 14px; border-top: 1px solid var(--border-default);';

      const clearBtn = document.createElement('button');
      clearBtn.style.cssText = `${BTN} background: var(--border-default); color: var(--text-muted); width: 100%; text-align: center;`;
      clearBtn.textContent = 'Clear active';
      clearBtn.addEventListener('click', () => {
        setActiveChangesetId(null);
        this._render();
      });
      footer.appendChild(clearBtn);
      this._scrollContainer.appendChild(footer);
    }
  }

  // ── Drag / resize / geometry ──────────────────────────────────────────────

  _startDrag(e, closeBtn) {
    // Below this width the panel is a full-width sheet and geometry is
    // owned by CSS — dragging or resizing it would fight the stylesheet, and
    // these are mouse-only interactions anyway.
    if (window.innerWidth <= 640) return;
    if (!this.el || (closeBtn && closeBtn.contains(e.target))) return;
    e.preventDefault();

    const rect = this.el.getBoundingClientRect();
    this.el.style.top = rect.top + 'px';
    this.el.style.left = rect.left + 'px';
    this.el.style.bottom = 'auto';
    this.el.style.right = 'auto';

    const offsetX = e.clientX - rect.left;
    const offsetY = e.clientY - rect.top;

    const onMove = (ev) => {
      const maxLeft = window.innerWidth - this.el.offsetWidth;
      const maxTop = window.innerHeight - this.el.offsetHeight;
      this.el.style.left = Math.max(0, Math.min(ev.clientX - offsetX, maxLeft)) + 'px';
      this.el.style.top = Math.max(0, Math.min(ev.clientY - offsetY, maxTop)) + 'px';
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      this._persistGeometry();
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  _buildResizeHandle() {
    const handle = document.createElement('div');
    handle.style.cssText = `
      position: absolute; right: 0; bottom: 0; width: 16px; height: 16px;
      cursor: nwse-resize;
      background: linear-gradient(135deg, transparent 50%, var(--border-strong) 50%, var(--border-strong) 60%, transparent 60%, transparent 70%, var(--border-strong) 70%, var(--border-strong) 80%, transparent 80%);
    `;
    handle.addEventListener('mousedown', (e) => this._startResize(e));
    return handle;
  }

  _startResize(e) {
    // Below this width the panel is a full-width sheet and geometry is
    // owned by CSS — dragging or resizing it would fight the stylesheet, and
    // these are mouse-only interactions anyway.
    if (window.innerWidth <= 640) return;
    if (!this.el) return;
    e.preventDefault();
    e.stopPropagation();

    const rect = this.el.getBoundingClientRect();
    this.el.style.top = rect.top + 'px';
    this.el.style.left = rect.left + 'px';
    this.el.style.bottom = 'auto';
    this.el.style.right = 'auto';

    const startX = e.clientX;
    const startY = e.clientY;
    const startW = this.el.offsetWidth;
    const startH = this.el.offsetHeight;

    const onMove = (ev) => {
      this.el.style.width = Math.max(MIN_WIDTH, startW + (ev.clientX - startX)) + 'px';
      this.el.style.height = Math.max(MIN_HEIGHT, startH + (ev.clientY - startY)) + 'px';
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      this._persistGeometry();
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  _persistGeometry() {
    if (!this.el) return;
    const rect = this.el.getBoundingClientRect();
    try {
      localStorage.setItem(LS_GEOMETRY_KEY, JSON.stringify({
        left: rect.left, top: rect.top, width: rect.width, height: rect.height,
      }));
    } catch { /* localStorage unavailable */ }
  }

  _restoreGeometry() {
    // Below this width the panel is a full-width sheet and geometry is
    // owned by CSS — dragging or resizing it would fight the stylesheet, and
    // these are mouse-only interactions anyway.
    if (window.innerWidth <= 640) return;
    if (!this.el) return;
    let geo = null;
    try { geo = JSON.parse(localStorage.getItem(LS_GEOMETRY_KEY) || 'null'); } catch { geo = null; }
    if (!geo) return;

    const width = Math.max(MIN_WIDTH, geo.width || MIN_WIDTH);
    const height = Math.max(MIN_HEIGHT, geo.height || MIN_HEIGHT);
    const left = Math.max(0, Math.min(geo.left ?? 0, window.innerWidth - width));
    const top = Math.max(0, Math.min(geo.top ?? 0, window.innerHeight - height));

    this.el.style.width = width + 'px';
    this.el.style.height = height + 'px';
    this.el.style.left = left + 'px';
    this.el.style.top = top + 'px';
    this.el.style.bottom = 'auto';
    this.el.style.right = 'auto';
  }

  // ── Actions ────────────────────────────────────────────────────────────────

  async _setActive(csId) {
    setActiveChangesetId(csId);
    this.expanded.add(csId);
    this._render();
  }

  async _handleGoToDoc(docType, docId) {
    this.selectedDocId = null;
    if (!this.onNavigate) return;
    await this.onNavigate(docType, docId);
  }

  async _handleMove(docId, fromCsId, toCsId) {
    try {
      await addDocToChangeset(toCsId, docId);
      await removeDocFromChangeset(fromCsId, docId);
      await this.refresh();
    } catch (err) {
      showToast('error', 'Move failed', err.message);
    }
  }

  async _handleViewDocDiff(csId, docId, rowEl) {
    // Remove any existing inline diff in this row
    const existing = rowEl.querySelector('[data-inline-diff]');
    if (existing) {
      existing.remove();
      return;
    }

    const diffWrap = document.createElement('div');
    diffWrap.setAttribute('data-inline-diff', '');
    diffWrap.style.cssText = 'padding-top: 6px; font-size: 11px;';

    const loading = document.createElement('span');
    loading.style.cssText = 'color: var(--text-muted);';
    loading.textContent = 'Loading…';
    diffWrap.appendChild(loading);
    rowEl.appendChild(diffWrap);

    try {
      const diffData = await fetchChangesetDiff(csId);
      const docDiff = (diffData.diffs || []).find(d => d.doc_id === docId);

      diffWrap.innerHTML = '';

      if (!docDiff) {
        diffWrap.style.color = 'var(--text-muted)';
        diffWrap.textContent = 'No diff data found';
        return;
      }

      // Status badge
      const badge = document.createElement('span');
      badge.style.cssText = 'display: inline-block; border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: 600; margin-bottom: 4px;';
      if (docDiff.is_new) {
        badge.style.cssText += 'background: var(--accent); color: var(--accent-on);';
        badge.textContent = 'NEW';
      } else if (docDiff.has_changes) {
        badge.style.cssText += 'background: var(--border-default); color: var(--text-primary);';
        badge.textContent = `+${docDiff.additions} −${docDiff.deletions}`;
      } else {
        badge.style.cssText += 'background: var(--border-default); color: var(--text-muted);';
        badge.textContent = 'No changes';
      }
      diffWrap.appendChild(badge);

      // Unified diff block
      if (docDiff.has_changes && docDiff.diff) {
        const pre = document.createElement('pre');
        pre.style.cssText = `
          background: var(--bg-base); color: var(--text-primary); border-radius: 6px;
          padding: 8px; margin: 4px 0 0; overflow-x: auto;
          font-size: 11px; line-height: 1.4; white-space: pre-wrap;
          max-height: 200px; overflow-y: auto;
        `;
        // Colorize diff lines
        docDiff.diff.split('\n').forEach(line => {
          const span = document.createElement('span');
          span.style.display = 'block';
          if (line.startsWith('+') && !line.startsWith('+++')) {
            span.style.color = 'var(--accent)';
          } else if (line.startsWith('-') && !line.startsWith('---')) {
            span.style.color = 'var(--pending)';
          } else if (line.startsWith('@@')) {
            span.style.color = 'var(--text-secondary)';
          } else {
            span.style.color = 'var(--text-muted)';
          }
          span.textContent = line;
          pre.appendChild(span);
        });
        diffWrap.appendChild(pre);
      }

      // Conflict warning
      if (docDiff.also_in?.length) {
        const warn = document.createElement('div');
        warn.style.cssText = 'color: var(--pending); font-size: 11px; padding-top: 4px;';
        const names = docDiff.also_in.map(c => `"${c.title || 'Untitled'}"`).join(', ');
        warn.textContent = `⚠ Also in: ${names}`;
        diffWrap.appendChild(warn);
      }
    } catch (err) {
      diffWrap.innerHTML = '';
      diffWrap.style.color = 'var(--pending)';
      diffWrap.textContent = `Failed to load diff: ${err.message}`;
    }
  }

  async _handleDiscardDraft(docId, slug) {
    if (!confirm(`Discard draft changes for "${slug}"? This reverts to the last published version.`)) return;
    try {
      this.selectedDocId = null;
      await discardDraft(docId);
      showToast('info', 'Draft discarded');
      await this.refresh();
    } catch (err) {
      showToast('error', 'Discard failed', err.message);
    }
  }

  async _handleDeleteDocument(docId, slug, isDraftDeleted) {
    try {
      this.selectedDocId = null;
      await setDraftDeleted(docId, isDraftDeleted ? null : true, this.activeChangesetId);
      showToast('info', isDraftDeleted ? 'Delete cancelled' : 'Will delete with changeset');
      await this.refresh();
    } catch (err) {
      showToast('error', 'Delete failed', err.message);
    }
  }

  async _handleRemoveFromChangeset(csId, docId) {
    try {
      await removeDocFromChangeset(csId, docId);
      await this.refresh();
    } catch (err) {
      showToast('error', 'Remove failed', err.message);
    }
  }

  async _promptAndCreate(docId) {
    const title = window.prompt('Changeset name (optional):');
    if (title === null) return;
    try {
      const cs = await createChangeset(title);
      setActiveChangesetId(cs.id);
      if (docId) {
        await addDocToChangeset(cs.id, docId);
      }
      this.expanded.add(cs.id);
      await this.refresh();
    } catch (err) {
      showToast('error', 'Failed to create changeset', err.message);
    }
  }

  async _handleAddToChangeset(docId, changesetId) {
    try {
      await addDocToChangeset(changesetId, docId);
      await this.refresh();
    } catch (err) {
      showToast('error', 'Failed to add to changeset', err.message);
    }
  }

  async openDrawer() {
    const csId = this.activeChangesetId;
    if (!csId) return;
    try {
      const diffData = await fetchChangesetDiff(csId);
      this._renderDrawer(csId, diffData);
    } catch (err) {
      showToast('error', 'Failed to load diff', err.message);
    }
  }

  closeDrawer() {
    const existing = document.getElementById('changeset-drawer');
    if (existing) existing.remove();
  }

  /**
   * Open Review & Publish for a changeset. Public entry for other surfaces
   * (e.g. the embed toolbar's Publish) to route a whole session through the
   * same review flow as the panel's own publish button.
   *
   * @param {string} changesetId
   */
  async reviewAndPublish(changesetId) {
    await this._handlePublish(changesetId);
  }

  async _handlePublish(changesetId) {
    try {
      const diffData = await fetchChangesetDiff(changesetId);
      this._renderDrawer(changesetId, diffData);
    } catch (err) {
      showToast('error', 'Failed to load diff', err.message);
    }
  }

  _renderDrawer(changesetId, diffData) {
    this.closeDrawer();

    const overlay = document.createElement('div');
    overlay.id = 'changeset-drawer';
    overlay.className = 'changeset-overlay';
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) this.closeDrawer();
    });

    const modal = document.createElement('div');
    modal.className = 'changeset-modal';

    // Header
    const header = document.createElement('div');
    header.className = 'changeset-drawer__header';
    const title = document.createElement('span');
    title.className = 'changeset-drawer__header-title';
    title.textContent = 'Review & Publish';
    const closeBtn = document.createElement('button');
    closeBtn.className = 'btn btn--ghost';
    closeBtn.style.cssText = 'font-size: 16px; padding: 0 4px;';
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', () => this.closeDrawer());
    header.appendChild(title);
    header.appendChild(closeBtn);
    modal.appendChild(header);

    // Diff rows
    const diffs = diffData.diffs || [];
    if (diffs.length === 0) {
      const empty = document.createElement('div');
      empty.style.cssText = 'padding: 16px; text-align: center;';
      empty.className = 'changeset-drawer__doc-label';
      empty.style.color = 'var(--text-muted)';
      empty.textContent = 'No documents in this changeset.';
      modal.appendChild(empty);
    } else {
      diffs.forEach(d => {
        const row = document.createElement('div');
        row.className = 'changeset-drawer__doc-row';

        const topLine = document.createElement('div');
        topLine.className = 'changeset-drawer__doc-top';

        const label = document.createElement('span');
        label.className = 'changeset-drawer__doc-label';
        label.textContent = `${d.doc_type}: ${d.slug || '(no slug)'}`;

        const badges = document.createElement('span');
        badges.className = 'changeset-drawer__badges';

        if (d.draft_deleted) {
          const tag = document.createElement('span');
          tag.className = 'changeset-drawer__badge changeset-drawer__badge--delete';
          tag.textContent = 'WILL DELETE';
          badges.appendChild(tag);
        } else if (d.is_new) {
          const tag = document.createElement('span');
          tag.className = 'changeset-drawer__badge changeset-drawer__badge--new';
          tag.textContent = 'NEW';
          badges.appendChild(tag);
        } else if (d.additions > 0 || d.deletions > 0) {
          const tag = document.createElement('span');
          tag.className = 'changeset-drawer__badge changeset-drawer__badge--changed';
          tag.textContent = `+${d.additions} -${d.deletions}`;
          badges.appendChild(tag);
        } else if (d.draft_published == null) {
          const tag = document.createElement('span');
          tag.className = 'changeset-drawer__badge changeset-drawer__badge--none';
          tag.textContent = 'No changes';
          badges.appendChild(tag);
        }

        if (d.draft_published != null) {
          const pubTag = document.createElement('span');
          pubTag.className = 'changeset-drawer__badge changeset-drawer__badge--changed';
          pubTag.style.fontStyle = 'italic';
          pubTag.textContent = d.draft_published ? 'Draft → Published' : 'Published → Draft';
          badges.appendChild(pubTag);
        }

        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn btn--ghost';
        removeBtn.style.cssText = 'color: var(--red); font-size: 11px; padding: 2px 6px;';
        removeBtn.textContent = 'Remove';
        removeBtn.addEventListener('click', async () => {
          try {
            await removeDocFromChangeset(changesetId, d.doc_id);
            row.remove();
            showToast('info', 'Removed from changeset');
          } catch (err) {
            showToast('error', 'Remove failed', err.message);
          }
        });

        topLine.appendChild(label);
        topLine.appendChild(badges);
        topLine.appendChild(removeBtn);
        row.appendChild(topLine);

        if (d.also_in && d.also_in.length > 0) {
          const warn = document.createElement('div');
          warn.className = 'changeset-drawer__conflict';
          const names = d.also_in.map(c => `"${c.title || 'Untitled'}"`).join(', ');
          warn.textContent = `⚠ Also in: ${names}`;
          row.appendChild(warn);
        }

        if (d.has_changes && d.diff && !d.draft_deleted) {
          const toggle = document.createElement('button');
          toggle.className = 'changeset-drawer__diff-toggle';
          toggle.textContent = 'Show diff ▸';
          const diffBlock = document.createElement('pre');
          diffBlock.className = 'changeset-drawer__diff-block';
          diffBlock.textContent = d.diff;

          toggle.addEventListener('click', () => {
            const show = diffBlock.style.display === 'none' || !diffBlock.style.display;
            diffBlock.style.display = show ? 'block' : 'none';
            toggle.textContent = show ? 'Hide diff ▾' : 'Show diff ▸';
          });

          row.appendChild(toggle);
          row.appendChild(diffBlock);
        }

        modal.appendChild(row);
      });
    }

    // Footer
    const footer = document.createElement('div');
    footer.className = 'changeset-drawer__footer';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn--ghost';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', () => this.closeDrawer());

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn btn--primary';
    confirmBtn.textContent = 'Confirm Publish';
    confirmBtn.addEventListener('click', async () => {
      confirmBtn.disabled = true;
      confirmBtn.textContent = 'Publishing…';
      try {
        await publishChangeset(changesetId);
        if (this.activeChangesetId === changesetId) {
          setActiveChangesetId(null);
        }
        this.closeDrawer();
        showToast('success', 'Published — site rebuilding');
        await this.refresh();
      } catch (err) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Confirm Publish';
        showToast('error', 'Publish failed', err.message);
      }
    });

    footer.appendChild(cancelBtn);
    footer.appendChild(confirmBtn);
    modal.appendChild(footer);

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
  }

  async _handleSchedule(changesetId, rowEl) {
    rowEl.querySelectorAll('[data-schedule-picker]').forEach(el => el.remove());

    const pickerWrap = document.createElement('div');
    pickerWrap.setAttribute('data-schedule-picker', '');
    pickerWrap.style.cssText = 'flex: 1 1 100%; display: flex; gap: 6px; align-items: center; padding-top: 4px;';

    const input = document.createElement('input');
    input.type = 'datetime-local';
    input.style.cssText = `
      background: var(--border-default); color: var(--text-primary); border: 1px solid var(--border-strong);
      border-radius: 6px; padding: 4px 8px; font-size: 12px; flex: 1;
    `;

    const confirmBtn = document.createElement('button');
    confirmBtn.style.cssText = `${BTN} background: var(--text-secondary); color: var(--accent-on);`;
    confirmBtn.textContent = 'Confirm';
    confirmBtn.addEventListener('click', async () => {
      if (!input.value) return;
      const dt = new Date(input.value).toISOString();
      try {
        await scheduleChangeset(changesetId, dt);
        await this.refresh();
      } catch (err) {
        showToast('error', 'Schedule failed', err.message);
      }
    });

    pickerWrap.appendChild(input);
    pickerWrap.appendChild(confirmBtn);
    rowEl.appendChild(pickerWrap);
  }

  async _handleDelete(changesetId) {
    if (!confirm('Delete this changeset? Documents will not be affected.')) return;
    try {
      await deleteChangeset(changesetId);
      if (this.activeChangesetId === changesetId) {
        setActiveChangesetId(null);
      }
      this.expanded.delete(changesetId);
      await this.refresh();
    } catch (err) {
      showToast('error', 'Delete failed', err.message);
    }
  }
}
