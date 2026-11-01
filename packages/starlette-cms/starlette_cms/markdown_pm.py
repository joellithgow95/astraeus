"""
Markdown → ProseMirror JSON for the editor's schema.

Lets a writer that thinks in markdown (an MCP client, the chat assistant) hand
the CMS content that the collab authority can insert as steps. The output fits
:func:`starlette_cms.collab_verify.editor_schema`: the basic schema plus lists.

Needs ``mistune`` (installed with the ``collab-verify`` extra)::

    >>> doc = markdown_to_pm("# Hello\\n\\nWorld **bold**")
    >>> [n["type"] for n in doc["content"]]
    ['heading', 'paragraph']
"""

from __future__ import annotations

from typing import Any


def _add_mark(pm_node: dict, mark: dict) -> None:
    """Add *mark* to a PM text node's marks (mutates in place)."""
    if pm_node.get("type") != "text":
        return
    pm_node["marks"] = [mark, *pm_node.get("marks", [])]


def _inline_nodes(ast_children: list[dict]) -> list[dict]:
    """Walk mistune inline AST nodes and return PM text, image and break nodes."""
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

        elif ntype in ("strong", "emphasis", "link"):
            if ntype == "link":
                attrs = node.get("attrs", {})
                mark: dict[str, Any] = {
                    "type": "link",
                    "attrs": {"href": attrs.get("url", ""), "title": attrs.get("title") or None},
                }
            else:
                mark = {"type": "strong" if ntype == "strong" else "em"}
            for child in _inline_nodes(node.get("children", [])):
                _add_mark(child, mark)
                result.append(child)

        elif ntype == "image":
            attrs = node.get("attrs", {})
            alt = "".join(
                c.get("raw", "") for c in node.get("children", []) if c.get("type") == "text"
            )
            result.append(
                {
                    "type": "image",
                    "attrs": {
                        "src": attrs.get("url", ""),
                        "alt": alt or None,
                        "title": attrs.get("title") or None,
                    },
                }
            )

        else:
            raw = node.get("raw", "")
            if raw:
                result.append({"type": "text", "text": raw})

    return result


def _paragraph(children: list[dict]) -> dict:
    content = _inline_nodes(children)
    return {"type": "paragraph", "content": content} if content else {"type": "paragraph"}


def _block_nodes(ast_nodes: list[dict]) -> list[dict]:
    """Convert mistune block AST nodes to PM block nodes."""
    result: list[dict] = []

    for node in ast_nodes:
        ntype = node.get("type")

        if ntype == "blank_line":
            continue

        if ntype in ("paragraph", "block_text"):
            result.append(_paragraph(node.get("children", [])))

        elif ntype == "heading":
            level = node.get("attrs", {}).get("level", 1)
            heading: dict[str, Any] = {"type": "heading", "attrs": {"level": level}}
            content = _inline_nodes(node.get("children", []))
            if content:
                heading["content"] = content
            result.append(heading)

        elif ntype == "block_code":
            code = node.get("raw", "").rstrip("\n")
            block: dict[str, Any] = {"type": "code_block"}
            if code:
                block["content"] = [{"type": "text", "text": code}]
            result.append(block)

        elif ntype == "block_quote":
            inner = _block_nodes(node.get("children", [])) or [{"type": "paragraph"}]
            result.append({"type": "blockquote", "content": inner})

        elif ntype == "thematic_break":
            result.append({"type": "horizontal_rule"})

        elif ntype == "list":
            ordered = node.get("attrs", {}).get("ordered", False)
            items = []
            for item in node.get("children", []):
                content = _block_nodes(item.get("children", []))
                # A list item must start with a paragraph in the editor's schema.
                if not content or content[0]["type"] != "paragraph":
                    content.insert(0, {"type": "paragraph"})
                items.append({"type": "list_item", "content": content})
            list_node: dict[str, Any] = {
                "type": "ordered_list" if ordered else "bullet_list",
                "content": items,
            }
            if ordered:
                list_node["attrs"] = {"order": node.get("attrs", {}).get("start") or 1}
            result.append(list_node)

        else:
            raw = node.get("raw", "")
            if raw:
                result.append({"type": "paragraph", "content": [{"type": "text", "text": raw}]})
            elif node.get("children"):
                result.extend(_block_nodes(node["children"]))

    return result


def markdown_to_pm(text: str) -> dict:
    """Parse *text* as markdown into a ProseMirror ``doc`` dict.

    Paragraphs, headings, bold, italic, inline code, links, images, hard breaks,
    code blocks, blockquotes, horizontal rules and bullet and ordered lists map
    to the editor's nodes and marks. Anything else (tables, HTML) is kept as its
    raw text in a paragraph rather than dropped.

    :raises ImportError: if ``mistune`` is not installed.
    """
    import mistune

    if not text or not text.strip():
        return {"type": "doc", "content": []}
    parser = mistune.create_markdown(renderer=None)
    ast: list[dict] = parser(text)  # type: ignore[assignment]
    return {"type": "doc", "content": _block_nodes(ast)}
