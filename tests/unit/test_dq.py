import pytest

from vic_suburbs.common import dq


def test_not_null_expr():
    assert dq.rule_to_expr({"type": "not_null", "column": "sal_code"}) == "sal_code IS NOT NULL"


def test_value_range_allows_null():
    expr = dq.rule_to_expr({"type": "value_range", "column": "x", "min": 0, "max": 9})
    assert "BETWEEN 0 AND 9" in expr and "IS NULL OR" in expr


def test_regex_quotes_pattern():
    expr = dq.rule_to_expr({"type": "regex_match", "column": "postcode", "pattern": "^3\\d{3}$"})
    assert "RLIKE '^3\\d{3}$'" in expr


def test_in_set_quotes_strings():
    expr = dq.rule_to_expr({"type": "in_set", "column": "s", "values": ["A", "B"]})
    assert "IN ('A', 'B')" in expr


def test_cross_field_wraps_expr():
    expr = dq.rule_to_expr({"type": "cross_field", "expr": "a = b"})
    assert expr == "(a = b)"


def test_build_splits_by_severity():
    rules = [
        {"name": "r1", "type": "not_null", "column": "a", "severity": "FATAL"},
        {
            "name": "r2",
            "type": "value_range",
            "column": "b",
            "min": 0,
            "max": 1,
            "severity": "WARN",
        },
        {
            "name": "r3",
            "type": "row_count_min",
            "min": 1,
            "severity": "FATAL",
        },  # batch-level, skipped
    ]
    fatal = dq.build_expectation_exprs(rules, "FATAL")
    warn = dq.build_expectation_exprs(rules, "WARN")
    assert set(fatal) == {"r1"}  # r3 is batch-level, excluded
    assert set(warn) == {"r2"}


def test_invalid_severity_raises():
    with pytest.raises(ValueError):
        dq.build_expectation_exprs([], "MAYBE")


def test_escalation_rules_detected():
    rules = [
        {
            "name": "x",
            "type": "crosswalk_resolved",
            "column": "sal_code",
            "severity": "WARN",
            "fail_if_violation_pct_above": 20,
        }
    ]
    assert dq.escalation_rules(rules)[0]["name"] == "x"


def test_real_property_rules_compile(config_dir):
    from vic_suburbs.common import config

    rules = config.load_dq_rules("property", config_dir)
    warn = dq.build_expectation_exprs(rules, "WARN")
    fatal = dq.build_expectation_exprs(rules, "FATAL")
    assert "sal_code_not_null" in fatal
    assert "median_price_sane" in warn
