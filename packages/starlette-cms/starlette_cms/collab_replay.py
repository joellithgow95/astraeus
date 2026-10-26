"""
Rebuild rich-text fields from the collab step log (``cms_step``).

Every accepted editor step is written to ``cms_step``, so the log can bring
back text the stored draft lost. Replaying it used to be unreliable, and the
failures looked like steps "not applying":

- Nothing recorded what a session's first step applied to. The history endpoint
  offered the *published* body, but a session starts from the draft as it was
  when the session opened, after any REST or MCP write. Positions computed
  against one document land in the wrong place in another.
- Rows were read in version order, but ``draft_version`` restarts at 0 on
  publish and discard, so later cycles interleaved with earlier ones.
- A write outside the editor took a version without leaving a step, so the
  log had a gap the replay could not see.

Now each session writes a baseline row (``stepType: "baseline"``, the field and
its document) before its first step, writes from outside the editor are logged
as steps (:meth:`~starlette_cms.collab.CollabManager.apply_external_update`),
and :func:`replay_rows` reads rows in the order they were written. A log from
before baselines existed can still be replayed by passing the document the
steps start from (for example the draft a lost edit was written over) and the
version range of the session.

Usage::

    segments = await replay_document(doc_id, field="body_markdown")
    for seg in segments:
        print(seg.base_version, seg.version, seg.error, seg.doc)

or, against a restored database::

    python -m starlette_cms.collab_replay sqlite:///content.db DOC_ID --field body_markdown
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, Any

from starlette_cms.collab import BASELINE_STEP_TYPE

if TYPE_CHECKING:
    from prosemirror.model import Schema


@dataclass
class ReplaySegment:
    """One editing session's steps, applied to the document they start from."""

    #: The rich-text field, or ``None`` for a legacy segment with no baseline row.
    field: str | None
    #: Version of the document the first step applies to.
    base_version: int
    #: That document (ProseMirror JSON).
    base_doc: dict[str, Any] | None
    #: The document after every step that applied.
    doc: dict[str, Any] | None = None
    #: Version reached: ``base_version`` plus the steps applied.
    version: int = 0
    #: Steps applied.
    applied: int = 0
    #: Why replay stopped early, or ``None`` if every step applied.
    error: str | None = None
    #: Places where the stored versions jump (a write that left no step, a lost
    #: batch). The replay carries on, but text near a gap may be off.
    gaps: list[str] = dc_field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True if every step applied and the versions ran without a gap."""
        return self.error is None and not self.gaps


def _parse(raw: Any) -> dict[str, Any] | None:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def replay_rows(
    rows: list[dict[str, Any]],
    *,
    field: str | None = None,
    base_doc: dict[str, Any] | None = None,
    from_version: int | None = None,
    to_version: int | None = None,
    schema: Schema | None = None,
) -> list[ReplaySegment]:
    """Replay ``cms_step`` rows, one segment per editing session.

    :param rows: Rows with ``version`` and ``step_data`` (and ideally ``id``),
        in the order they were written. Sorted by ``id`` when every row has one.
    :param field: Only replay sessions on this field. Steps after a baseline for
        another field are skipped.
    :param base_doc: For rows written before baselines existed: the document
        the first of them applies to. Without it those rows are skipped.
    :param from_version: Legacy rows only: the version *base_doc* is at. Rows at
        or below it are skipped.
    :param to_version: Stop at this version (all segments).
    :param schema: The editor schema; defaults to
        :func:`~starlette_cms.collab_verify.editor_schema`.
    :raises ImportError: without the ``collab-verify`` extra.
    """
    from prosemirror.transform import Step

    from starlette_cms.collab_verify import EMPTY_DOC, editor_schema

    schema = schema or editor_schema()
    if rows and all("id" in r for r in rows):
        rows = sorted(rows, key=lambda r: r["id"])

    segments: list[ReplaySegment] = []
    current: ReplaySegment | None = None
    node: Any = None
    skipping = False

    def start(seg: ReplaySegment) -> None:
        nonlocal current, node, skipping
        current = seg
        segments.append(seg)
        skipping = False
        try:
            node = schema.node_from_json(seg.base_doc or EMPTY_DOC)
            seg.doc = node.to_json()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            node = None
            seg.error = f"base document does not fit the schema: {exc}"

    if base_doc is not None:
        start(ReplaySegment(field=field, base_version=from_version or 0, base_doc=base_doc))
        if current is not None:
            current.version = current.base_version

    for row in rows:
        data = _parse(row.get("step_data"))
        version = row.get("version")
        if data is None or not isinstance(version, int):
            continue

        if data.get("stepType") == BASELINE_STEP_TYPE:
            if field is not None and data.get("field") != field:
                current, node, skipping = None, None, True
                continue
            seg = ReplaySegment(
                field=data.get("field"), base_version=version, base_doc=data.get("doc")
            )
            seg.version = version
            start(seg)
            continue

        if skipping or current is None:
            continue  # another field's session, or legacy rows with no base given
        if current.field is None and from_version is not None and version <= from_version:
            continue
        if to_version is not None and version > to_version:
            continue
        if current.error is not None:
            continue
        if version != current.version + 1:
            current.gaps.append(f"version {current.version} -> {version}")
        try:
            result = Step.from_json(schema, data).apply(node)
        except Exception as exc:  # noqa: BLE001
            current.error = f"step at version {version} could not be applied: {exc}"
            continue
        if result.failed or result.doc is None:
            current.error = f"step at version {version} failed: {result.failed}"
            continue
        node = result.doc
        current.doc = node.to_json()
        current.version = version
        current.applied += 1

    return segments


async def load_step_rows(document_id: str) -> list[dict[str, Any]]:
    """Every ``cms_step`` row of *document_id*, in the order they were written."""
    from starlette_cms.tables import CMSStep

    return (
        await CMSStep.select(
            CMSStep._meta.primary_key, CMSStep.version, CMSStep.client_id, CMSStep.step_data
        )
        .where(CMSStep.document_id == document_id)
        .order_by(CMSStep._meta.primary_key, ascending=True)
        .run()
    )


async def replay_document(document_id: str, **kwargs: Any) -> list[ReplaySegment]:
    """Load *document_id*'s step log and :func:`replay_rows` it."""
    return replay_rows(await load_step_rows(document_id), **kwargs)


