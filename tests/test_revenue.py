import copy
from datetime import date
from itertools import product

import pytest

from lakecontext.revenue import (
    MAX_MINOR,
    STRATEGIES,
    RevenueModel,
    RevenueRequest,
    compile_plan,
    evaluate,
    synthetic_fixture,
    witness,
)


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("group_by", [["category"], ["category", "region"]])
def test_delivered_sql_passes_valid_cases_and_blocks_invalid_cases(strategy, group_by):
    plan = compile_plan(
        RevenueModel(),
        RevenueRequest(group_by=group_by, start_date=date(2026, 1, 1), end_date=date(2026, 2, 1)),
        strategy,
    )
    assert plan["status"] == "compiled"
    assert plan["validation"]["case_count"] >= 14
    assert "preflight" in plan["execution_gate"]
    assert plan["additive_across_categories"] == (strategy != "full_credit")


def test_question_selects_small_example_separating_all_interpretations():
    result = witness(RevenueModel(), RevenueRequest())
    assert result["selection"]["separated_interpretation_pairs"] == 3
    assert result["selection"]["total_fixture_rows"] == 3
    outputs = {option["id"]: option.get("results") for option in result["options"]}
    assert [r["revenue_minor"] for r in outputs["item_value"]] == [90000, 10000]
    assert [r["revenue_minor"] for r in outputs["equal_categories"]] == [50000, 50000]
    assert [r["revenue_minor"] for r in outputs["full_credit"]] == [100000, 100000]
    assert outputs["none"] is None
    assert all(r["status"] == "passed" for r in result["replays"].values())


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_small_integer_grid_and_repeated_categories(strategy):
    model, request = RevenueModel(), RevenueRequest()
    for amounts in product(range(3), repeat=3):
        for repeated in (False, True):
            fixture = synthetic_fixture(amounts)
            if repeated:
                fixture["items"][2]["category"] = "Books"
            result = evaluate(fixture, model, request, strategy)
            assert result["status"] == "passed", (amounts, repeated, result)
            if strategy != "full_credit":
                assert sum(r["revenue_minor"] for r in result["actual"]) == sum(amounts)


def test_exact_residual_assignment_at_supported_upper_bound():
    fixture = synthetic_fixture((MAX_MINOR - 2, 1, 1))
    result = evaluate(fixture, RevenueModel(), RevenueRequest(), "equal_categories")
    assert result["status"] == "passed"
    assert [r["revenue_minor"] for r in result["actual"]] == [
        333333333334,
        333333333333,
        333333333333,
    ]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_dates_regions_multiple_orders_and_excluded_currency(strategy):
    fixture = synthetic_fixture((90, 10))
    for oid, day, region, currency in (
        (2, "2026-01-01", "South", "INR"),
        (3, "2026-02-01", "North", "INR"),
        (4, "2026-01-31", None, "USD"),
    ):
        fixture["orders"].append(
            {
                **fixture["orders"][0],
                "id": oid,
                "booked_on": day,
                "region": region,
                "currency": currency,
            }
        )
        fixture["items"].extend(
            {**item, "id": oid * 10 + item["id"], "order_id": oid}
            for item in copy.deepcopy(fixture["items"][:2])
        )
    request = RevenueRequest(
        group_by=["category", "region"], start_date=date(2026, 1, 1), end_date=date(2026, 2, 1)
    )
    result = evaluate(fixture, RevenueModel(), request, strategy)
    assert result["status"] == "passed"
    assert len(result["actual"]) == 4
    assert {r["region"] for r in result["actual"]} == {"North", "South"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("net_minor", None),
        ("net_minor", -1),
        ("net_minor", MAX_MINOR + 1),
        ("booked_on", None),
        ("id", None),
    ],
)
def test_invalid_order_is_not_silently_excluded_by_date_filter(field, value):
    fixture = synthetic_fixture()
    fixture["orders"][0][field] = value
    request = RevenueRequest(start_date=date(2026, 1, 1))
    assert (
        evaluate(fixture, RevenueModel(), request, "item_value")["status"] == "blocked_by_preflight"
    )


@pytest.mark.parametrize(
    "field,value", [("line_minor", None), ("category", ""), ("category", "  "), ("id", None)]
)
def test_invalid_item_is_blocked(field, value):
    fixture = synthetic_fixture()
    fixture["items"][0][field] = value
    assert (
        evaluate(fixture, RevenueModel(), RevenueRequest(), "item_value")["status"]
        == "blocked_by_preflight"
    )


@pytest.mark.parametrize(
    "status", ["paid'booked", "paid\\booked", "paid\\'; DROP TABLE orders; --"]
)
def test_custom_mapping_and_quoted_values_replay(status):
    model = RevenueModel(
        paid_status=status,
        order_columns={
            "id": "order key",
            "net_minor": "amount",
            "status": "state",
            "currency": "ccy",
            "booked_on": "day",
            "region": "area",
        },
    )
    fixture = synthetic_fixture(model=model)
    fixture["items"][0]["category"] = "Kid's books"
    assert evaluate(fixture, model, RevenueRequest(), "item_value")["status"] == "passed"


@pytest.mark.parametrize(
    "updates",
    [
        {"metric": "recognized_revenue"},
        {"currency": "USD"},
        {"group_by": ["product"]},
        {"basis": "after_refunds"},
        {"definition_version": "v2"},
    ],
)
def test_unsupported_business_meaning_returns_no_sql(updates):
    result = compile_plan(RevenueModel(), RevenueRequest(**updates), "item_value")
    assert result["status"] == "unsupported"
    assert "sql" not in result
