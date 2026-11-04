"""
Python ProseMirror document builder.

Converts markdown text to a ProseMirror document dict and back to plain
text for LLM context injection.

Implemented in Phase CH-2.
"""

from __future__ import annotations

import mistune

# ---------------------------------------------------------------------------
# Internal helpers — inline node walking
# ---------------------------------------------------------------------------

def _inline_nodes(ast_children: list[dict]) -> list[dict]:
    """Walk a list of inline mistune AST nodes and return PM text/break nodes.

    Each plain text run becomes a ``{"type": "text", "text": "..."}`` node,
    optionally with a ``marks`` list for strong/em/code styling (the mark names the
    editor's schema uses).
    ``softbreak`` and ``linebreak`` both become a ``hard_break`` node.
    """
    result: list[dict] = []

    for node in ast_children:
        ntype = node.get("type")

        if ntype == "text":
            raw = node.get("raw", "")
            if raw:
                result.append({"type": "text", "text": raw})

        elif ntype in ("softbreak", "linebreak"):
            result.append({"type": "hard_break"})

        elif ntype == "codespan":
            raw = node.get("raw", "")
            if raw:
                result.append({"type": "text", "text": raw, "marks": [{"type": "code"}]})

        elif ntype == "strong":
            for child_node in _inline_nodes(node.get("children", [])):
                _add_mark(child_node, {"type": "strong"})
                result.append(child_node)

        elif ntype == "emphasis":
            for child_node in _inline_nodes(node.get("children", [])):
                _add_mark(child_node, {"type": "em"})
                result.append(child_node)

        elif ntype == "image":
            attrs = node.get("attrs", {})
            # image alt text is in children text nodes
            alt_parts = [c.get("raw", "") for c in node.get("children", []) if c.get("type") == "text"]
            alt = "".join(alt_parts)
            result.append({
                "type": "image",
                "attrs": {
                    "src": attrs.get("url", ""),
                    "alt": alt,
                    "title": attrs.get("title") or "",
                },
            })

        else:
            # Unknown inline — try to emit raw text, fall back to empty
            raw = node.get("raw", "")
            if raw:
                result.append({"type": "text", "text": raw})

    return result


def _add_mark(pm_node: dict, mark: dict) -> None:
    """Prepend *mark* to a PM text node's marks list (mutates in place)."""
    if pm_node.get("type") != "text":
        return
    marks = list(pm_node.get("marks", []))
    marks.insert(0, mark)
    pm_node["marks"] = marks


# ---------------------------------------------------------------------------
# Block-level converter
# ---------------------------------------------------------------------------

def _block_nodes(ast_nodes: list[dict]) -> list[dict]:
    """Convert a flat list of mistune block AST nodes to PM block nodes."""
    result: list[dict] = []

    for node in ast_nodes:
        ntype = node.get("type")

        if ntype == "blank_line":
            continue

        elif ntype == "paragraph":
            content = _inline_nodes(node.get("children", []))
            result.append({"type": "paragraph", "content": content})

        elif ntype == "heading":
            level = node.get("attrs", {}).get("level", 1)
            content = _inline_nodes(node.get("children", []))
            result.append({"type": "heading", "attrs": {"level": level}, "content": content})

        elif ntype == "block_code":
            code = node.get("raw", "")
            info = node.get("attrs", {}).get("info") or ""
            result.append({
                "type": "code_block",
                "attrs": {"params": info},
                "content": [{"type": "text", "text": code}],
            })

        elif ntype == "block_quote":
            inner = _block_nodes(node.get("children", []))
            result.append({"type": "blockquote", "content": inner})

        elif ntype == "list":
            attrs = node.get("attrs", {})
            ordered = attrs.get("ordered", False)
            list_type = "ordered_list" if ordered else "bullet_list"
            items = []
            for item in node.get("children", []):
                item_content = _list_item_content(item)
                items.append({"type": "list_item", "content": item_content})
            result.append({"type": list_type, "content": items})

        else:
            # Unknown block — wrap raw/text in a fallback paragraph
            raw = node.get("raw", "")
            if raw:
                result.append({"type": "paragraph", "content": [{"type": "text", "text": raw}]})
            else:
                # Try to recurse into children if any; otherwise emit empty paragraph
                children = node.get("children", [])
                if children:
                    result.extend(_block_nodes(children))
                else:
                    result.append({"type": "paragraph", "content": []})

    return result


def _list_item_content(item_node: dict) -> list[dict]:
    """Convert a list_item mistune node into a list of PM paragraph nodes.

    Tight list items use ``block_text`` children; loose items use ``paragraph``
    children.  Either way we normalise to PM paragraphs.
    """
    paragraphs: list[dict] = []
    for child in item_node.get("children", []):
        ctype = child.get("type")
        if ctype in ("block_text", "paragraph"):
            content = _inline_nodes(child.get("children", []))
            paragraphs.append({"type": "paragraph", "content": content})
        elif ctype == "list":
            # Nested list — delegate back to block nodes
            paragraphs.extend(_block_nodes([child]))
        else:
            content = _inline_nodes(child.get("children", []))
            if content:
                paragraphs.append({"type": "paragraph", "content": content})
    return paragraphs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_md_parser = mistune.create_markdown(renderer=None)


def markdown_to_pm(text: str) -> dict:
    """Parse markdown text into a ProseMirror document dict.

    Supports the node types used by starlette-editor:
    paragraph, heading, text (strong/em/code marks), code_block,
    blockquote, bullet_list, ordered_list, list_item, image, hard_break.

    Unknown node types are wrapped in a paragraph rather than silently
    dropped.

    ::

        >>> doc = markdown_to_pm("# Hello\\n\\nWorld **bold**")
        >>> doc["type"]
        'doc'
        >>> doc["content"][0]["type"]
        'heading'
    """
    if not text or not text.strip():
        return {"type": "doc", "content": []}

    ast: list[dict] = _md_parser(text)  # type: ignore[assignment]
    blocks = _block_nodes(ast)
    return {"type": "doc", "content": blocks}


def pm_to_text(doc: dict) -> str:
    """Extract plain text from a ProseMirror document dict.

    Used to build LLM context from the current draft body.  A newline
    separates each top-level block.

    ::

        >>> pm_to_text({"type": "doc", "content": [
        ...     {"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}
        ... ]})
        'hi'
    """
    lines: list[str] = []
    for block in doc.get("content", []):
        text = _node_to_text(block)
        if text:
            lines.append(text)
    return "\n".join(lines)


def _node_to_text(node: dict) -> str:
    """Recursively extract all text from a PM node, joining inline content."""
    ntype = node.get("type", "")
    if ntype == "text":
        return node.get("text", "")
    if ntype == "hard_break":
        return "\n"
    parts: list[str] = []
    for child in node.get("content", []):
        parts.append(_node_to_text(child))
    return "".join(parts)
