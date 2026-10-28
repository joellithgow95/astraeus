# Webhooks

Webhooks notify external services when documents change. Register a URL and a list of events, and the CMS will POST a JSON payload whenever those events fire.

## Registering a webhook

```bash
curl -X POST http://localhost:8000/cms/api/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer secret" \
  -d '{
    "url": "https://api.netlify.com/build_hooks/abc123",
    "events": ["document.published"]
  }'
```

## Events

| Event | Fires when |
|---|---|
| `document.created` | A document is created via `POST /api/documents` |
| `document.updated` | A document is updated via `PATCH /api/documents/{id}` |
| `document.deleted` | A document is deleted via `DELETE /api/documents/{id}` |
| `document.published` | A document is published (including singleton publishes) |
| `document.unpublished` | A document is unpublished |
| `changeset.published` | A changeset is published. One event for the whole changeset, with `changeset_id`, `title`, `document_ids`, `document_count` and `deleted_document_ids` (its staged deletions). The documents it publishes or deletes fire no `document.*` event of their own |

## Payload shape

Every webhook delivery is a POST with a JSON body:

```json
{
  "event": "document.published",
  "document_id": "abc123",
  "document_type": "article",
  "slug": "my-first-post",
  "timestamp": "2026-01-15T10:30:00Z"
}
```

Singleton publishes include an additional field:

```json
{
  "event": "document.published",
  "document_id": "def456",
  "document_type": "site_config",
  "slug": null,
  "timestamp": "2026-01-15T10:30:00Z",
  "singleton": true
}
```

## Managing webhooks

### List all webhooks

```bash
curl http://localhost:8000/cms/api/webhooks \
  -H "Authorization: Bearer secret"
```

Listing needs the API key even when `read_auth=False`, because a webhook URL
is a credential (a build-hook URL can be triggered by anyone who has it).

### Delete a webhook

```bash
curl -X DELETE http://localhost:8000/cms/api/webhooks/{id} \
  -H "Authorization: Bearer secret"
```

## Delivery behavior

Webhooks are **fire-and-forget** in v1:

- Delivery uses `httpx.AsyncClient` with a 10-second timeout
- Failed deliveries are logged but not retried
- Errors do not block the original request — if a webhook fails, the document operation still succeeds
- Delivery is async — webhooks fire as background tasks after the response is sent

## Example: Netlify build trigger

A common pattern is triggering a static site rebuild when content is published:

```bash
# Register Netlify build hook
curl -X POST http://localhost:8000/cms/api/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer secret" \
  -d '{
    "url": "https://api.netlify.com/build_hooks/your-hook-id",
    "events": ["document.published", "document.unpublished"]
  }'
```

The flow:

1. An author (human or AI agent) publishes a document
2. The CMS fires the `document.published` webhook
3. Netlify receives the POST and triggers a rebuild
4. The static site (Astro, Next.js, etc.) fetches fresh content from the CMS API during build

## Example: cache invalidation

```bash
curl -X POST http://localhost:8000/cms/api/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer secret" \
  -d '{
    "url": "https://your-app.com/api/cache/invalidate",
    "events": ["document.published", "document.updated", "document.deleted"]
  }'
```

Your cache invalidation endpoint can use the `document_type` and `document_id` fields to selectively purge cached data.
