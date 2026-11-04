"""Tests for starlette_chat.prosemirror — markdown_to_pm and pm_to_text."""

from __future__ import annotations

import pytest
from starlette_chat.prosemirror import markdown_to_pm, pm_to_text

# ---------------------------------------------------------------------------
# markdown_to_pm
# ---------------------------------------------------------------------------


class TestMarkdownToPm:
    def test_empty_string(self) -> None:
        doc = markdown_to_pm("")
        assert doc == {"type": "doc", "content": []}

    def test_whitespace_only(self) -> None:
        doc = markdown_to_pm("   \n  ")
        assert doc == {"type": "doc", "content": []}

    def test_plain_paragraph(self) -> None:
        doc = markdown_to_pm("Hello world")
        assert doc["type"] == "doc"
        assert len(doc["content"]) == 1
        para = doc["content"][0]
        assert para["type"] == "paragraph"
        assert para["content"] == [{"type": "text", "text": "Hello world"}]

    def test_heading_level_1(self) -> None:
        doc = markdown_to_pm("# Title")
        heading = doc["content"][0]
        assert heading["type"] == "heading"
        assert heading["attrs"]["level"] == 1
        assert heading["content"] == [{"type": "text", "text": "Title"}]

    def test_heading_level_2(self) -> None:
        doc = markdown_to_pm("## Subtitle")
        heading = doc["content"][0]
        assert heading["attrs"]["level"] == 2

    def test_heading_level_3(self) -> None:
        doc = markdown_to_pm("### Section")
        heading = doc["content"][0]
        assert heading["attrs"]["level"] == 3

    def test_bold_text(self) -> None:
        doc = markdown_to_pm("**bold**")
        para = doc["content"][0]
        assert para["type"] == "paragraph"
        text_node = para["content"][0]
        assert text_node["type"] == "text"
        assert text_node["text"] == "bold"
        assert {"type": "strong"} in text_node["marks"]

    def test_italic_text(self) -> None:
        doc = markdown_to_pm("*italic*")
        para = doc["content"][0]
        text_node = para["content"][0]
        assert text_node["type"] == "text"
        assert text_node["text"] == "italic"
        assert {"type": "em"} in text_node["marks"]

    def test_inline_code(self) -> None:
        doc = markdown_to_pm("`code`")
        para = doc["content"][0]
        text_node = para["content"][0]
        assert text_node["type"] == "text"
        assert text_node["text"] == "code"
        assert {"type": "code"} in text_node["marks"]

    def test_code_block_with_language(self) -> None:
        doc = markdown_to_pm("```python\nprint('hi')\n```")
        block = doc["content"][0]
        assert block["type"] == "code_block"
        assert block["attrs"]["params"] == "python"
        assert block["content"][0]["type"] == "text"
        assert "print" in block["content"][0]["text"]

    def test_code_block_without_language(self) -> None:
        doc = markdown_to_pm("```\nsome code\n```")
        block = doc["content"][0]
        assert block["type"] == "code_block"
        assert block["attrs"]["params"] == ""

    def test_blockquote(self) -> None:
        doc = markdown_to_pm("> quoted text")
        block = doc["content"][0]
        assert block["type"] == "blockquote"
        assert len(block["content"]) >= 1
        inner = block["content"][0]
        assert inner["type"] == "paragraph"
        assert inner["content"][0]["text"] == "quoted text"

    def test_unordered_list_three_items(self) -> None:
        doc = markdown_to_pm("- alpha\n- beta\n- gamma")
        block = doc["content"][0]
        assert block["type"] == "bullet_list"
        assert len(block["content"]) == 3
        for item in block["content"]:
            assert item["type"] == "list_item"
            # Each list_item has a paragraph child
            assert item["content"][0]["type"] == "paragraph"

    def test_unordered_list_item_text(self) -> None:
        doc = markdown_to_pm("- alpha\n- beta")
        items = doc["content"][0]["content"]
        texts = [item["content"][0]["content"][0]["text"] for item in items]
        assert texts == ["alpha", "beta"]

    def test_ordered_list(self) -> None:
        doc = markdown_to_pm("1. first\n2. second")
        block = doc["content"][0]
        assert block["type"] == "ordered_list"
        assert len(block["content"]) == 2
        for item in block["content"]:
            assert item["type"] == "list_item"

    def test_image_with_alt(self) -> None:
        doc = markdown_to_pm('![my alt](https://example.com/img.png "My title")')
        para = doc["content"][0]
        # Image is an inline node inside a paragraph
        image_node = para["content"][0]
        assert image_node["type"] == "image"
        assert image_node["attrs"]["src"] == "https://example.com/img.png"
        assert image_node["attrs"]["alt"] == "my alt"
        assert image_node["attrs"]["title"] == "My title"

    def test_mixed_marks_bold_and_italic(self) -> None:
        doc = markdown_to_pm("**bold** and *italic*")
        para = doc["content"][0]
        # Should have at least: bold text node, plain " and ", italic text node
        types_and_marks = [(n["type"], n.get("marks", [])) for n in para["content"]]
        bold_nodes = [m for _, m in types_and_marks if {"type": "strong"} in m]
        italic_nodes = [m for _, m in types_and_marks if {"type": "em"} in m]
        assert len(bold_nodes) >= 1
        assert len(italic_nodes) >= 1

    def test_unknown_ast_node_does_not_raise(self) -> None:
        """Injecting a synthetic unknown node via monkeypatching the parser.

        We test the fallback by calling _block_nodes directly with a fake node.
        """
        from starlette_chat.prosemirror import _block_nodes  # type: ignore[attr-defined]

        fake_nodes = [{"type": "totally_unknown_node", "raw": "some raw text"}]
        # Should NOT raise
        result = _block_nodes(fake_nodes)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "paragraph"

    def test_unknown_ast_node_no_raw_does_not_raise(self) -> None:
        from starlette_chat.prosemirror import _block_nodes  # type: ignore[attr-defined]

        fake_nodes = [{"type": "mystery_block"}]
        result = _block_nodes(fake_nodes)
        assert isinstance(result, list)
        # Should produce an empty paragraph (or nothing), but never raise
        assert all(n["type"] == "paragraph" for n in result)

    def test_result_is_doc(self) -> None:
        doc = markdown_to_pm("# H1\n\nParagraph")
        assert doc["type"] == "doc"
        assert isinstance(doc["content"], list)


