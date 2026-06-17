"""Tests for the rawkit.query --where expression engine."""

from __future__ import annotations

import pytest

from rawkit.query import QueryError, compile_where


# --- canonical records used across many tests ------------------------------

R_SONY = {
    "path": "/x/a.ARW",
    "date": "2024-03-15 12:34:56",
    "maker": "SONY",
    "model": "ILCE-7M4",
    "lens": "FE 50mm F1.4 GM",
    "iso": 800,
    "fnumber": 1.4,
    "shutter": 0.004,
    "focal": 50,
}

R_CANON = {
    "path": "/x/b.CR3",
    "date": "2022-05-13 16:38:09",
    "maker": "Canon",
    "model": "Canon EOS R5",
    "lens": "RF800mm F11 IS STM",
    "iso": 400,
    "fnumber": 11.0,
    "shutter": 0.00625,
    "focal": 800,
}

R_RICOH_NO_LENS = {  # fixed-lens compact, LensModel absent
    "path": "/x/c.DNG",
    "date": "2022-11-07 23:15:15",
    "maker": "RICOH",
    "model": "RICOH GR III",
    "iso": 500,
    "fnumber": 2.8,
    "shutter": 1 / 30,
    "focal": 18.3,
}

ALL = [R_SONY, R_CANON, R_RICOH_NO_LENS]


def _filter(expr: str, records=ALL):
    pred = compile_where(expr)
    return [r for r in records if pred(r)]


# --- numeric comparison -----------------------------------------------------

def test_simple_numeric_gt() -> None:
    assert _filter("iso>500") == [R_SONY]


def test_numeric_lte() -> None:
    assert _filter("iso<=500") == [R_CANON, R_RICOH_NO_LENS]


def test_numeric_eq_float() -> None:
    assert _filter("fnumber==1.4") == [R_SONY]


def test_numeric_neq() -> None:
    assert _filter("focal!=50") == [R_CANON, R_RICOH_NO_LENS]


# --- substring match (~) ----------------------------------------------------

def test_substring_match_case_insensitive() -> None:
    # "50" appears in Sony's "FE 50mm F1.4 GM"
    assert _filter('lens~"50"') == [R_SONY]


def test_substring_match_excludes_fixed_lens() -> None:
    # RICOH GR III has no LensModel at all → ~ must return False, not crash
    assert _filter('lens~"rf"') == [R_CANON]


def test_substring_match_on_model() -> None:
    assert _filter('model~"r5"') == [R_CANON]


def test_substring_match_on_maker() -> None:
    assert _filter('maker~"sony"') == [R_SONY]


# --- date / time ------------------------------------------------------------

def test_date_gte() -> None:
    assert _filter('date>="2023-01-01"') == [R_SONY]


def test_date_range() -> None:
    assert _filter('date>="2022-01-01" and date<"2023-01-01"') == [R_CANON, R_RICOH_NO_LENS]


def test_time_field_derived_from_date() -> None:
    # R_SONY @ 12:34, R_CANON @ 16:38, R_RICOH @ 23:15
    assert _filter('time>="20:00"') == [R_RICOH_NO_LENS]


# --- logical combinators + precedence ---------------------------------------

def test_and_combines() -> None:
    assert _filter('iso>200 and lens~"rf"') == [R_CANON]


def test_or_combines() -> None:
    assert _filter('iso==800 or iso==400') == [R_SONY, R_CANON]


def test_not_negates() -> None:
    assert _filter('not maker~"canon"') == [R_SONY, R_RICOH_NO_LENS]


def test_precedence_and_binds_tighter_than_or() -> None:
    # "iso==800 or iso==400 and fnumber==11"
    #   parses as: iso==800 OR (iso==400 AND fnumber==11)
    # Sony matches the OR-left; Canon matches the AND on the right.
    assert _filter('iso==800 or iso==400 and fnumber==11') == [R_SONY, R_CANON]


def test_parentheses_override_precedence() -> None:
    # "(iso==800 or iso==400) and fnumber==11"
    #   only Canon matches both clauses
    assert _filter('(iso==800 or iso==400) and fnumber==11') == [R_CANON]


def test_double_negation() -> None:
    assert _filter('not not iso==800') == [R_SONY]


# --- missing-field handling -------------------------------------------------

def test_missing_field_numeric_comparison_is_false() -> None:
    rec_no_iso = {**R_SONY}
    del rec_no_iso["iso"]
    assert _filter("iso>0", [rec_no_iso]) == []


def test_missing_field_substring_match_is_false() -> None:
    # RICOH GR III has no LensModel — substring match on lens returns False
    assert _filter('lens~"anything"', [R_RICOH_NO_LENS]) == []


# --- error reporting --------------------------------------------------------

def test_empty_expression_is_error() -> None:
    with pytest.raises(QueryError, match="empty"):
        compile_where("")


def test_syntax_error_includes_position() -> None:
    with pytest.raises(QueryError) as ei:
        compile_where("iso > and 5")
    msg = str(ei.value).lower()
    assert "line" in msg and "column" in msg


def test_unknown_field_is_rejected() -> None:
    # `foo` is not in the FIELD terminal; lark sees it as a syntax error.
    with pytest.raises(QueryError):
        compile_where("foo > 1")


def test_type_mismatch_numeric_field_with_string() -> None:
    with pytest.raises(QueryError, match="numeric"):
        compile_where('iso == "high"')


def test_type_mismatch_string_field_with_number() -> None:
    with pytest.raises(QueryError, match="string"):
        compile_where('lens == 50')


def test_match_op_rejected_on_numeric_field() -> None:
    with pytest.raises(QueryError, match="substring"):
        compile_where('iso ~ "800"')
