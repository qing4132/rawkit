"""Tests for the rawkit.query --where expression engine."""

from __future__ import annotations

import pytest

from rawkit.query import QueryError, compile_where


# --- canonical records used across many tests ------------------------------

R_SONY = {
    "path": "/x/a.ARW",
    "datetime": "2024-03-15 12:34:56",
    "date":     "2024-03-15",
    "time":     "12:34:56",
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
    "datetime": "2022-05-13 16:38:09",
    "date":     "2022-05-13",
    "time":     "16:38:09",
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
    "datetime": "2022-11-07 23:15:15",
    "date":     "2022-11-07",
    "time":     "23:15:15",
    "maker": "RICOH",
    "model": "RICOH GR III",
    "iso": 500,
    "fnumber": 2.8,
    "shutter": 1 / 30,
    "focal": 18.3,
}

# Synthetic records to exercise the new fields (orientation/gps/bias/flash/rating).
R_PORTRAIT = {**R_SONY, "path": "/x/portrait.ARW", "orientation": "portrait"}
R_LANDSCAPE = {**R_SONY, "path": "/x/landscape.ARW", "orientation": "landscape"}
R_GPS_BJ = {
    **R_SONY, "path": "/x/bj.ARW",
    "gps": True, "gps_lat": 39.9, "gps_lon": 116.4,
}
R_NO_GPS = {**R_SONY, "path": "/x/nogps.ARW"}
R_FLASH = {**R_SONY, "path": "/x/flash.ARW", "flash": True}
R_NO_FLASH = {**R_SONY, "path": "/x/noflash.ARW", "flash": False}
R_PUSHED = {**R_SONY, "path": "/x/pushed.ARW", "bias": 1.5}
R_PULLED = {**R_SONY, "path": "/x/pulled.ARW", "bias": -2.0}
R_RATED4 = {**R_SONY, "path": "/x/r4.ARW", "rating": 4}
R_RATED1 = {**R_SONY, "path": "/x/r1.ARW", "rating": 1}

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


# --- aperture: photographer-direction reversed fnumber ---------------------

def test_aperture_gte_selects_large_apertures() -> None:
    """'aperture>=2.8' = wider than or equal to f/2.8 (large aperture).
    Sony has f/1.4 (wider) → matches. Ricoh has f/2.8 → matches. Canon has
    f/11 (narrow) → excluded. The DSL rewrites this internally to
    fnumber<=2.8 so the photographer never has to think about it."""
    assert _filter("aperture>=2.8") == [R_SONY, R_RICOH_NO_LENS]


def test_aperture_lte_selects_small_apertures() -> None:
    """'aperture<=2.8' = narrower than or equal to f/2.8 (small aperture).
    Ricoh has f/2.8 (boundary) → matches. Canon has f/11 → matches. Sony has
    f/1.4 (too wide) → excluded."""
    assert _filter("aperture<=2.8") == [R_CANON, R_RICOH_NO_LENS]


def test_aperture_strictly_wider() -> None:
    """'aperture>2.8' = strictly wider than f/2.8 → fnumber<2.8."""
    assert _filter("aperture>2.8") == [R_SONY]


def test_aperture_eq() -> None:
    """'aperture==2.8' should match f/2.8 (Ricoh). Eq isn't flipped because
    == is symmetric — the rewrite just goes through unchanged."""
    assert _filter("aperture==2.8") == [R_RICOH_NO_LENS]


def test_aperture_and_fnumber_are_complements() -> None:
    """For any comparison, aperture and fnumber operators are mirror images:
    'aperture>=N' selects exactly the same rows as 'fnumber<=N'."""
    for expr_a, expr_f in [
        ("aperture>=2.8", "fnumber<=2.8"),
        ("aperture<3.5",  "fnumber>3.5"),
        ("aperture==4.0", "fnumber==4.0"),
        ("aperture!=11",  "fnumber!=11"),
    ]:
        assert _filter(expr_a) == _filter(expr_f), (expr_a, expr_f)


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