# ---------------------------------------------------------------------------
# pm_to_text
# ---------------------------------------------------------------------------


class TestPmToText:
    def _make_doc(self, *blocks: dict) -> dict:
        return {"type": "doc", "content": list(blocks)}

    def _para(self, text: str) -> dict:
        return {"type": "paragraph", "content": [{"type": "text", "text": text}]}

    def _heading(self, level: int, text: str) -> dict:
        return {
            "type": "heading",
            "attrs": {"level": level},
            "content": [{"type": "text", "text": text}],
        }

    def test_extracts_text_from_paragraph(self) -> None:
        doc = self._make_doc(self._para("Hello world"))
        assert pm_to_text(doc) == "Hello world"

    def test_extracts_text_from_heading(self) -> None:
        doc = self._make_doc(self._heading(1, "Big Title"))
        assert pm_to_text(doc) == "Big Title"

    def test_newlines_between_blocks(self) -> None:
        doc = self._make_doc(self._para("First"), self._para("Second"))
        result = pm_to_text(doc)
        assert "First" in result
        assert "Second" in result
        assert result.index("First") < result.index("Second")
        # There should be a newline between them
        assert "\n" in result

    def test_three_blocks_separated(self) -> None:
        doc = self._make_doc(
            self._para("A"),
            self._heading(2, "B"),
            self._para("C"),
        )
        result = pm_to_text(doc)
        lines = result.split("\n")
        assert lines == ["A", "B", "C"]

    def test_empty_doc(self) -> None:
        doc = {"type": "doc", "content": []}
        assert pm_to_text(doc) == ""

    def test_bold_marks_ignored_in_plain_text(self) -> None:
        doc = self._make_doc({
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "plain "},
                {"type": "text", "text": "bold", "marks": [{"type": "strong"}]},
            ],
        })
        assert pm_to_text(doc) == "plain bold"