def _segment_json(seg: ReplaySegment) -> dict[str, Any]:
    return {
        "field": seg.field,
        "base_version": seg.base_version,
        "version": seg.version,
        "applied": seg.applied,
        "complete": seg.complete,
        "error": seg.error,
        "gaps": seg.gaps,
        "doc": seg.doc,
    }


async def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m starlette_cms.collab_replay",
        description="Replay a document's collab step log and print each session's result.",
    )
    parser.add_argument("database_url", help="e.g. sqlite:///content.db (a restored copy)")
    parser.add_argument("document_id")
    parser.add_argument("--field", default=None)
    parser.add_argument(
        "--base-json",
        default=None,
        help="Legacy logs: a JSON file holding the document the steps start from",
    )
    parser.add_argument("--from-version", type=int, default=None)
    parser.add_argument("--to-version", type=int, default=None)
    args = parser.parse_args(argv)

    from starlette_cms.db import CMSDatabase

    db = CMSDatabase(args.database_url)
    await db.init()
    try:
        base = None
        if args.base_json:
            with open(args.base_json) as fh:
                base = json.load(fh)
        segments = await replay_document(
            args.document_id,
            field=args.field,
            base_doc=base,
            from_version=args.from_version,
            to_version=args.to_version,
        )
    finally:
        await db.close()
    print(json.dumps([_segment_json(s) for s in segments], indent=1, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())
