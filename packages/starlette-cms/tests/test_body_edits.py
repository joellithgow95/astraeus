"""apply_edits: anchored edits to a rich-text document, as ProseMirror steps."""

from __future__ import annotations

import pytest

pytest.importorskip("prosemirror", reason="needs the collab-verify extra")
pytest.importorskip("mistune", reason="needs the collab-verify extra")

from starlette_cms.body_edits import EditError, apply_edits  # noqa: E402
from starlette_cms.collab_verify import apply_steps, editor_schema  # noqa: E402


def _para(*runs: str | tuple[str, str]) -> dict:
    content = []
    for run in runs:
        if isinstance(run, tuple):
            content.append({"type": "text", "text": run[0], "marks": [{"type": run[1]}]})
        else:
            content.append({"type": "text", "text": run})
    return {"type": "paragraph", "content": content}


def _heading(text: str, level: int = 2) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


DOC = {
    "type": "doc",
    "content": [
        _para("Intro, with ", ("bold words", "strong"), "."),
        _heading("Setup"),
        _para("Install it."),
        _heading("Details", 3),
        _para("Fine print."),
        _heading("Usage"),
        _para("Run it."),
    ],
}


def _texts(doc: dict) -> list[str]:
    return [
        "".join(n.get("text", "") for n in block.get("content", []))
        for block in doc.get("content", [])
    ]


def _apply(*edits: dict, doc: dict = DOC) -> dict:
    steps, new_doc = apply_edits(editor_schema(), doc, list(edits))
    # The steps really produce the document they claim, as the authority checks.
    replay = apply_steps(editor_schema(), doc, steps)
    assert replay.ok and replay.doc == new_doc
    return new_doc


def test_insert_at_the_end_of_a_section_keeps_its_subsections():
    doc = _apply({"insert": "More setup.", "after_heading": "setup"})
    assert _texts(doc) == [
        "Intro, with bold words.",
        "Setup",
        "Install it.",
        "Details",
        "Fine print.",
        "More setup.",
        "Usage",
        "Run it.",
    ]


def test_insert_at_start_and_end():
    doc = _apply({"insert": "First."}, {"insert": "Zero.", "at": "start"})
    assert _texts(doc)[0] == "Zero."
    assert _texts(doc)[-1] == "First."


def test_insert_into_an_empty_doc_replaces_the_blank_paragraph():
    empty = {"type": "doc", "content": [{"type": "paragraph"}]}
    assert _texts(_apply({"insert": "# T\n\nText."}, doc=empty)) == ["T", "Text."]


def test_find_and_replace_inside_a_line_keeps_the_marks_it_replaces():
    doc = _apply({"find": "bold words", "replace": "strong words"})
    run = doc["content"][0]["content"][1]
    assert run == {"type": "text", "text": "strong words", "marks": [{"type": "strong"}]}


def test_replacement_markdown_brings_its_own_marks():
    doc = _apply({"find": "Install it.", "replace": "Install it with `uv`."})
    assert doc["content"][2]["content"][1]["marks"] == [{"type": "code"}]


def test_an_empty_replace_deletes_the_text_or_the_whole_block():
    doc = _apply({"find": "with ", "replace": ""}, {"find": "Fine print.", "replace": ""})
    assert _texts(doc) == [
        "Intro, bold words.",
        "Setup",
        "Install it.",
        "Details",
        "Usage",
        "Run it.",
    ]


def test_a_whole_block_can_become_several():
    doc = _apply({"find": "Install it.", "replace": "Install it.\n\n- with uv\n- or pip"})
    assert [b["type"] for b in doc["content"][2:4]] == ["paragraph", "bullet_list"]


def test_several_blocks_from_part_of_a_line_is_refused():
    with pytest.raises(EditError, match="part of a line"):
        _apply({"find": "Install", "replace": "One.\n\nTwo."})


def test_find_must_occur_exactly_once():
    with pytest.raises(EditError, match="occurs 0 times"):
        _apply({"find": "Not there", "replace": "x"})
    with pytest.raises(EditError, match="occurs 2 times"):
        _apply({"find": "it.", "replace": "x"})


def test_blank_line_separated_find_replaces_a_run_of_blocks():
    doc = _apply({"find": "Details\n\nFine print.", "replace": "### Notes\n\nSmall print."})
    assert _texts(doc)[3:5] == ["Notes", "Small print."]


def test_a_section_is_replaced_or_deleted_by_its_heading():
    doc = _apply({"section": "Setup", "replace": "## Install\n\nOne line."})
    assert _texts(doc) == ["Intro, with bold words.", "Install", "One line.", "Usage", "Run it."]
    doc = _apply({"section": "Usage", "replace": ""})
    assert _texts(doc)[-1] == "Fine print."


def test_deleting_everything_leaves_an_empty_paragraph():
    one = {"type": "doc", "content": [_heading("Only"), _para("x")]}
    assert _apply({"section": "Only", "replace": ""}, doc=one) == {
        "type": "doc",
        "content": [{"type": "paragraph"}],
    }


def test_later_edits_see_earlier_ones():
    doc = _apply(
        {"insert": "## Notes", "at": "end"},
        {"insert": "A note.", "after_heading": "Notes"},
    )
    assert _texts(doc)[-2:] == ["Notes", "A note."]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ({"replace": "x"}, "exactly one of 'find' or 'section'"),
        ({"find": "Intro", "section": "Setup", "replace": "x"}, "exactly one"),
        ({"find": "Intro"}, "give 'insert', or 'replace'"),
        ({"insert": "x", "find": "Intro"}, "'insert' takes"),
        ({"insert": "x", "at": "middle"}, "'at' must be"),
        ({"insert": "x", "after_heading": "Setup", "at": "end"}, "not both"),
        ({"insert": "x", "after_heading": "Nope"}, "the headings are"),
        ({"insert": "   "}, "nothing to insert"),
        ({"replace": "x", "find": "Intro", "where": 1}, "unknown keys"),
    ],
)
def test_bad_edits_are_refused_with_their_index(edit, message):
    with pytest.raises(EditError, match=message) as exc:
        _apply({"insert": "fine"}, edit)
    assert str(exc.value).startswith("edit 1:")


def test_no_edits_is_refused():
    with pytest.raises(EditError, match="non-empty list"):
        apply_edits(editor_schema(), DOC, [])
