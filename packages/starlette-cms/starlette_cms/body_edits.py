"""
Anchored edits to a rich-text field, turned into ProseMirror steps (ADR 027).

An agent edits a body the way it edits a file: by quoting text it has just read
and saying what to put there, or by naming a heading. It never counts
positions. Each edit is resolved against the document *as it is when the edit
is applied* (the collab authority's live copy), so an edit lands where its
anchor is now, and an anchor that a person has since retyped is not found and
the whole batch is refused, rather than landing somewhere else.

Edits, as JSON objects::

    {"find": "exact text", "replace": "markdown"}      # "" deletes it
    {"section": "Heading", "replace": "markdown"}      # the heading and its section
    {"insert": "markdown", "after_heading": "Setup"}  # at the end of that section
    {"insert": "markdown", "at": "end"}                # or "start"

``find`` must occur exactly once. Within one paragraph or heading it replaces
that run of text (markdown that is a single paragraph keeps to the line;
anything more needs ``find`` to be the block's whole text). Blank-line
separated ``find`` text matches a run of whole top-level blocks.

Needs the ``collab-verify`` extra (``prosemirror`` and ``mistune``).
"""

from __future__ import annotations

from typing import Any

#: The keys an edit may carry.
EDIT_KEYS = frozenset({"find", "section", "replace", "insert", "after_heading", "at"})


class EditError(ValueError):
    """An edit could not be applied: its anchor is missing or ambiguous, or the
    result would not fit the schema. Nothing in the batch is applied."""


def apply_edits(schema: Any, doc_json: dict | None, edits: list[dict]) -> tuple[list[dict], dict]:
    """Resolve *edits* in order against *doc_json*; all or nothing.

    :returns: The steps (one per edit) and the document they produce.
    :raises EditError: naming the edit (by index) that failed and why.
    """
    from starlette_cms.collab_verify import EMPTY_DOC

    if not isinstance(edits, list) or not edits:
        raise EditError("'edits' must be a non-empty list")
    doc = schema.node_from_json(doc_json or EMPTY_DOC)
    steps: list[dict] = []
    for i, edit in enumerate(edits):
        try:
            step = _edit_step(schema, doc, edit)
            result = step.apply(doc)
        except EditError as exc:
            raise EditError(f"edit {i}: {exc}") from exc
        except ValueError as exc:  # prosemirror raises where it cannot fit content
            raise EditError(
                f"edit {i}: the result does not fit the editor's schema: {exc}"
            ) from exc
        if result.failed or result.doc is None:
            raise EditError(f"edit {i}: {result.failed}")
        doc = result.doc
        steps.append(dict(step.to_json()))
    return steps, dict(doc.to_json())


# ---------------------------------------------------------------------------
# One edit
# ---------------------------------------------------------------------------


def _edit_step(schema: Any, doc: Any, edit: Any) -> Any:
    if not isinstance(edit, dict):
        raise EditError("an edit must be an object")
    unknown = set(edit) - EDIT_KEYS
    if unknown:
        raise EditError(f"unknown keys {sorted(unknown)}; an edit takes {sorted(EDIT_KEYS)}")

    if "insert" in edit:
        if "find" in edit or "section" in edit or "replace" in edit:
            raise EditError(
                "'insert' takes 'after_heading' or 'at', not 'find'/'section'/'replace'"
            )
        return _insert_step(schema, doc, edit)
    if "replace" not in edit:
        raise EditError("give 'insert', or 'replace' with 'find' or 'section'")
    if ("find" in edit) == ("section" in edit):
        raise EditError("'replace' takes exactly one of 'find' or 'section'")
    if "section" in edit:
        start, end = _section_range(doc, str(edit["section"]))
        return _replace_blocks(schema, doc, start, end, str(edit["replace"]))
    return _find_replace_step(schema, doc, str(edit["find"]), str(edit["replace"]))


def _blocks(schema: Any, markdown: str) -> Any:
    """Markdown as a Fragment of block nodes, checked against the schema."""
    from prosemirror.model import Fragment

    from starlette_cms.markdown_pm import markdown_to_pm

    try:
        fragment = Fragment.from_json(schema, markdown_to_pm(markdown)["content"])
        fragment.for_each(lambda node, *_: node.check())
    except Exception as exc:  # noqa: BLE001 - prosemirror raises plain errors
        raise EditError(f"the markdown does not fit the editor's schema: {exc}") from exc
    return fragment


def _is_blank(doc: Any) -> bool:
    first = doc.first_child
    return (
        doc.child_count == 1
        and first is not None
        and first.type.name == "paragraph"
        and first.content.size == 0
    )


def _insert_step(schema: Any, doc: Any, edit: dict) -> Any:
    from prosemirror.model import Slice
    from prosemirror.transform import ReplaceStep

    fragment = _blocks(schema, str(edit["insert"]))
    if fragment.size == 0:
        raise EditError("nothing to insert")
    heading = edit.get("after_heading")
    at = edit.get("at", "end")
    if _is_blank(doc):
        # An empty field is one empty paragraph: replace it, don't keep it.
        start, end = 0, doc.content.size
    elif heading is not None:
        if "at" in edit:
            raise EditError("give 'after_heading' or 'at', not both")
        start = end = _section_range(doc, str(heading))[1]
    elif at == "start":
        start = end = 0
    elif at == "end":
        start = end = doc.content.size
    else:
        raise EditError(f"'at' must be 'start' or 'end', not {at!r}")
    return ReplaceStep(start, end, Slice(fragment, 0, 0))