def test_time_field() -> None:
    # R_SONY @ 12:34:56, R_CANON @ 16:38:09, R_RICOH @ 23:15:15
    assert _filter('time>="20:00"') == [R_RICOH_NO_LENS]


def test_time_with_seconds_literal() -> None:
    assert _filter('time>="23:15:15"') == [R_RICOH_NO_LENS]
    assert _filter('time>="23:15:16"') == []


def test_datetime_field() -> None:
    # full timestamp compare — same calendar day but different seconds
    rec_a = {**R_SONY, "datetime": "2024-03-15 12:00:00"}
    rec_b = {**R_SONY, "datetime": "2024-03-15 14:00:00"}
    assert _filter('datetime>="2024-03-15 13:00:00"', [rec_a, rec_b]) == [rec_b]


def test_datetime_accepts_date_only_literal() -> None:
    # 'YYYY-MM-DD' literal against datetime field compares prefix correctly
    assert _filter('datetime>="2024-01-01"', [R_SONY, R_CANON]) == [R_SONY]


def test_subsecond_in_time_literal() -> None:
    """TIME literal accepts '.NNN' suffix; records with subsec compare correctly."""
    rec_early = {**R_SONY, "time": "12:34:56.100"}
    rec_late  = {**R_SONY, "time": "12:34:56.900"}
    # Filter > 0.5 sec into that second.
    assert _filter('time>="12:34:56.500"', [rec_early, rec_late]) == [rec_late]


def test_subsecond_in_datetime_literal() -> None:
    rec_a = {**R_SONY, "datetime": "2024-10-27 17:09:43.100"}
    rec_b = {**R_SONY, "datetime": "2024-10-27 17:09:43.900"}
    assert _filter('datetime>"2024-10-27 17:09:43.500"', [rec_a, rec_b]) == [rec_b]


# --- mixed-precision compare (SQL start-of-unit semantics) -----------------

def test_short_time_literal_eq_matches_unpadded_record() -> None:
    """`time=="16:00"` must match record `time="16:00:00"` (start of minute)."""
    rec = {**R_SONY, "time": "16:00:00"}
    assert _filter('time=="16:00"', [rec]) == [rec]


def test_short_time_literal_eq_does_not_match_into_minute() -> None:
    """But `time=="16:00"` does NOT match `time="16:00:05"` — start-of-minute
    means exactly 16:00:00, not 'any time in the minute'. Use a range for
    that intent (or use a coarser field if one exists)."""
    rec = {**R_SONY, "time": "16:00:05"}
    assert _filter('time=="16:00"', [rec]) == []


def test_short_time_literal_lte_matches_at_boundary() -> None:
    """`time<="16:00"` should include `time="16:00:00"` exactly."""
    at  = {**R_SONY, "time": "16:00:00", "path": "/x/at.ARW"}
    after = {**R_SONY, "time": "16:00:01", "path": "/x/after.ARW"}
    before = {**R_SONY, "time": "15:59:59", "path": "/x/before.ARW"}
    assert _filter('time<="16:00"', [at, after, before]) == [at, before]


def test_short_time_literal_lt_excludes_boundary() -> None:
    """`time<"16:00"` excludes `time="16:00:00"` (strict before)."""
    at = {**R_SONY, "time": "16:00:00"}
    just_before = {**R_SONY, "time": "15:59:59.999"}
    assert _filter('time<"16:00"', [at, just_before]) == [just_before]


def test_short_time_literal_gte_includes_boundary_and_subsec() -> None:
    rec_at  = {**R_SONY, "time": "16:00:00"}
    rec_sub = {**R_SONY, "time": "16:00:00.048"}
    rec_b   = {**R_SONY, "time": "15:59:59"}
    assert _filter('time>="16:00"', [rec_at, rec_sub, rec_b]) == [rec_at, rec_sub]


def test_short_datetime_literal_eq_pads_to_midnight() -> None:
    """`datetime=="2024-01-02"` matches only the exact midnight that day,
    matching SQL's implicit-cast semantics."""
    midnight = {**R_SONY, "datetime": "2024-01-02 00:00:00"}
    noon     = {**R_SONY, "datetime": "2024-01-02 12:00:00"}
    assert _filter('datetime=="2024-01-02"', [midnight, noon]) == [midnight]


