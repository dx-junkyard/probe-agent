"""_parse_evidence の行範囲検証（IK-0026）。

プロンプトは本文を行番号なしで渡すため、モデルは end_line を数行超過しやすい。超過は末尾に丸めて draft を残し、
start_line がファイル末尾を越える（何も裏付けがない）場合だけ拒否する。
"""

import pytest

from app.draft_generator import DraftValidationError, _parse_evidence


LINE_COUNTS = {"tov/units.py": 61}


def _item(start, end, path="tov/units.py"):
    return {"path": path, "start_line": start, "end_line": end, "summary": "単位の変換定数"}


def test_range_inside_file_is_kept_as_is():
    items = _parse_evidence([_item(10, 20)], LINE_COUNTS)
    assert (items[0].start_line, items[0].end_line) == (10, 20)


def test_end_line_past_file_end_is_clamped_not_rejected():
    items = _parse_evidence([_item(50, 70)], LINE_COUNTS)
    assert (items[0].start_line, items[0].end_line) == (50, 61)


def test_start_line_past_file_end_is_rejected():
    with pytest.raises(DraftValidationError, match="exceeds snapshot content"):
        _parse_evidence([_item(62, 70)], LINE_COUNTS)


def test_one_overshooting_item_does_not_discard_the_others():
    items = _parse_evidence([_item(1, 5), _item(60, 99)], LINE_COUNTS)
    assert [(i.start_line, i.end_line) for i in items] == [(1, 5), (60, 61)]


def test_unknown_path_is_still_rejected():
    with pytest.raises(DraftValidationError, match="not in snapshot"):
        _parse_evidence([_item(1, 2, path="missing.py")], LINE_COUNTS)
