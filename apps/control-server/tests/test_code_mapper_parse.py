"""_parse_mapping_response の識別子照合（IK-0027 相当: 厳密な識別子検証で応答全体が捨てられる）。

モデルは feature_id を名前から自作し、symbol 名に一覧の「(kind)」を写す。解決できるものは解決し、
解決できない link はその 1 件だけ落とす。全部落ちたときだけ失敗にする。
"""

import json

import pytest

from app.code_mapper import MappingValidationError, _parse_mapping_response


FEATURE_IDS = {"select-equation-of-state", "mass-radius-sequence"}
ALIASES = {"use-a-polytropic-or-tabulated-eos": "select-equation-of-state",
           "generate-a-tov-mass-radius-sequence": "mass-radius-sequence"}
SYMBOLS = {("tov/eos.py", "Polytrope"), ("tov/sequence.py", "mass_radius_sequence")}


def _link(fid, name, path, reason="implements it", confidence=0.9):
    return {"feature_id": fid, "symbol_qualified_name": name, "symbol_path": path,
            "relation_reason": reason, "confidence": confidence}


def _parse(links):
    return _parse_mapping_response(json.dumps({"links": links}), FEATURE_IDS, SYMBOLS, ALIASES)


def test_exact_ids_pass_through():
    out = _parse([_link("select-equation-of-state", "Polytrope", "tov/eos.py")])
    assert [(l.feature_id, l.symbol_qualified_name) for l in out] == [("select-equation-of-state", "Polytrope")]


def test_feature_name_slug_is_resolved_to_the_id():
    out = _parse([_link("use-a-polytropic-or-tabulated-eos", "Polytrope", "tov/eos.py")])
    assert out[0].feature_id == "select-equation-of-state"


def test_kind_suffix_on_symbol_name_is_stripped():
    out = _parse([_link("mass-radius-sequence", "mass_radius_sequence (function)", "tov/sequence.py")])
    assert out[0].symbol_qualified_name == "mass_radius_sequence"


def test_unresolvable_link_is_dropped_but_others_kept():
    out = _parse([
        _link("totally-made-up", "Polytrope", "tov/eos.py"),
        _link("mass-radius-sequence", "not_a_symbol", "tov/sequence.py"),
        _link("mass-radius-sequence", "mass_radius_sequence", "tov/sequence.py"),
    ])
    assert [(l.feature_id, l.symbol_qualified_name) for l in out] == [("mass-radius-sequence", "mass_radius_sequence")]


def test_all_links_unresolvable_is_an_error_naming_both_kinds():
    with pytest.raises(MappingValidationError, match=r"unknown feature_id: totally-made-up; unknown symbol: tov/sequence.py:not_a_symbol"):
        _parse([
            _link("totally-made-up", "Polytrope", "tov/eos.py"),
            _link("mass-radius-sequence", "not_a_symbol", "tov/sequence.py"),
        ])
