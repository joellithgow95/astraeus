# Astraeus

An open-source content stack for Python developers. Headless CMS, visual editor, and media management — built for the Starlette/ASGI ecosystem.

## Packages

| Package | Description | PyPI |
|---------|-------------|------|
| [`starlette-cms`](packages/starlette-cms) | Headless CMS — block registry, document API, webhooks, schema versioning | *(pre-release)* |
| [`starlette-editor`](packages/starlette-editor) | Visual editing UI — ProseMirror-based, auto-generated from block schema | *(pre-release)* |
| [`astraeus-media`](packages/astraeus-media) | Media management — S3-compatible storage, IIIF Image API, upload flow, admin UI | *(pre-release)* |

## How it fits together

```
┌─────────────────────────────────────────────────┐
│  Your Starlette / FastAPI app                   │
│                                                 │
│  /cms     ← starlette-cms (content API)         │
│  /editor  ← starlette-editor (visual UI)        │
│  /media   ← astraeus-media (media management)         │
└─────────────────────────────────────────────────┘
         │ webhooks on publish
         ▼
  Netlify / Vercel rebuild hook
         │
         ▼
  Your Astro / Next / static frontend
```

Each package works standalone. Use one, two, or all three.

## Agent interface

Each package ships an optional MCP server (`[mcp]` extra) that exposes its API as tools for LLM agents:

```bash
pip install starlette-cms[mcp]
starlette-cms mcp serve --url https://mysite.com/cms --api-key secret
```

## Development

This is a UV workspace monorepo. All packages share a single lockfile.

```bash
git clone git@github.com:ASneakyToast/astraeus
cd astraeus
uv sync

# Run all tests
uv run pytest packages/

# Run tests for one package
uv run pytest packages/starlette-cms/
```

## Examples

See [`examples/demo`](examples/demo) for a minimal full-stack integration.

## Status

All packages are pre-release. Specs are complete; implementation in progress.

## License

MIT
