from datetime import UTC, datetime

import pytest

from app.ingestion.instagram.jsonutil import (
    find_list_of_dicts,
    labelled_value,
    load_json,
    parse_timestamp,
    repair_text,
)


def test_repairs_meta_mojibake():
    assert repair_text("caf\u00c3\u00a9") == "café"
    assert repair_text("\u00f0\u009f\u0098\u008a") == "😊"


def test_leaves_correct_text_alone():
    for s in ("café", "plain", "日本の本屋", "naïve ½"):
        assert repair_text(s) == s


def test_load_json_handles_bom_and_repairs():
    assert load_json(b'\xef\xbb\xbf{"t": "caf\\u00c3\\u00a9"}') == {"t": "café"}


def test_load_json_raises_on_malformed():
    with pytest.raises(ValueError):
        load_json(b'{"a": ')


@pytest.mark.parametrize(
    "value",
    [1717000000, 1717000000000, 1717000000000000, "1717000000", "2024-05-29T16:26:40Z",
     "2024-05-29T16:26:40+00:00", 1717000000.0],
)
def test_timestamp_formats(value):
    assert parse_timestamp(value) == datetime(2024, 5, 29, 16, 26, 40, tzinfo=UTC)


@pytest.mark.parametrize("value", [None, "", "yesterday", True, 0, -5, 42, {"x": 1}])
def test_invalid_timestamps(value):
    assert parse_timestamp(value) is None


def test_find_list_of_dicts_ignores_key_name():
    key, items = find_list_of_dicts({"renamed_in_2027": [{"a": 1}, {"b": 2}], "x": [1, 2, 3]})
    assert key == "renamed_in_2027" and len(items) == 2


def test_labelled_value_both_shapes():
    assert labelled_value({"string_map_data": {"Name": {"value": "A"}}}, "name") == {"value": "A"}
    lv = {"label_values": [{"label": "Name", "value": "B"}]}
    assert labelled_value(lv, "Name")["value"] == "B"
    assert labelled_value({}, "Name") is None
