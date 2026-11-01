"""markdown_to_pm: markdown in, a document that fits the editor's schema out."""

from __future__ import annotations

import pytest

pytest.importorskip("prosemirror", reason="needs the collab-verify extra")
pytest.importorskip("mistune", reason="needs the collab-verify extra")

from starlette_cms.collab_verify import editor_schema  # noqa: E402
from starlette_cms.markdown_pm import markdown_to_pm  # noqa: E402

MARKDOWN = """## Section

Some **bold**, *italic*, `code` and a [link](https://x.test "Title").

- one
- two
  - nested

3. three
4. four

> quoted

---

```py
x = 1
```

![Alt text](https://img.test/a.png)
"""


def test_every_supported_construct_fits_the_editor_schema():
    doc = markdown_to_pm(MARKDOWN)
    node = editor_schema().node_from_json(doc)
    node.check()
    assert [c.type.name for c in node.content.content] == [
        "heading",
        "paragraph",
        "bullet_list",
        "ordered_list",
        "blockquote",
        "horizontal_rule",
        "code_block",
        "paragraph",
    ]


def test_links_keep_their_href_and_title():
    para = markdown_to_pm('[a](https://x.test "T")')["content"][0]
    assert para["content"] == [
        {
            "type": "text",
            "text": "a",
            "marks": [{"type": "link", "attrs": {"href": "https://x.test", "title": "T"}}],
        }
    ]


def test_an_ordered_list_keeps_its_start_number():
    assert markdown_to_pm("3. a\n4. b")["content"][0]["attrs"] == {"order": 3}


def test_blank_markdown_is_an_empty_doc():
    assert markdown_to_pm("  \n") == {"type": "doc", "content": []}


def test_unsupported_markdown_keeps_its_text():
    doc = markdown_to_pm("| a | b |\n|---|---|\n| 1 | 2 |")
    editor_schema().node_from_json(doc).check()
    assert "a" in str(doc)
