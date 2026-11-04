/**
 * Enforces the dependency direction in ADR 020.
 *
 *         components/  prosemirror/  utils.js  api.js  state.js  collab.js …
 *                           ▲                    ▲
 *                           │                    │
 *                      standard/             embed/
 *
 * The shared layer was laid out this way from the start but wired backwards:
 * every module in components/ imported from standard/, so embed/ could not
 * consume any of it without dragging the shell in. That is why the surfaces
 * diverged, and why each one grew its own palette.
 *
 * A structural test rather than a convention, because the convention already
 * failed once.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')

/** Surface-specific directories. Neither may be imported by the shared layer. */
const SURFACES = ['standard', 'embed']

/** Shared directories, plus the root modules, which may not import a surface. */
const SHARED_DIRS = ['components', 'prosemirror', 'media']

/** @returns {string[]} absolute paths of .js files under `dir`, recursively */
function jsFiles(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    if (entry === '__tests__' || entry === 'node_modules') continue

    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      out.push(...jsFiles(full))
    } else if (entry.endsWith('.js')) {
      out.push(full)
    }
  }
  return out
}

/** @returns {string[]} module specifiers imported by `file`, static and dynamic */
function importsOf(file) {
  const src = readFileSync(file, 'utf8')
  return [...src.matchAll(/(?:from|import)\s*\(?\s*['"]([^'"]+)['"]/g)].map(m => m[1])
}

/** @returns {string[]} root-level shared modules (api.js, state.js, utils.js, …) */
function rootModules() {
  return readdirSync(SRC)
    .filter(e => e.endsWith('.js') && e !== 'index.js')
}

describe('shared layer does not import a surface', () => {
  const sharedFiles = [
    ...SHARED_DIRS.flatMap(d => jsFiles(join(SRC, d))),
    ...rootModules().map(f => join(SRC, f)),
  ]

  it.each(sharedFiles.map(f => [f.slice(SRC.length + 1), f]))(
    '%s',
    (_label, file) => {
      const offenders = importsOf(file).filter(spec =>
        SURFACES.some(s => spec.includes(`${s}/`)),
      )
      expect(offenders).toEqual([])
    },
  )
})

describe('surfaces do not import each other', () => {
  it.each(SURFACES)('%s', surface => {
    const others = SURFACES.filter(s => s !== surface)
    const offenders = []

    for (const file of jsFiles(join(SRC, surface))) {
      for (const spec of importsOf(file)) {
        if (others.some(o => spec.includes(`${o}/`))) {
          offenders.push(`${file.slice(SRC.length + 1)} → ${spec}`)
        }
      }
    }

    expect(offenders).toEqual([])
  })
})

describe('the shared layer is actually shared', () => {
  it('is reachable from embed/, not just standard/', () => {
    const embedImports = jsFiles(join(SRC, 'embed')).flatMap(importsOf)
    const shared = embedImports.filter(
      spec => spec.startsWith('../') && !spec.includes('embed/'),
    )

    // Before ADR 020 this was a single module (changeset-store.js) while embed/
    // reimplemented the rest.
    expect(shared.length).toBeGreaterThan(1)
  })
})

describe('one ProseMirror schema', () => {
  it('is constructed in exactly one place', () => {
    // Both surfaces connect to the same collab authority, and a step only
    // applies if both ends agree on the schema. Two schemas that match today
    // drift tomorrow, and the failure is silent and mid-session.
    const constructors = jsFiles(SRC).filter(f =>
      /new Schema\(/.test(readFileSync(f, 'utf8')),
    )

    expect(constructors.map(f => f.slice(SRC.length + 1))).toEqual(['prosemirror/schema.js'])
  })
})
