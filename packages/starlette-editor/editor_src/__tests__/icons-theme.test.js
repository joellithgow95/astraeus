// @vitest-environment happy-dom
/**
 * Tests for icons.js and theme.js — the shell's icon set and the
 * light/dark/system choice.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { icon, hasIcon, typeIconName } from '../icons.js'
import { THEME_KEY, getThemeChoice, setThemeChoice, nextThemeChoice, buildThemeToggle } from '../theme.js'

describe('icon', () => {
  it('draws an aria-hidden svg in currentColor', () => {
    const svg = icon('layers')
    expect(svg.tagName.toLowerCase()).toBe('svg')
    expect(svg.getAttribute('aria-hidden')).toBe('true')
    expect(svg.getAttribute('stroke')).toBe('currentColor')
    expect(svg.dataset.icon).toBe('layers')
  })

  it('falls back to the file glyph for an unknown name', () => {
    expect(icon('nope').innerHTML).toBe(icon('file').innerHTML)
  })
})

describe('typeIconName', () => {
  it.each([
    ['blog_post', 'pen'],
    ['project_page', 'folder'],
    ['experience_entry', 'briefcase'],
    ['definition', 'book'],
    ['link', 'bookmark'],
    ['spotify_like', 'music'],
    ['inat_observation', 'leaf'],
    ['system_prompt', 'terminal'],
    ['model_config', 'sliders'],
    ['page', 'file-text'],
    ['widget', 'file'],
  ])('%s → %s', (key, name) => {
    expect(typeIconName(key)).toBe(name)
    expect(hasIcon(name)).toBe(true)
  })

  it('prefers an icon the schema names', () => {
    expect(typeIconName('blog_post', { icon: 'music' })).toBe('music')
    expect(typeIconName('blog_post', { icon: 'not-a-glyph' })).toBe('pen')
  })
})

describe('theme choice', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-astraeus-theme')
  })

  it('follows the system until a choice is stored', () => {
    expect(getThemeChoice()).toBe('system')
  })

  it('pins light or dark on <html> and remembers it', () => {
    setThemeChoice('dark')
    expect(document.documentElement.getAttribute('data-astraeus-theme')).toBe('dark')
    expect(localStorage.getItem(THEME_KEY)).toBe('dark')
    expect(getThemeChoice()).toBe('dark')
  })

  it('clears the pin when set back to system', () => {
    setThemeChoice('light')
    setThemeChoice('system')
    expect(document.documentElement.hasAttribute('data-astraeus-theme')).toBe(false)
    expect(localStorage.getItem(THEME_KEY)).toBe(null)
  })

  it('steps system → light → dark → system', () => {
    expect(nextThemeChoice('system')).toBe('light')
    expect(nextThemeChoice('light')).toBe('dark')
    expect(nextThemeChoice('dark')).toBe('system')
  })

  it('the toggle advances the choice on click', () => {
    const btn = buildThemeToggle()
    expect(btn.dataset.theme).toBe('system')
    btn.click()
    expect(btn.dataset.theme).toBe('light')
    expect(document.documentElement.getAttribute('data-astraeus-theme')).toBe('light')
  })
})
