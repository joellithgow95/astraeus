/**
 * theme.js — Light, dark, or follow the system.
 *
 * The stored choice lives in localStorage under "astraeus-theme", the key the
 * CMS's static/theme.js reads in <head> before first paint, so every admin
 * surface on the origin honours a choice made here. tokens.css does the rest:
 * with no data-astraeus-theme attribute it follows prefers-color-scheme.
 */

import { icon } from './icons.js'

export const THEME_KEY = 'astraeus-theme'

/** The order the toggle steps through. */
const ORDER = ['system', 'light', 'dark']

const LABELS = {
  system: 'Theme: match system',
  light: 'Theme: light',
  dark: 'Theme: dark',
}

const ICONS = { system: 'monitor', light: 'sun', dark: 'moon' }

/** @returns {'system'|'light'|'dark'} */
export function getThemeChoice() {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

/**
 * Store a choice and apply it to <html>.
 *
 * @param {'system'|'light'|'dark'} choice
 */
export function setThemeChoice(choice) {
  const root = document.documentElement
  if (choice === 'light' || choice === 'dark') {
    root.setAttribute('data-astraeus-theme', choice)
  } else {
    root.removeAttribute('data-astraeus-theme')
  }
  try {
    if (choice === 'system') localStorage.removeItem(THEME_KEY)
    else localStorage.setItem(THEME_KEY, choice)
  } catch { /* storage blocked: the choice lasts this page */ }
}

/** The choice after this one. */
export function nextThemeChoice(choice) {
  return ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length]
}

/**
 * A button that steps through system → light → dark, showing the current
 * choice as its icon and naming it in its tooltip.
 *
 * @returns {HTMLButtonElement}
 */
export function buildThemeToggle() {
  const btn = document.createElement('button')
  btn.type = 'button'
  btn.className = 'icon-btn theme-toggle'

  const paint = () => {
    const choice = getThemeChoice()
    btn.replaceChildren(icon(ICONS[choice]))
    btn.title = `${LABELS[choice]} (click to change)`
    btn.setAttribute('aria-label', LABELS[choice])
    btn.dataset.theme = choice
  }

  btn.addEventListener('click', () => {
    setThemeChoice(nextThemeChoice(getThemeChoice()))
    paint()
  })
  paint()
  return btn
}
