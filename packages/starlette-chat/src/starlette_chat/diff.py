"""
ProseMirror document differ.

Produces a list of ProseMirror ReplaceStep dicts that transforms one
document into another, operating at block (paragraph/heading) granularity
to interleave correctly with concurrent human edits.

Implemented in Phase CH-2.
"""

from __future__ import annotations

import difflib
import json

# ---------------------------------------------------------------------------
# Node size calculation
# ---------------------------------------------------------------------------

def node_size(node: dict) -> int:
    """Return the ProseMirror token size of *node*.

    ProseMirror counts positions as token boundaries:
    - Each non-leaf block contributes 2 tokens (open + close) plus the
      sizes of its children.
    - Each leaf text node contributes one token per character.
    - hard_break and image are leaf nodes sized at 1.

    ::

        >>> node_size({"type": "paragraph", "content": [{"type": "text", "text": "abc"}]})
        5
    """
    ntype = node.get("type", "")

    if ntype == "text":
        return len(node.get("text", ""))

    if ntype in ("hard_break", "image"):
        return 1

    # All other nodes: 2 (open + close) + sum of children
    children = node.get("content", [])
    return 2 + sum(node_size(child) for child in children)


def _block_start_positions(blocks: list[dict]) -> list[int]:
    """Return start positions for each block plus a sentinel past the last.

    ProseMirror positions index into the doc node's *content*: the doc's own
    open token is not counted, so the first block starts at position 0 (position
    1 is already inside that block).

    ::

        >>> _block_start_positions([
        ...     {"type": "paragraph", "content": [{"type": "text", "text": "ab"}]},
        ...     {"type": "paragraph", "content": [{"type": "text", "text": "cd"}]},
        ... ])
        [0, 4, 8]
    """
    positions: list[int] = []
    pos = 0
    for block in blocks:
        positions.append(pos)
        pos += node_size(block)
    positions.append(pos)  # sentinel
    return positions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_docs(current: dict, new: dict) -> list[dict]:
    """Return a list of ProseMirror ReplaceStep dicts that transforms current → new.

    Steps are at block granularity — no character-level diffing.  Steps are
    returned in **reverse document order** (highest position first) so that
    each step's ``from``/``to`` positions remain valid when the steps are
    applied sequentially.

    Returns an empty list when the documents are identical.

    ::

        >>> diff_docs({"type": "doc", "content": []},
        ...           {"type": "doc", "content": []})
        []
    """
    current_blocks: list[dict] = current.get("content", [])
    new_blocks: list[dict] = new.get("content", [])

    # Stringify blocks for equality comparison (order-stable, sort_keys for
    # determinism)
    current_keys = [json.dumps(b, sort_keys=True) for b in current_blocks]
    new_keys = [json.dumps(b, sort_keys=True) for b in new_blocks]

    if current_keys == new_keys:
        return []

    current_positions = _block_start_positions(current_blocks)

    matcher = difflib.SequenceMatcher(None, current_keys, new_keys, autojunk=False)
    opcodes = matcher.get_opcodes()

    steps: list[dict] = []

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue

        from_pos = current_positions[i1]
        to_pos = current_positions[i2]

        if tag == "replace":
            slice_content = new_blocks[j1:j2]
        elif tag == "delete":
            slice_content = []
        else:  # insert
            slice_content = new_blocks[j1:j2]

        steps.append({
            "stepType": "replace",
            "from": from_pos,
            "to": to_pos,
            "slice": {
                "content": slice_content,
                "openStart": 0,
                "openEnd": 0,
            },
        })

    # Return in reverse document order so callers can apply sequentially
    # without position shift.
    steps.reverse()
    return steps
