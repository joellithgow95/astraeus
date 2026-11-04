# Astraeus

**Governed data platform for Python/Starlette developers.**

Astraeus gives you the primitives — blocks, documents, singletons, references, webhooks — to make any structured data artifact auditable, versioned, and agent-accessible. Define your schema in Python, get a full REST API and JSON Schema introspection for free.

## The stack

| Package | Role | Status |
|---|---|---|
| **starlette-cms** | Headless CMS — block registry, document API, webhooks, schema versioning | v0.4.0 |
| **starlette-editor** | Visual editing UI — ProseMirror-based, auto-generated from block schema | Planned |
| **astraeus-media** | Media management — S3-compatible storage, IIIF Image API, presigned uploads | In progress |

Each package installs independently from PyPI. Together they form a full content management and data governance stack.

## Not just a CMS

The same primitives that power editorial content work for any structured data that needs version history, authorship, and audit trail:

- **Intake forms** — structured submissions as governed documents
- **Rule tables** — actuarial parameters as singleton governed config
- **AI pipeline prompts** — versioned prompt templates with approval workflow
- **Eval datasets** — human-scored AI runs with document references
- **Test case libraries** — curated test scenarios as authored fixtures

## Install

```bash
pip install starlette-cms
```

## Quick example

```python
from starlette_cms import CMS, TextField, RichTextField, ListField
from starlette.applications import Starlette
from starlette.routing import Mount

cms = CMS(database_url="sqlite:///content.db")

@cms.block("hero")
class HeroBlock:
    title: str = TextField(required=True, label="Headline")
    body: dict = RichTextField()

@cms.document("page")
class PageDocument:
    title: str = TextField(required=True)
    slug: str = TextField(required=True)
    sections: list = ListField(blocks=[HeroBlock])

app = Starlette(
    routes=[Mount("/cms", app=cms.app)],
    lifespan=cms.lifespan,
)
```

This gives you `POST /cms/api/documents`, `GET /cms/api/schema`, webhooks, and more — all type-safe and validated against your block definitions.

**[Get started in 5 minutes →](getting-started.md)**
