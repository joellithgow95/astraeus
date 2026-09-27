"""
Tests for field_meta() on all field types, including block container fields.
"""

from __future__ import annotations

from starlette_cms.fields import (
    BlockField,
    BoolField,
    DocumentRef,
    DoodleField,
    ImageField,
    JSONField,
    ListField,
    NumberField,
    RichTextField,
    SelectField,
    TextField,
    URLField,
)

# ---------------------------------------------------------------------------
# ListField
# ---------------------------------------------------------------------------


def test_list_field_meta_has_field_type_block_list():
    meta = ListField().field_meta()
    assert meta["field_type"] == "block_list"


def test_list_field_meta_with_item_type():
    class MyBlock:
        pass

    meta = ListField(MyBlock).field_meta()
    assert meta["field_type"] == "block_list"


def test_list_field_meta_with_blocks_kwarg():
    class BlockA:
        pass

    class BlockB:
        pass

    meta = ListField(blocks=[BlockA, BlockB]).field_meta()
    assert meta["field_type"] == "block_list"


def test_list_field_meta_passes_through_base_attrs():
    meta = ListField(label="Content Blocks", help_text="Add blocks here").field_meta()
    assert meta["field_type"] == "block_list"
    assert meta["label"] == "Content Blocks"
    assert meta["help_text"] == "Add blocks here"


# ---------------------------------------------------------------------------
# BlockField
# ---------------------------------------------------------------------------


def test_block_field_meta_has_field_type_block():
    meta = BlockField().field_meta()
    assert meta["field_type"] == "block"


def test_block_field_meta_with_block_type_kwarg():
    class HeroBlock:
        pass

    meta = BlockField(block_type=HeroBlock).field_meta()
    assert meta["field_type"] == "block"


def test_block_field_meta_passes_through_base_attrs():
    meta = BlockField(label="Hero", immutable=True).field_meta()
    assert meta["field_type"] == "block"
    assert meta["label"] == "Hero"
    assert meta["immutable"] is True


# ---------------------------------------------------------------------------
# Sanity-check other field types are not broken
# ---------------------------------------------------------------------------


def test_rich_text_field_meta():
    assert RichTextField().field_meta()["field_type"] == "rich_text"


def test_image_field_meta():
    assert ImageField().field_meta()["field_type"] == "image"


def test_text_field_has_no_field_type():
    assert "field_type" not in TextField().field_meta()


def test_bool_field_has_no_field_type():
    assert "field_type" not in BoolField().field_meta()


def test_number_field_has_no_field_type():
    assert "field_type" not in NumberField().field_meta()


def test_select_field_has_choices():
    meta = SelectField(choices=["a", "b"]).field_meta()
    assert meta["choices"] == ["a", "b"]
    assert "field_type" not in meta


def test_document_ref_field_type():
    meta = DocumentRef(block_type="post").field_meta()
    assert meta["field_type"] == "document_ref"


def test_url_field_format():
    meta = URLField().field_meta()
    assert meta["format"] == "url"


def test_json_field_no_schema():
    meta = JSONField().field_meta()
    assert "schema" not in meta


def test_json_field_with_schema():
    meta = JSONField(schema={"type": "object"}).field_meta()
    assert meta["schema"] == {"type": "object"}


# ---------------------------------------------------------------------------
# DoodleField
# ---------------------------------------------------------------------------


def test_doodle_field_meta_has_field_type_doodles():
    meta = DoodleField().field_meta()
    assert meta["field_type"] == "doodles"


def test_doodle_field_meta_passes_through_base_attrs():
    meta = DoodleField(label="Doodles", help_text="Draw something").field_meta()
    assert meta["label"] == "Doodles"
    assert meta["help_text"] == "Draw something"


def test_doodle_field_optional_defaults_to_none():
    py_type, kwargs = DoodleField().to_pydantic(annotation=list, optional=True)
    assert py_type == (list[dict] | None)
    assert kwargs == {"default": None}


def test_doodle_field_required_defaults_to_empty_list():
    py_type, kwargs = DoodleField(required=True).to_pydantic(annotation=list, optional=False)
    assert py_type == list[dict]
    assert kwargs == {"default_factory": list}
