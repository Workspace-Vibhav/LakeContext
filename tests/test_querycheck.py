import copy
from pathlib import Path

import pytest

from lakecontext.querycheck import (
    UnsupportedSQL,
    checked_tree,
    compare,
    run_query,
    sql_hash,
    suite_v1,
)
from lakecontext.revenue import RevenueModel, RevenueRequest, queries, synthetic_fixture

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
BASELINE = (EXAMPLES / "revenue-approved.sql").read_text()
CANDIDATE = (EXAMPLES / "revenue-proposed.sql").read_text()


@pytest.mark.parametrize("strategy", ["item_value", "equal_categories", "full_credit"])
@pytest.mark.parametrize("group_by", [["category"], ["category", "region"]])
def test_generated_plans_match_confirmed_meaning_across_suite(strategy, group_by):
    model = RevenueModel()
    request = RevenueRequest(group_by=group_by, start_date="2026-01-01", end_date="2026-02-01")
    result = compare(queries(model, request, strategy)["sql"], model, request, strategy)
    assert result["status"] == "no_counterexample", result
    assert result["case_count"] == 28
    assert result["mismatch_count"] == 0


def test_refunded_order_change_has_replayable_counterexample():
    result = compare(CANDIDATE, RevenueModel(), RevenueRequest(), "item_value")
    assert result["status"] == "counterexample"
    witness = result["witness"]
    assert witness["name"] == "refunded"
    assert witness["expected"] == []
    assert witness["actual"] == [
        {"category": "Books", "revenue_minor": 90000},
        {"category": "Games", "revenue_minor": 10000},
    ]
    assert "INSERT INTO" in witness["fixture_sql"]
    assert "synthetic_orders" in witness["query_sqlite"]


@pytest.mark.parametrize(
    "old,new",
    [
        ("SUM(i.line_minor)", "SUM(o.net_minor)"),
        ("SUM(i.line_minor)", "SUM(DISTINCT i.line_minor)"),
        ("o.currency = 'INR'", "o.currency IN ('INR', 'USD')"),
        ("o.status = 'paid'", "o.status != 'not-paid'"),
    ],
)
def test_common_metric_changes_are_detected(old, new):
    result = compare(BASELINE.replace(old, new), RevenueModel(), RevenueRequest(), "item_value")
    assert result["status"] == "counterexample", result


def test_date_boundary_drift_detected():
    model = RevenueModel()
    request = RevenueRequest(start_date="2026-01-01", end_date="2026-02-01")
    sql = queries(model, request, "item_value")["sql"].replace(
        "< CAST('2026-02-01' AS DATE)", "<= CAST('2026-02-01' AS DATE)"
    )
    # Databricks generator uses CAST for DATE literals in this supported sqlglot version.
    assert sql != queries(model, request, "item_value")["sql"]
    result = compare(sql, model, request, "item_value")
    assert result["status"] == "counterexample", result
    assert result["witness"]["name"] == "exclusive_end"


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE demo.commerce.orders",
        "SELECT 1; SELECT 2",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM read_csv('file:///etc/passwd')",
        "SELECT load_extension('/tmp/anything')",
        "SELECT random() AS revenue_minor",
        "SELECT * FROM demo.commerce.secret",
        "WITH RECURSIVE t AS (SELECT 1) SELECT * FROM t",
        "SELECT * FROM demo.commerce.orders CROSS JOIN demo.commerce.order_items",
        "SELECT 1.5 AS revenue_minor",
        "SELECT * FROM demo.commerce.orders LIMIT 1",
        "SELECT * FROM demo.commerce.orders UNION SELECT * FROM demo.commerce.orders",
        "WITH synthetic_orders AS (SELECT 1) SELECT * FROM synthetic_orders",
        "SELECT /*+ BROADCAST(o) */ * FROM demo.commerce.orders o",
    ],
)
def test_unsupported_or_effectful_sql_is_refused_before_execution(sql):
    with pytest.raises(UnsupportedSQL):
        checked_tree(sql, RevenueModel())


def test_invalid_output_or_unknown_column_is_unsupported():
    assert (
        compare(
            BASELINE.replace("revenue_minor", "amount"),
            RevenueModel(),
            RevenueRequest(),
            "item_value",
        )["status"]
        == "unsupported"
    )
    assert (
        compare(
            BASELINE.replace("i.line_minor", "i.missing_column"),
            RevenueModel(),
            RevenueRequest(),
            "item_value",
        )["status"]
        == "unsupported"
    )


def test_comments_and_formatting_do_not_require_reapproval():
    model = RevenueModel()
    assert sql_hash(BASELINE, model) == sql_hash(
        "-- harmless comment\n" + BASELINE.replace("SELECT", "select"), model
    )


def test_execution_budget_is_enforced(monkeypatch):
    import lakecontext.querycheck as module

    monkeypatch.setattr(module, "MAX_STEPS", 0)
    fixture = copy.deepcopy(suite_v1(RevenueModel(), RevenueRequest())[-1]["fixture"])
    sql = BASELINE.replace(
        "JOIN demo.commerce.order_items AS i ON o.order_id = i.order_id",
        "JOIN demo.commerce.order_items AS i ON o.order_id = i.order_id JOIN demo.commerce.order_items AS j ON j.order_id = i.order_id JOIN demo.commerce.order_items AS k ON k.order_id = j.order_id",
    )
    with pytest.raises(UnsupportedSQL, match="interrupted"):
        run_query(sql, fixture, RevenueModel(), RevenueRequest(), "item_value")


def test_quoted_categories_and_null_regions_remain_data():
    model, request = RevenueModel(), RevenueRequest(group_by=["category", "region"])
    fixture = synthetic_fixture()
    fixture["items"][0]["category"] = "'); DROP TABLE synthetic_orders; --"
    fixture["orders"][0]["region"] = None
    result = run_query(
        queries(model, request, "item_value")["sql"], fixture, model, request, "item_value"
    )
    assert result["actual"] == result["expected"]


def test_sql_length_limit():
    with pytest.raises(UnsupportedSQL, match="characters"):
        checked_tree(BASELINE + " " * 16000, RevenueModel())
