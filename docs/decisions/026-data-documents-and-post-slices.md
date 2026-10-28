# ADR 026 — Collected data as its own documents; posts reference slices of it

**Status:** Accepted 2026-10-28 (built in joellithgow.com; see *Outcome*). Supersedes nothing: it changes how an application models what its gateways collect, not
the gateway framework ([ADR 015](015-starlette-cms-gateways.md), [ADR 023](023-gateway-state-and-publishing.md)),
and builds on `DocumentRef` ([ADR 010](010-document-references.md)).
**Date:** 2026-10-27
**Informed by:** `docs/use-cases/personal-site-joellithgow.md`, the joellithgow.com "Content model restructure"
backlog (2026-10-27), and the earlier "CMS data architecture redesign" proposal (2026-10-04), which reached the
same conclusion and was never built.

---

## Context

joellithgow.com is the first real consumer of Astraeus, and its content model mixes two different things in one
kind of document: **data** that is collected (liked songs, nature observations, dictionary entries, links) and
**posts** that a person writes about it.

- **Gateways write post-shaped documents.** The Spotify gateway bundles likes into 22 `spotify_liked_dump`
  months (1,023 songs as embedded JSON); the iNaturalist gateway bundles observations into 31
  `inaturalist_outing` documents, clustered by date and a 3 km radius inside its `fetch()`. Each carries
  `title`, `description`, `publish_date` and `tags`, and the site merges them into the blog feed. The grouping is
  fixed by the gateway, so a post about one album, or a week-long trip, has nowhere to go.
- **The framework never asked for this.** ADR 015's own example writes one `spotify_liked_song` document per
  track. Bundling into months and outings is an application choice, made so each document could double as a page.
  ADR 023's ownership rules (owned vs seeded fields, deferring on a person's draft) exist largely because one
  document is both machine-written data and a person's post.
- **Dictionary entries are both data and feed items.** 15 `definition` documents each get a page under
  `/blog/dictionary/` and their own feed card. Two near-synonyms (Comp Stomp, Bot Bash) are separate entries with
  nothing linking them, and there is no way to write one post about both.
- **Links live inside the post that mentions them.** A `links` JSON list on each post; four `collection` posts
  hold most of them. A collection holds only what was typed into it, so a link in another post never joins it,
  and tags belong to the post, not the link.
- **Three post types that mostly aren't.** `blog_post.post_type` is `article`, `thought` or `collection`. In the
  site, thought and article differ only by a CSS rule; the real switch is `has_detail_page`, which 9 thoughts set
  to false so they have no page and render in full on the feed. `featured` is in the schema and unread.
- **The editing inputs are raw JSON.** `tags`, `links`, `image` and definition `sources` are `JSONField`
  textareas, and the site's build fails on shapes the CMS accepts (a definition source without `type`).

Infranet and Dash, two other systems of the same owner, already separate the steps: collectors write raw items,
and presentation decides how to group them later.

## Decision

### 1. Collected data is one document per item

Each gateway writes **one data document per source item**, with the source's own fields and no title, summary or
publish date of its own:

- `spotify_like`: track, artist, album, art URL, track URL, `liked_at`.
- `inat_observation`: the existing slim record (`curate_observation`), with `observed_on` and coordinates.
- `definition` (the dictionary) and `link` (`url`, `title`, `description`, `date_added`, `tags`) are data too,
  written by a person or an agent rather than a gateway. Each URL is stored once.

