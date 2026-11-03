"""_parse_evidence の行範囲検証と、プロンプトの行番号付き本文（IK-0026）。

モデルは行番号を数え間違える（超過・0 始まり・逆転）。行範囲は引用の精度の問題であって経路の捏造ではないので、
正規化できるものは正規化して draft を残し、ファイル内に裏付けがあり得ない範囲だけ拒否する。
"""

import pytest

from app.draft_generator import DraftValidationError, _build_file_context, _parse_evidence
from app.git_ops import IndexedFile


LINE_COUNTS = {"tov/units.py": 61}


def _item(start, end, path="tov/units.py"):
    return {"path": path, "start_line": start, "end_line": end, "summary": "単位の変換定数"}


def test_range_inside_file_is_kept_as_is():
    items = _parse_evidence([_item(10, 20)], LINE_COUNTS)
    assert (items[0].start_line, items[0].end_line) == (10, 20)


def test_end_line_past_file_end_is_clamped_not_rejected():
    items = _parse_evidence([_item(50, 70)], LINE_COUNTS)
    assert (items[0].start_line, items[0].end_line) == (50, 61)


def test_zero_based_start_is_normalised_to_one():
    items = _parse_evidence([_item(0, 5)], LINE_COUNTS)
    assert (items[0].start_line, items[0].end_line) == (1, 5)


def test_inverted_range_is_swapped():
    items = _parse_evidence([_item(20, 10)], LINE_COUNTS)
    assert (items[0].start_line, items[0].end_line) == (10, 20)


def test_start_line_past_file_end_is_rejected_with_values():
    with pytest.raises(DraftValidationError, match=r"exceeds snapshot content: tov/units.py \(62-70, 61 lines\)"):
        _parse_evidence([_item(62, 70)], LINE_COUNTS)


def test_one_overshooting_item_does_not_discard_the_others():
    items = _parse_evidence([_item(1, 5), _item(60, 99)], LINE_COUNTS)
    assert [(i.start_line, i.end_line) for i in items] == [(1, 5), (60, 61)]


def test_unknown_path_is_still_rejected():
    with pytest.raises(DraftValidationError, match="not in snapshot"):
        _parse_evidence([_item(1, 2, path="missing.py")], LINE_COUNTS)


def test_file_context_shows_line_numbers():
    body = b"x = 1\ny = 2\n"
    f = IndexedFile(path="a.py", source_type="source", size_bytes=len(body), content_hash="h", content=body)
    text, selected = _build_file_context([f])
    assert selected == [f]
    assert "   1| x = 1\n   2| y = 2" in text
