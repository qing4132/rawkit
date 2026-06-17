"""--where filter language: parse a predicate string into a callable.

Grammar v1 (per README):

    field   ::= iso | fnumber | shutter | focal | lens | model | maker
              | date | time
    cmp     ::= '>' | '<' | '>=' | '<=' | '==' | '!='
    string  ::= '"' ... '"'      (case-insensitive substring match with ~)

    expr    ::= 'not' expr
              | expr 'or'  expr
              | expr 'and' expr
              | '(' expr ')'
              | field cmp value
              | field '~' string         (substring match)

Operator precedence (highest first): grouping > 'not' > 'and' > 'or'.

This module is intentionally PURE — it never reads files, never imports the
CLI. That makes it independently testable and lets the same predicate engine
be reused by future commands (rawkit thumb --where, etc.) without circular
imports.
"""

from __future__ import annotations

from typing import Any, Callable

from lark import Lark, Transformer, Token, UnexpectedInput, v_args
from lark.exceptions import VisitError


# --- grammar ----------------------------------------------------------------

_GRAMMAR = r"""
?start: or_expr

?or_expr:  and_expr ("or"  and_expr)*  -> or_
?and_expr: not_expr ("and" not_expr)*  -> and_
?not_expr: "not" not_expr              -> not_
         | atom

?atom: "(" or_expr ")"
     | comparison
     | match

comparison: FIELD CMP value
match:      FIELD "~" STRING

FIELD: "iso"|"fnumber"|"shutter"|"focal"|"bias"|"rating"
     | "gps_lat"|"gps_lon"
     | "lens"|"model"|"maker"|"orientation"
     | "datetime"|"date"|"time"
     | "gps"|"flash"
CMP:   ">=" | "<=" | "==" | "!=" | ">" | "<"

?value: DATETIME -> datetime_literal
      | NUMBER   -> number
      | STRING   -> string
      | DATE     -> date_literal
      | TIME     -> time_literal
      | BOOL     -> bool_literal

NUMBER:   /-?\d+(\.\d+)?/
STRING:   /"[^"]*"/
DATETIME: /\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?/
DATE:     /\d{4}-\d{2}-\d{2}/
TIME:     /\d{2}:\d{2}(:\d{2}(\.\d+)?)?/
BOOL:     "true" | "false"

%import common.WS
%ignore WS
"""


_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "iso", "fnumber", "shutter", "focal",
    "bias", "rating", "gps_lat", "gps_lon",
})
_STRING_FIELDS:  frozenset[str] = frozenset({"lens", "model", "maker", "orientation"})
_DATETIME_FIELDS: frozenset[str] = frozenset({"datetime"})
_DATE_FIELDS:    frozenset[str] = frozenset({"date"})
_TIME_FIELDS:    frozenset[str] = frozenset({"time"})
_BOOL_FIELDS:    frozenset[str] = frozenset({"gps", "flash"})


# --- evaluation tree --------------------------------------------------------
# We build small lambdas during transformation rather than a class hierarchy;
# fewer types to reason about, and the result is directly callable.

Predicate = Callable[[dict[str, Any]], bool]