Data documents are **published** so the static build can read them, are grouped as "Data" in the editor sidebar
(the registry's existing `group=`), and get **no page or feed card** of their own. Definitions are the exception
on pages: they keep `/blog/dictionary/<slug>`, since a dictionary entry is something you link to; they leave
the feed.

The gateway framework is unchanged. `import_ref` stays the dedup key (now one per item, e.g.
`spotify:like:<track id>`), and a run is still one changeset published once (ADR 023 §4). With nothing seeded on
a data document, every field is owned, so a re-sync overwrites the whole record and the "is this draft a
person's?" question (ADR 023 §3) no longer arises for gateway data.

### 2. One post type, referencing data through slices

`blog_post` becomes the only kind of post. `post_type` is dropped; what a post looks like follows from what it
holds. It gains one field, **`data`: a list of slices**. A slice is one of:

| Kind | Shape | Use |
|---|---|---|
| Range | `{source: "spotify", from: "2026-10-01", to: "2026-10-31"}` | A month of likes; picks up items synced late |
| Picked | `{source: "inat", refs: [<doc ids>]}` | "Three songs off this album", "the birds from this trip", two definitions |
| Tag | `{source: "links", tag: "developer-blog"}` | A collection that grows when a link is tagged |

`source` names a data type through a fixed table in the application (`spotify` → `spotify_like` filtered on
`liked_at`, and so on). The site resolves every slice **at build time** and renders each source with its own
component (today's music and nature cards move there). A collection is then a post with a tag slice; the
`collection` type and its embedded `links` go away, and the collection layout keys on "has a links slice".

The slice shape is validated by the CMS on write (a Pydantic model behind the field), so a bad slice fails at
save, not in the Netlify build.

### 3. What Astraeus adds

The joellithgow schema and site own the content model above. Astraeus provides the generic pieces, each reusable
by any site:

- **A list of `DocumentRef`** (`ListField(item_type=DocumentRef(...))`), already specified in ADR 010's API shape
  but not built: write-time validation of every id, and `resolve_refs` for lists. Picked slices use it, as does
  an optional `see_also` on `definition` for synonyms.
- **A multi-ref picker** in the editor (search by title, add, remove, reorder), with "paste a URL" creating a
  `link` document on the spot.
- **`TagsField`**: `list[str]` rendered as chips with type-ahead from tags already used on the type. Independent
  of the rest; can ship first.
- **A slice editor** for the `data` field: pick a source, then a date range, items or a tag. Last, since the
  JSON shape works through the MCP tools without it.

Whether the slice itself becomes an Astraeus field type, or stays an application `JSONField` with a validator,
is left until a second consumer needs it.

### 4. Grouping becomes a suggestion; monthly posts are composed

The 3 km outing clustering leaves the iNaturalist gateway and becomes a helper that proposes a draft post ("4 new
observations near Albany on 07-04") with a picked slice. A scheduled routine composes the monthly music post on
the 1st: sync Spotify, then create a draft `blog_post` "Liked songs, <month>" with a range slice. Drafts land in
the default changeset (ADR 025) for a person to publish.

## Alternatives considered

- **Keep bundled documents and add references between them.** A post could reference a month document. It keeps
  the gateway deciding the grouping, which is the problem; a post about one album still has no home.
- **Data as plain JSON inside each post.** What `links` does today. Every post copies its data, nothing is shared,
  and a collection cannot gather links it was not given.
- **Only picked refs, no ranges.** Simpler, but a month post would need re-editing whenever a late sync adds a
  song, and the composer would have to be re-run. A range resolves at build time instead.
- **Only ranges and tags, no picks.** Cannot express "these three songs", the case that started this.
- **Resolve slices in the CMS (a computed field or endpoint).** Possible later; the static build already pages
  through every collection, and resolving there keeps the CMS headless (ADR 002) and the slice a pointer, not a
  copy.
- **Data outside the CMS** (a table or JSON in the site repo). Loses version history, the editor, the MCP tools
  and the audit trail that make Astraeus worth using for data (the governed-data use cases).

## Consequences

**Positive**
- A post can be about any cut of the data: a month, an album, a trip, two synonyms, a tag.
- Gateways become simple writers of records; ADR 023's draft classification stops mattering for them.
- One post type and one feed card rule; the agents (hermes, the MCP tools) learn one shape.
- A link or definition is stored once and can appear in many posts.

**Negative / trade-offs**
- **Volume.** About 1,100 new data documents on migration, published in one changeset (one site build). A
  changeset that large has not been tried, and the build fetches them all every time.
- **Live URLs.** The 22 music months and 31 outings have pages; their replacement posts must keep the slugs, or
  the site adds redirects.
- **Hand edits on months and outings** (titles, descriptions, notes are seeded fields) must be carried into the new
  posts, not regenerated.
- **Gateway state resets.** The cursor and owned-field hashes assume the old documents; the rewrite resets the
  cursor and needs one `all_time` run against the new types.
- **A range is not a snapshot.** A post's music changes if a song is un-liked and the month re-synced, and an edit
  to a definition shows in every post that picks it. This matches ADR 010 (refs point at current state).
- **Agents and docs** (hermes's blog-bot profile, the MCP tool descriptions, joellithgow `docs/blog-conventions.md`)
  describe `post_type` and must change in the same release.

**Migration** is the application's, as with ADR 025's orphans: a dry-run report first, developed against a local
`litestream restore` of prod, shown to the owner before anything is written; in-flight changesets cleared before
the schema changes so they do not mix.

**Testing.** List-of-refs validation and `resolve_refs` against the real in-process CMS (`RegistryTestCase`); the
slice validator and the gateway rewrite in joellithgow against the same.

## Open questions

1. **Which posts get a detail page.** Today 9 thoughts (`has_detail_page: false`) have no page and show their
   whole body inline on the feed. The owner's intent is that a post has *its own detail page* (not child pages
   under other posts), and that the thought/article split was originally meant to be exactly this inline-only vs
   detail-page difference, built somewhat wrong. Options: every post gets a page and the feed card shows the full
   body when the post is short (no flag); or keep an explicit "inline only" flag on the one post type. Until this
   is settled, `has_detail_page` stays and only `post_type` is dropped.
2. **Monthly posts:** draft for review in Staging, or published automatically? (Leaning: draft.)
3. **Existing months and outings:** convert each to a post with a slice, or replace them with fewer, regrouped
   posts? (Leaning: convert, regroup later by hand.)
4. **Definitions on the feed:** only through posts, or also on their own when new? (Leaning: only through posts.)
5. **`/blog/music` and `/blog/nature`:** list posts, or browse all the data and link to posts? (Leaning: all the
   data.)
6. **Link groups:** plain tags with a short agreed list (developer-blog, inspo, friends, reading), or a separate
   list field so topic tags and list names do not mix? (Leaning: plain tags.)
7. **Where the compose routine runs:** a hermes cron, or a CronJob in jlithgow-ops calling a `gateways compose`
   command? (Leaning: CronJob, versioned and agent-free.)
8. **`featured`:** drop it from `blog_post`, or give it a use?

## Outcome (2026-10-28)

Built and live on joellithgow.com. The open questions were settled as follows, and the build differs from the
Decision above in these places:

1. **Detail pages:** every post has one; `post_type` and `has_detail_page` are both gone. The feed card shows the
   excerpt, or the whole body when the post is short.
2. **Monthly posts:** a draft in Staging. A CronJob runs a compose-only job on the 1st; it never syncs and never
   publishes. A separate daily CronJob syncs both gateways (`since_last_sync`), so the month is already collected.
3. **Existing months and outings:** converted one-to-one into `blog_post`s with slices on the same slugs; the old
   types and gateways are removed.
4. **Definitions on the feed:** only through posts. A definition holds the term and its sources; the owner's own
   take goes in the post that picks it. (Removing the per-definition feed card is still to be built.)
5. **`/blog/music` and `/blog/nature`:** browse all the data by month and by day.
6. **Link groups:** plain tags.
7. **Where the compose routine runs:** a CronJob in jlithgow-ops (`python -m cms.compose_music_month`), not a
   `gateways compose` command.
8. **`featured`:** kept in the schema, still unread.

**Slice shape.** Instead of one slice with a `source` key, each source has its own block type in a `ListField` of
blocks: `music_range`, `nature_range`, `music_picks`, `nature_picks`, `definition_picks`, `link_picks`,
`links_by_tag`. The CMS then validates each kind on save with ordinary block validation, and picked refs are lists
of `DocumentRef` checked for existence and type. The slice stays an application block set, not an Astraeus field
type, as §3 left open.

**Still to come:** moving the posts' embedded `links` into `link` documents, and the editor's pickers for slices
(the multi-ref picker exists; a slice editor and paste-a-URL link creation do not). The outing suggestion helper
(§4) is not started.

