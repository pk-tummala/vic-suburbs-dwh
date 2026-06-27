"""Data-quality rule compilation.

Translates the declarative rules in ``config/dq_rules/*.yaml`` into Spark SQL boolean
expressions keyed by rule name. The DLT pipeline applies these via
``@dlt.expect_all_or_drop`` (WARN) and ``@dlt.expect_all_or_fail`` (FATAL), so the
pipeline event log records pass/fail counts under each rule's name.

This module is pure string-building so it is unit-testable without Spark.
"""

from __future__ import annotations

from typing import Any

# Rule types that evaluate per row (expressible as a boolean column expression).
ROW_LEVEL_TYPES = {
    "not_null",
    "in_set",
    "value_range",
    "regex_match",
    "cross_field",
}
# Rule types evaluated over the whole batch (handled outside expectations).
BATCH_LEVEL_TYPES = {"unique", "row_count_min"}

VALID_SEVERITIES = {"WARN", "FATAL"}


def _sql_literal(value: Any) -> str:
    if isinstance(value, str):
        # Escape backslashes first, then single quotes. Spark's default string-literal parser
        # treats backslash as an escape char, so an unescaped '\d' collapses to 'd' — which would
        # silently break a RLIKE pattern like '^3\d{3}$' and drop every row. Doubling the backslash
        # makes Spark see the intended metacharacter.
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def rule_to_expr(rule: dict[str, Any]) -> str:
    """Return a Spark SQL boolean expression that is TRUE for a *passing* row."""
    rtype = rule["type"]
    col = rule.get("column")

    if rtype == "not_null":
        return f"{col} IS NOT NULL"
    if rtype == "in_set":
        members = ", ".join(_sql_literal(v) for v in rule["values"])
        return f"({col} IS NULL OR {col} IN ({members}))"
    if rtype == "value_range":
        lo, hi = rule["min"], rule["max"]
        return f"({col} IS NULL OR {col} BETWEEN {lo} AND {hi})"
    if rtype == "regex_match":
        return f"({col} IS NULL OR {col} RLIKE {_sql_literal(rule['pattern'])})"
    if rtype == "cross_field":
        # caller supplies a full boolean expression
        return f"({rule['expr']})"

    raise ValueError(f"Rule type '{rtype}' is not row-level; cannot compile to an expectation expr")


def build_expectation_exprs(rules: list[dict[str, Any]], severity: str) -> dict[str, str]:
    """Return ``{rule_name: expr}`` for all row-level rules of the given severity."""
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"severity must be one of {VALID_SEVERITIES}, got {severity!r}")
    out: dict[str, str] = {}
    for rule in rules:
        if rule.get("severity") != severity:
            continue
        if rule["type"] in BATCH_LEVEL_TYPES:
            continue
        out[rule["name"]] = rule_to_expr(rule)
    return out


def batch_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return rules that must be checked at batch scope (unique, row_count_min)."""
    return [r for r in rules if r["type"] in BATCH_LEVEL_TYPES]


def escalation_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """WARN rules that escalate to a run failure above a violation-percentage threshold."""
    return [r for r in rules if "fail_if_violation_pct_above" in r]