def _coerce_numeric(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _record_field(record: dict[str, Any], field: str) -> Any:
    """Read a logical field from a record. All derived fields are already
    materialized in exif._normalize(), so this is a straight lookup."""
    return record.get(field)


def _cmp_op(op: str) -> Callable[[Any, Any], bool]:
    return {
        ">":  lambda a, b: a is not None and a >  b,
        "<":  lambda a, b: a is not None and a <  b,
        ">=": lambda a, b: a is not None and a >= b,
        "<=": lambda a, b: a is not None and a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }[op]


@v_args(inline=True)
class _Builder(Transformer):
    """Lark Transformer that turns the parse tree into a callable predicate."""

    # value leaves
    def number(self, tok: Token) -> tuple[str, float]:
        return ("num", float(tok))

    def string(self, tok: Token) -> tuple[str, str]:
        return ("str", str(tok)[1:-1])  # strip surrounding quotes

    def date_literal(self, tok: Token) -> tuple[str, str]:
        return ("date", str(tok))  # 'YYYY-MM-DD'

    def time_literal(self, tok: Token) -> tuple[str, str]:
        return ("time", str(tok))  # 'HH:MM' or 'HH:MM:SS'

    def datetime_literal(self, tok: Token) -> tuple[str, str]:
        # normalize 'T' separator to space so lexicographic compare matches
        # the record's stored format 'YYYY-MM-DD HH:MM:SS'
        return ("datetime", str(tok).replace("T", " "))

    def bool_literal(self, tok: Token) -> tuple[str, bool]:
        return ("bool", str(tok) == "true")

    # comparison: FIELD CMP value
    def comparison(self, field: Token, cmp: Token, value) -> Predicate:
        field_name = str(field)
        op = str(cmp)
        op_fn = _cmp_op(op)
        kind, literal = value

        # Validate field/value compatibility — fail at parse time, not at scan
        # time, so the user gets the error immediately.
        if field_name in _NUMERIC_FIELDS and kind != "num":
            raise QueryError(
                f"field `{field_name}` is numeric; got a {kind} value"
            )
        if field_name in _STRING_FIELDS and kind == "num":
            raise QueryError(
                f"field `{field_name}` is a string; use `=='...'` or `~'...'` instead"
            )
        if field_name in _DATE_FIELDS and kind not in ("date", "str"):
            raise QueryError(
                f"field `date` expects YYYY-MM-DD (got a {kind})"
            )
        if field_name in _TIME_FIELDS and kind not in ("time", "str"):
            raise QueryError(
                f"field `time` expects HH:MM or HH:MM:SS (got a {kind})"
            )
        if field_name in _DATETIME_FIELDS and kind not in ("datetime", "date", "str"):
            raise QueryError(
                f"field `datetime` expects YYYY-MM-DD[ HH:MM[:SS]] (got a {kind})"
            )
        if field_name in _BOOL_FIELDS:
            if kind != "bool":
                raise QueryError(
                    f"field `{field_name}` is boolean; use `==true` or `==false`"
                )
            if op not in ("==", "!="):
                raise QueryError(
                    f"boolean field `{field_name}` only supports `==` and `!=`"
                )

        if kind == "num":
            def pred(rec: dict[str, Any]) -> bool:
                lhs = _coerce_numeric(_record_field(rec, field_name))
                return False if lhs is None else op_fn(lhs, literal)
            return pred

        if kind == "bool":
            def pred_bool(rec: dict[str, Any]) -> bool:
                lhs = _record_field(rec, field_name)
                # Treat missing-bool as False so `flash==false` includes shots
                # whose camera didn't write a Flash tag at all (common on
                # mirrorless when flash is off).
                lhs_b = bool(lhs) if lhs is not None else False
                return op_fn(lhs_b, literal)
            return pred_bool

        # string / date / time → string-compare. Both sides lowercased for ==/!= on strings.
        def pred_str(rec: dict[str, Any]) -> bool:
            lhs = _record_field(rec, field_name)
            if lhs is None:
                return op == "!="  # missing != anything is True
            return op_fn(str(lhs), literal)
        return pred_str

    # match: FIELD ~ STRING  (case-insensitive substring)
    # STRING is a Token here (not the ('str', ...) tuple) because the rule
    # references the STRING terminal directly, bypassing `?value`/`string`.
    def match(self, field: Token, string_token: Token) -> Predicate:
        field_name = str(field)
        if field_name not in (
            _STRING_FIELDS | _DATE_FIELDS | _TIME_FIELDS | _DATETIME_FIELDS
        ):
            raise QueryError(
                f"`~` (substring match) only applies to string/date/time fields; "
                f"got `{field_name}`"
            )
        needle = str(string_token)[1:-1].lower()  # strip surrounding quotes

        def pred(rec: dict[str, Any]) -> bool:
            lhs = _record_field(rec, field_name)
            return isinstance(lhs, str) and needle in lhs.lower()
        return pred

    # logical combinators — variadic because grammar uses (op X)*
    def and_(self, *preds: Predicate) -> Predicate:
        if len(preds) == 1:
            return preds[0]
        return lambda rec: all(p(rec) for p in preds)

    def or_(self, *preds: Predicate) -> Predicate:
        if len(preds) == 1:
            return preds[0]
        return lambda rec: any(p(rec) for p in preds)

    def not_(self, pred: Predicate) -> Predicate:
        return lambda rec: not pred(rec)


class QueryError(ValueError):
    """Raised when a --where expression is syntactically or semantically bad."""


_parser = Lark(_GRAMMAR, parser="lalr")


def compile_where(expr: str) -> Predicate:
    """Compile a --where expression into a callable predicate.

    Raises QueryError with a human-readable message on syntax or type
    errors. The returned callable is pure: `predicate(record) -> bool`.
    """
    expr = expr.strip()
    if not expr:
        raise QueryError("empty --where expression")
    try:
        tree = _parser.parse(expr)
    except UnexpectedInput as e:
        # Lark's get_context() shows the failing character pointed by ^.
        context = e.get_context(expr).rstrip()
        raise QueryError(
            f"can't parse --where at line {e.line}, column {e.column}:\n{context}"
        ) from None
    try:
        return _Builder().transform(tree)
    except VisitError as e:
        # Lark wraps any exception raised inside a transformer in VisitError;
        # unwrap our own QueryError so callers get the clean message.
        if isinstance(e.orig_exc, QueryError):
            raise e.orig_exc from None
        raise
