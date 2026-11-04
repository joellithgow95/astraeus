"""Tests for starlette_chat.diff — diff_docs and node_size."""

from __future__ import annotations

import pytest
from starlette_chat.diff import _block_start_positions, diff_docs, node_size

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _doc(*blocks: dict) -> dict:
    return {"type": "doc", "content": list(blocks)}


# ---------------------------------------------------------------------------
# node_size
# ---------------------------------------------------------------------------


class TestNodeSize:
    def test_text_node(self) -> None:
        assert node_size({"type": "text", "text": "abc"}) == 3

    def test_empty_text_node(self) -> None:
        assert node_size({"type": "text", "text": ""}) == 0

    def test_paragraph_abc(self) -> None:
        # open(1) + "abc"(3) + close(1) = 5
        assert node_size(_para("abc")) == 5

    def test_paragraph_empty(self) -> None:
        assert node_size({"type": "paragraph", "content": []}) == 2

    def test_heading(self) -> None:
        node = {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Hi"}]}
        assert node_size(node) == 4  # 2 + 2

    def test_hard_break(self) -> None:
        assert node_size({"type": "hard_break"}) == 1

    def test_image(self) -> None:
        assert node_size({"type": "image", "attrs": {}}) == 1


# ---------------------------------------------------------------------------
# _block_start_positions
# ---------------------------------------------------------------------------


class TestBlockStartPositions:
    def test_empty(self) -> None:
        assert _block_start_positions([]) == [0]

    def test_two_paragraphs(self) -> None:
        blocks = [_para("ab"), _para("cd")]
        # para("ab") size = 2+2 = 4; para("cd") size = 4
        positions = _block_start_positions(blocks)
        assert positions == [0, 4, 8]

    def test_sentinel_is_past_last(self) -> None:
        blocks = [_para("x")]
        positions = _block_start_positions(blocks)
        assert positions[-1] == positions[0] + node_size(blocks[0])


# ---------------------------------------------------------------------------
# diff_docs
# ---------------------------------------------------------------------------


class TestDiffDocs:
    def test_identical_docs_return_empty(self) -> None:
        doc = _doc(_para("Hello"), _para("World"))
        assert diff_docs(doc, doc) == []

    def test_both_empty_return_empty(self) -> None:
        assert diff_docs(_doc(), _doc()) == []

    def test_single_paragraph_changed(self) -> None:
        current = _doc(_para("Original"))
        new = _doc(_para("Changed"))
        steps = diff_docs(current, new)
        assert len(steps) == 1
        step = steps[0]
        assert step["stepType"] == "replace"
        # from should be 0 (start of first block: doc content starts at 0)
        assert step["from"] == 0
        # to should be node_size(para("Original"))
        expected_to = node_size(_para("Original"))
        assert step["to"] == expected_to
        assert step["slice"]["content"] == [_para("Changed")]

    def test_paragraph_appended(self) -> None:
        current = _doc(_para("First"))
        new = _doc(_para("First"), _para("Second"))
        steps = diff_docs(current, new)
        assert len(steps) == 1
        step = steps[0]
        # insert: from == to
        assert step["from"] == step["to"]
        assert step["slice"]["content"] == [_para("Second")]

    def test_paragraph_deleted(self) -> None:
        current = _doc(_para("Keep"), _para("Delete me"))
        new = _doc(_para("Keep"))
        steps = diff_docs(current, new)
        assert len(steps) == 1
        step = steps[0]
        assert step["slice"]["content"] == []
        # The deleted block's range
        assert step["from"] < step["to"]

    def test_first_and_last_replaced_middle_unchanged(self) -> None:
        current = _doc(_para("A"), _para("Middle"), _para("C"))
        new = _doc(_para("X"), _para("Middle"), _para("Z"))
        steps = diff_docs(current, new)
        # Should produce exactly 2 steps (one for first, one for last)
        assert len(steps) == 2
        # Steps are in reverse document order, so last block's step comes first
        # Both should be replace steps
        for step in steps:
            assert step["stepType"] == "replace"

    def test_empty_current_populated_new(self) -> None:
        current = _doc()
        new = _doc(_para("Hello"), _para("World"))
        steps = diff_docs(current, new)
        assert len(steps) == 1
        step = steps[0]
        # Single insert step
        assert step["from"] == step["to"] == 0
        assert len(step["slice"]["content"]) == 2

    def test_populated_current_empty_new(self) -> None:
        current = _doc(_para("Hello"), _para("World"))
        new = _doc()
        steps = diff_docs(current, new)
        assert len(steps) == 1
        step = steps[0]
        assert step["slice"]["content"] == []

    def test_all_steps_have_valid_positions(self) -> None:
        """from <= to for every step."""
        current = _doc(_para("A"), _para("B"), _para("C"))
        new = _doc(_para("X"), _para("B"), _para("Y"))
        steps = diff_docs(current, new)
        for step in steps:
            assert step["from"] <= step["to"], (
                f"Invalid step: from={step['from']} > to={step['to']}"
            )

    def test_steps_in_reverse_order(self) -> None:
        """Steps should be returned highest-position-first."""
        current = _doc(_para("A"), _para("B"), _para("C"))
        new = _doc(_para("X"), _para("Y"), _para("Z"))
        steps = diff_docs(current, new)
        if len(steps) > 1:
            for i in range(len(steps) - 1):
                assert steps[i]["from"] >= steps[i + 1]["from"], (
                    "Steps are not in reverse document order"
                )

    def test_step_structure(self) -> None:
        """Each step has required fields."""
        current = _doc(_para("Old"))
        new = _doc(_para("New"))
        steps = diff_docs(current, new)
        assert len(steps) == 1
        step = steps[0]
        assert "stepType" in step
        assert "from" in step
        assert "to" in step
        assert "slice" in step
        assert "content" in step["slice"]
        assert "openStart" in step["slice"]
        assert "openEnd" in step["slice"]