def _replace_blocks(schema: Any, doc: Any, start: int, end: int, markdown: str) -> Any:
    """Replace top-level blocks in ``start..end`` with *markdown*'s blocks."""
    from prosemirror.model import Fragment, Slice
    from prosemirror.transform import ReplaceStep

    fragment = _blocks(schema, markdown)
    if fragment.size == 0 and start == 0 and end == doc.content.size:
        # Emptying the whole field leaves the empty paragraph the editor expects.
        fragment = Fragment.from_(schema.nodes["paragraph"].create())
    return ReplaceStep(start, end, Slice(fragment, 0, 0))


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


def _headings(doc: Any) -> list[str]:
    return [
        doc.child(i).text_content
        for i in range(doc.child_count)
        if doc.child(i).type.name == "heading"
    ]


def _section_range(doc: Any, heading: str) -> tuple[int, int]:
    """Start and end of the section under the top-level *heading*.

    It runs from the heading to the next heading of the same or a higher level.
    """
    wanted = heading.strip().casefold()
    pos = 0
    start: int | None = None
    level = 0
    for i in range(doc.child_count):
        child = doc.child(i)
        if child.type.name == "heading":
            if start is not None and child.attrs["level"] <= level:
                return start, pos
            if start is None and child.text_content.strip().casefold() == wanted:
                start, level = pos, child.attrs["level"]
        pos += child.node_size
    if start is not None:
        return start, pos
    raise EditError(f"no heading {heading!r}; the headings are {_headings(doc)!r}")


def _textblock_matches(doc: Any, find: str) -> list[tuple[Any, int, int, int]]:
    """Every occurrence of *find* inside one textblock.

    :returns: ``(block, block_pos, from, to)``, document positions of the text.
    """
    found: list[tuple[Any, int, int, int]] = []

    def visit(node: Any, pos: int, *_: Any) -> bool:
        if not node.is_textblock:
            return True
        # Map each character of the block's text to its document position.
        chars: list[int] = []
        offset = pos + 1
        for j in range(node.child_count):
            child = node.child(j)
            if child.is_text:
                chars.extend(range(offset, offset + len(child.text)))
            offset += child.node_size
        text = node.text_content
        idx = text.find(find)
        while idx != -1:
            found.append((node, pos, chars[idx], chars[idx + len(find) - 1] + 1))
            idx = text.find(find, idx + 1)
        return False

    doc.descendants(visit)
    return found


def _block_run_matches(doc: Any, parts: list[str]) -> list[tuple[int, int]]:
    """Runs of whole top-level blocks whose texts are *parts*, in order."""
    texts = [doc.child(i).text_content.strip() for i in range(doc.child_count)]
    starts = [0]
    for i in range(doc.child_count):
        starts.append(starts[-1] + doc.child(i).node_size)
    runs = []
    for i in range(len(texts) - len(parts) + 1):
        if texts[i : i + len(parts)] == parts:
            runs.append((starts[i], starts[i + len(parts)]))
    return runs


def _find_replace_step(schema: Any, doc: Any, find: str, replace: str) -> Any:
    from prosemirror.model import Fragment, Slice
    from prosemirror.transform import ReplaceStep

    if not find.strip():
        raise EditError("'find' is empty")
    parts = [p.strip() for p in find.strip().split("\n\n") if p.strip()]
    if len(parts) > 1:
        runs = _block_run_matches(doc, parts)
        if len(runs) != 1:
            raise EditError(
                f"'find' spans {len(parts)} blocks and matches {len(runs)} runs of whole "
                "blocks; it must match exactly one (quote each paragraph's full text, "
                "separated by a blank line)"
            )
        return _replace_blocks(schema, doc, *runs[0], replace)

    matches = _textblock_matches(doc, find)
    if len(matches) != 1:
        hint = "copy it exactly from the current draft" if not matches else "quote more of it"
        raise EditError(f"'find' occurs {len(matches)} times; it must occur once ({hint})")
    block, block_pos, from_, to = matches[0]
    whole_block = from_ == block_pos + 1 and to == block_pos + block.node_size - 1

    fragment = _blocks(schema, replace)
    if fragment.child_count == 1 and fragment.first_child.type.name == "paragraph":
        inline = fragment.first_child.content
        if not whole_block or block.type.name == "paragraph":
            # Plain replacement text keeps the marks of the text it replaces.
            marks = doc.resolve(from_ + 1).marks() if to > from_ else []
            if marks and all(not n.marks for n in _children(inline)):
                inline = Fragment.from_array([n.mark(marks) for n in _children(inline)])
            return ReplaceStep(from_, to, Slice(inline, 0, 0))
    if fragment.size == 0:
        if whole_block:
            return ReplaceStep(block_pos, block_pos + block.node_size, Slice.empty)
        return ReplaceStep(from_, to, Slice.empty)
    if not whole_block:
        raise EditError(
            "replacing part of a line with more than one paragraph: make 'find' the "
            "block's whole text, or keep 'replace' to one paragraph"
        )
    return ReplaceStep(block_pos, block_pos + block.node_size, Slice(fragment, 0, 0))


def _children(fragment: Any) -> list[Any]:
    return [fragment.child(i) for i in range(fragment.child_count)]
