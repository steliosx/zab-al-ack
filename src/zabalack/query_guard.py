"""Refuse reader queries that can inspect restricted staging (A5, T40).

Excluding the intent from response bodies closes what a reader is *handed*, not
what a reader can *ask*. Alerta's alert query builder compiles several forms that
read the stored attribute without returning it:

* `attributes.<key>:<term>` and its `_.<key>` alias compile to
  `"attributes"::jsonb ->>%(key)s ILIKE %(term)s`, and `->>` on a JSON object
  yields the object's whole serialised text, so the predicate matches a substring
  anywhere inside the intent. The phrase and sub-expression forms compile to the
  same shape.
* `_exists_:<key>` compiles to `"attributes"::jsonb ? %(key)s`.
* `?attributes.<key>=<value>` is a direct filter compiling to
  `attributes @> %(attr_<key>)s`; `attributes` is absent from the builder's
  `EXCLUDE_FROM_QUERY`, so it reaches that branch.
* `sort-by=attributes.<key>` orders by the extracted value.
* `group-by=attributes` makes the aggregate endpoints select and return the whole
  attributes object as a plain dictionary, bypassing serialisation entirely.
* `q.df=attributes` turns a bare term into a whole-column `ILIKE`.

The guard installs at the shared alert query-builder boundary, before SQL is
generated, so the list, history, count and aggregate endpoints are covered by one
control rather than each view separately. It validates *resolved* fields — the
query string is parsed and the produced SQL and bound parameters are inspected —
because a substring blacklist over the raw text cannot see through aliases,
nesting or the default field.

Ordinary keyed attribute searches, sorting and grouping are preserved. Only the
restricted key and whole-`attributes` operations are refused, with a stable
`400` that does not depend on whether any alert or intent exists, so the refusal
itself is not an oracle. It applies to every API reader including administrators,
and remains after retirement ships: it is what protects the interval in which a
staged intent is committed but the acknowledgement has not finished.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from .crypto import INTENT_ATTRIBUTE

RESTRICTED_ATTRIBUTES = frozenset({INTENT_ATTRIBUTE})
ATTRIBUTE_COLUMN = "attributes"
# A stable, content-free refusal. It must not reveal whether the selector matched
# anything, or the refusal becomes the oracle it is meant to remove.
REFUSAL = "Query selector is not permitted on restricted attributes"

# `"attributes"::jsonb ->>%(p)s` and `"attributes"::jsonb ? %(p)s`, the two
# keyed forms the parser emits for attribute access.
_KEYED = re.compile(r'"attributes"::jsonb\s*(?:->>|\?)\s*%\((\w+)\)s')
_ATTRIBUTE_MENTION = re.compile(r'"attributes"')


def _restricted(name: Any) -> bool:
    return isinstance(name, str) and name in RESTRICTED_ATTRIBUTES


def _dotted(field: str) -> tuple[str, str | None]:
    """Split `attributes.key` (or `attributes.key!`) into column and key."""
    bare = field.replace("!", "")
    head, _, tail = bare.partition(".")
    return head, tail or None


def selector_refusal(params: Any, parse: Callable[..., tuple[str, dict[str, Any]]] | None = None) -> str | None:
    """Return the refusal reason if these request parameters reach restricted staging."""
    for field in _keys(params):
        column, key = _dotted(field)
        if column == ATTRIBUTE_COLUMN and _restricted(key):
            return REFUSAL

    for sort_by in _listed(params, "sort-by"):
        column, key = _dotted(sort_by.lstrip("-"))
        if column == ATTRIBUTE_COLUMN and (key is None or _restricted(key)):
            return REFUSAL

    for group_by in _listed(params, "group-by"):
        column, key = _dotted(group_by)
        # The aggregate endpoints select the grouping expression itself, so a
        # whole-column grouping returns every attribute including this one.
        if column == ATTRIBUTE_COLUMN:
            return REFUSAL

    query = params.get("q", None)
    if query and parse is not None:
        try:
            sql, variables = parse(query=query, default_field=params.get("q.df"))
        except Exception:
            # Malformed queries are the builder's own 400 to raise, not ours.
            return None
        return _sql_refusal(sql, variables)
    return None


def _sql_refusal(sql: str, variables: dict[str, Any]) -> str | None:
    """Inspect generated SQL and its bound parameters for restricted access."""
    remainder = sql
    for parameter in _KEYED.findall(sql):
        if _restricted(variables.get(parameter)):
            return REFUSAL
    remainder = _KEYED.sub("", remainder)
    # Anything still naming the column operates on the whole object — a
    # `q.df=attributes` default field, or `attributes` used as a plain field —
    # and therefore reads the restricted value along with everything else.
    if _ATTRIBUTE_MENTION.search(remainder):
        return REFUSAL
    return None


def _keys(params: Any) -> Iterable[str]:
    try:
        return list(params.keys())
    except Exception:
        return []


def _listed(params: Any, name: str) -> Iterable[str]:
    getlist = getattr(params, "getlist", None)
    if getlist is not None:
        try:
            return [value for value in getlist(name) if isinstance(value, str)]
        except Exception:
            return []
    value = params.get(name)
    return [value] if isinstance(value, str) else []


def install(builder: Any, parser_factory: Callable[[], Any], api_error: Callable[..., Exception]) -> None:
    """Wrap one alert query builder's `from_params` with the guard."""
    declared = builder.__dict__.get("from_params")
    original = declared.__func__ if isinstance(declared, staticmethod) else builder.from_params
    if getattr(original, "_zabalack_guarded", False):
        return

    def from_params(params: Any, customers: Any = None, query_time: Any = None) -> Any:
        reason = selector_refusal(params, parse=parser_factory().parse)
        if reason:
            raise api_error(reason, 400)
        return original(params, customers=customers, query_time=query_time)

    from_params._zabalack_guarded = True  # type: ignore[attr-defined]
    builder.from_params = staticmethod(from_params)