def test_whole_day_via_range_idiom() -> None:
    """SQL idiom for 'any time on 2024-01-02': use a half-open range."""
    same_day_morn = {**R_SONY, "datetime": "2024-01-02 06:00:00", "path": "/x/m"}
    same_day_eve  = {**R_SONY, "datetime": "2024-01-02 22:00:00", "path": "/x/e"}
    next_day      = {**R_SONY, "datetime": "2024-01-03 00:00:00", "path": "/x/n"}
    out = _filter(
        'datetime>="2024-01-02" and datetime<"2024-01-03"',
        [same_day_morn, same_day_eve, next_day],
    )
    assert out == [same_day_morn, same_day_eve]


def test_whole_day_via_date_field_idiom() -> None:
    """The simpler way (only because we have a separate `date` field):
    just compare on `date` directly."""
    same_day = {**R_SONY, "date": "2024-01-02"}
    next_day = {**R_SONY, "date": "2024-01-03"}
    assert _filter('date=="2024-01-02"', [same_day, next_day]) == [same_day]


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


# --- new fields: orientation -----------------------------------------------

def test_orientation_filter() -> None:
    pred = compile_where('orientation == "portrait"')
    assert pred(R_PORTRAIT) is True
    assert pred(R_LANDSCAPE) is False
    # records without orientation key
    assert pred(R_SONY) is False


# --- new fields: gps (boolean) + gps_lat/gps_lon (numeric box) -------------

def test_gps_boolean() -> None:
    pred = compile_where('gps == true')
    assert pred(R_GPS_BJ) is True
    assert pred(R_NO_GPS) is False


def test_gps_negation() -> None:
    pred = compile_where('gps != true')
    assert pred(R_NO_GPS) is True
    assert pred(R_GPS_BJ) is False


def test_gps_bounding_box_beijing() -> None:
    # Rough Beijing box: 39°<lat<41°, 115°<lon<117°.
    pred = compile_where(
        'gps_lat>39 and gps_lat<41 and gps_lon>115 and gps_lon<117'
    )
    assert pred(R_GPS_BJ) is True
    far = {**R_GPS_BJ, "gps_lat": 31.2, "gps_lon": 121.5}  # Shanghai
    assert pred(far) is False
    # Records lacking GPS at all must evaluate False (don't crash).
    assert pred(R_NO_GPS) is False


def test_bool_field_rejects_inequality_operators() -> None:
    for op in ("<", "<=", ">", ">="):
        with pytest.raises(QueryError, match="only supports `==` and `!=`"):
            compile_where(f"gps {op} true")


def test_bool_field_rejects_non_bool_literal() -> None:
    with pytest.raises(QueryError, match="boolean"):
        compile_where('gps == 1')


# --- new fields: flash ------------------------------------------------------

def test_flash_filter() -> None:
    fired = compile_where('flash == true')
    assert fired(R_FLASH) is True
    assert fired(R_NO_FLASH) is False
    # Missing flash key treated as 'did not fire' (most mirrorless never wrote
    # the tag with the default "off" setting).
    assert fired(R_SONY) is False


# --- new fields: bias / rating ---------------------------------------------

def test_bias_pushed_shots() -> None:
    pred = compile_where('bias>=1')
    assert pred(R_PUSHED) is True
    assert pred(R_PULLED) is False
    assert pred(R_SONY) is False  # absent bias → numeric coerce fails → False


def test_rating_threshold() -> None:
    pred = compile_where('rating>=3')
    assert pred(R_RATED4) is True
    assert pred(R_RATED1) is False


# --- combined real-world queries -------------------------------------------

def test_keepers_query() -> None:
    """Typical cull predicate: 'high-rated, horizontal, no flash'."""
    pred = compile_where(
        'rating>=3 and orientation=="landscape" and flash==false'
    )
    keeper = {**R_RATED4, "orientation": "landscape", "flash": False}
    drop = {**R_RATED4, "orientation": "portrait", "flash": False}
    assert pred(keeper) is True
    assert pred(drop) is False
