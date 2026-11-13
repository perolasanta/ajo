"""Helpers for assertions involving PostgreSQL-generated definitions."""

from __future__ import annotations

import re

_STRING_LITERAL = re.compile(r"'((?:''|[^'])*)'")


def check_constraint_array_values(definition: str, *, column: str) -> set[str]:
    """Return string literals from a column's ``= ANY (ARRAY[...])`` check clause."""
    expression = re.compile(
        rf"\b{re.escape(column)}\s*=\s*ANY\s*\(\s*\(?\s*ARRAY\s*\[(?P<values>.*?)\]",
        re.IGNORECASE | re.DOTALL,
    )
    match = expression.search(definition)
    if match is None:
        raise ValueError(f"No = ANY (ARRAY[...]) expression found for {column!r}")

    values = {
        literal.replace("''", "'")
        for literal in _STRING_LITERAL.findall(match.group("values"))
    }
    if not values:
        raise ValueError(f"No string values found in {column!r} constraint array")
    return values
