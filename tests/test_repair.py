import copy
import sqlite3
from itertools import product

import pytest
import sqlglot

from lakecontext.compiler import compile_snapshot
from lakecontext.demo import demo_snapshot
from lakecontext.models import Column, Snapshot
from lakecontext.repair import (
    INTENTS,
    evaluate_repair,
    generate_sql,
    intent_result,
    parse_plan,
    repair,
)
from lakecontext.service import ContextService
from lakecontext.store import Store
from lakecontext.witness import UnsupportedQuery, seed_rows

LEFT = "SELECT SUM(o.order_id) AS total FROM demo.sales.orders o LEFT JOIN demo.sales.customers c ON o.customer_id = c.customer_id"
INNER = LEFT.replace("LEFT JOIN", "INNER JOIN")


@pytest.fixture
def graph():
    return compile_snapshot(demo_snapshot())


def test_no_intent_means_no_silent_rewrite(graph):
    result = repair(LEFT, graph)
    assert result["status"] == "needs_intent"
    assert set(result["available_intents"]) == set(INTENTS)
    assert "corrected_sql" not in result


@pytest.mark.parametrize("intent", INTENTS)
def test_repairs_validate_and_preserve_projection_alias(graph, intent):
    result = repair(LEFT, graph, intent)
    assert result["status"] == "repaired"
    assert result["validation"]["case_count"] >= 10
    assert all(case["passed"] for case in result["validation"]["cases"])
    ast = sqlglot.parse_one(result["corrected_sql"], read="databricks")
    assert ast.expressions[0].alias == "total"
    assert "DISTINCT" not in result["corrected_sql"]
    assert "JOIN" not in result["corrected_sql"]


def test_each_intent_has_different_eligibility_when_matches_missing(graph):
    plan = parse_plan(LEFT, graph)
    rows = seed_rows(plan)
    rows["c"] = []
    expectations = {"preserve_base": 100, "preserve_eligibility": 100, "require_matches": None}
    for intent, expected in expectations.items():
        result = evaluate_repair(generate_sql(plan, intent), plan, rows, intent)
        assert result["passed"]
        assert result["repaired_sum"] == expected
    inner = parse_plan(INNER, graph)
    eligible = evaluate_repair(
        generate_sql(inner, "preserve_eligibility"), inner, rows, "preserve_eligibility"
    )
    assert eligible["repaired_sum"] is None
    all_rows = evaluate_repair(generate_sql(inner, "preserve_base"), inner, rows, "preserve_base")
    assert all_rows["repaired_sum"] == 100


def test_equal_valued_physical_base_rows_are_not_deduplicated(graph):
    plan = parse_plan(LEFT, graph)
    rows = seed_rows(plan)
    rows["o"].append(rows["o"][0].copy())
    rows["c"].append(rows["c"][0].copy())
    for intent in INTENTS:
        result = evaluate_repair(generate_sql(plan, intent), plan, rows, intent)
        assert result["original_sum"] == 400
        assert result["repaired_sum"] == 200
        assert result["repaired_base_rows"] == 2


def test_empty_and_all_null_measures_remain_sql_null(graph):
    plan = parse_plan(INNER, graph)
    for intent in INTENTS:
        rows = seed_rows(plan)
        rows["o"][0]["order_id"] = None
        assert (
            evaluate_repair(generate_sql(plan, intent), plan, rows, intent)["repaired_sum"] is None
        )
        rows["o"] = []
        result = evaluate_repair(generate_sql(plan, intent), plan, rows, intent)
        assert result["repaired_sum"] is None
        assert result["repaired_base_rows"] == 0


def test_composite_key_requires_every_component():
    snapshot = demo_snapshot()
    for table in snapshot.tables[:2]:
        table.columns.append(Column(name="tenant_id", data_type="BIGINT"))
    graph = compile_snapshot(snapshot)
    plan = parse_plan(LEFT + " AND o.tenant_id = c.tenant_id", graph)
    rows = seed_rows(plan)
    rows["c"][0]["tenant_id"] = 2
    result = evaluate_repair(generate_sql(plan, "require_matches"), plan, rows, "require_matches")
    assert result["passed"] and result["repaired_sum"] is None


def branching_graph():
    return compile_snapshot(
        Snapshot.model_validate(
            {
                "source": "test://branches",
                "principal": "test-user",
                "tables": [
                    {
                        "name": "test.sales.transactions",
                        "columns": [
                            {"name": "amount", "data_type": "BIGINT"},
                            {"name": "account_id", "data_type": "BIGINT"},
                        ],
                    },
                    {
                        "name": "test.sales.accounts",
                        "columns": [
                            {"name": "account_id", "data_type": "BIGINT"},
                            {"name": "segment", "data_type": "BIGINT"},
                        ],
                    },
                    {
                        "name": "test.sales.a",
                        "columns": [{"name": "segment", "data_type": "BIGINT"}],
                    },
                    {
                        "name": "test.sales.b",
                        "columns": [{"name": "segment", "data_type": "BIGINT"}],
                    },
                ],
            }
        )
    )


BRANCH = "SELECT SUM(t.amount) FROM test.sales.transactions t LEFT JOIN test.sales.accounts p ON t.account_id = p.account_id INNER JOIN test.sales.a a ON p.segment = a.segment INNER JOIN test.sales.b b ON p.segment = b.segment"


@pytest.mark.parametrize("intent", ["preserve_eligibility", "require_matches"])
def test_branches_must_match_the_same_parent_row(intent):
    plan = parse_plan(BRANCH, branching_graph())
    rows = {
        "t": [{"amount": 100, "account_id": 1}],
        "p": [{"account_id": 1, "segment": 1}, {"account_id": 1, "segment": 2}],
        "a": [{"segment": 1}],
        "b": [{"segment": 2}],
    }
    result = evaluate_repair(generate_sql(plan, intent), plan, rows, intent)
    assert result["passed"]
    assert result["original_sum"] is None
    assert result["repaired_sum"] is None
    # If one parent satisfies both branches, the base row is eligible exactly once.
    rows["b"].append({"segment": 1})
    result = evaluate_repair(generate_sql(plan, intent), plan, rows, intent)
    assert result["passed"] and result["repaired_sum"] == 100


def test_optional_only_subtrees_do_not_restrict_eligibility():
    plan = parse_plan(BRANCH.replace("INNER JOIN", "LEFT JOIN"), branching_graph())
    rows = seed_rows(plan)
    rows["p"] = []
    result = evaluate_repair(
        generate_sql(plan, "preserve_eligibility"), plan, rows, "preserve_eligibility"
    )
    assert result["passed"] and result["repaired_sum"] == 100


def test_mixed_chain_requires_left_ancestor_when_inner_descendant_exists(graph):
    sql = LEFT + " INNER JOIN demo.finance.payments p ON c.customer_id = p.payment_id"
    plan = parse_plan(sql, graph)
    rows = seed_rows(plan)
    rows["c"] = []
    result = evaluate_repair(
        generate_sql(plan, "preserve_eligibility"), plan, rows, "preserve_eligibility"
    )
    assert result["passed"] and result["repaired_sum"] is None


@pytest.mark.parametrize("first,second", list(product(["LEFT", "INNER"], repeat=2)))
def test_repairs_match_independent_bag_semantics_over_small_domains(graph, first, second):
    sql = (
        LEFT.replace("LEFT JOIN", f"{first} JOIN")
        + f" {second} JOIN demo.finance.payments p ON c.customer_id = p.payment_id"
    )
    plan = parse_plan(sql, graph)
    generated = {intent: generate_sql(plan, intent) for intent in INTENTS}
    domain = (None, 1, 2)
    bags = [(), *[(key,) for key in domain], *product(domain, repeat=2)]
    for key, customer_keys, payment_keys in product(domain, bags, bags):
        rows = {
            "o": [{"order_id": 100, "customer_id": key}],
            "c": [{"customer_id": value} for value in customer_keys],
            "p": [{"payment_id": value} for value in payment_keys],
        }
        for intent in INTENTS:
            result = evaluate_repair(generated[intent], plan, rows, intent)
            assert result["passed"], (sql, rows, intent, result)


def test_independent_oracle_tracks_eligibility_not_only_sum(graph):
    plan = parse_plan(INNER, graph)
    rows = seed_rows(plan)
    rows["o"] = [{"order_id": 100, "customer_id": 1}, {"order_id": -100, "customer_id": 2}]
    assert intent_result(plan, rows, "preserve_eligibility") == {"sum": 100, "base_rows": 1}


def test_delivered_sql_fixtures_are_independently_replayable(graph):
    result = repair(INNER, graph, "preserve_eligibility")
    for case in result["validation"]["cases"]:
        with sqlite3.connect(":memory:") as db:
            db.executescript(case["fixture_sql"])
            assert db.execute(case["repaired_sqlite_sql"]).fetchone()[0] == case["expected_sum"]


def test_failure_withholds_corrected_sql(graph, monkeypatch):
    import lakecontext.repair as module

    monkeypatch.setattr(module, "generate_sql", lambda plan, intent: "SELECT 999;")
    result = repair(LEFT, graph, "preserve_base")
    assert result["status"] == "validation_failed"
    assert "corrected_sql" not in result
    assert result["failed_cases"]


def test_quoted_identifiers_and_output_alias_are_escaped(graph):
    sql = LEFT.replace("AS total", "AS `total; not a command`")
    result = repair(sql, graph, "require_matches")
    assert result["status"] == "repaired"
    assert (
        sqlglot.parse_one(result["corrected_sql"], read="databricks").expressions[0].alias
        == "total; not a command"
    )


@pytest.mark.parametrize(
    "sql",
    [
        LEFT + " WHERE o.order_id > 1",
        LEFT + " GROUP BY c.customer_id",
        "DROP TABLE demo.sales.orders",
        LEFT.replace("SUM(o.order_id)", "SUM(o.amount)"),
    ],
)
def test_unsupported_inputs_are_not_repaired(graph, sql):
    before = copy.deepcopy(graph)
    with pytest.raises(UnsupportedQuery):
        repair(sql, graph, "preserve_base")
    assert graph == before


def test_unknown_intent_is_rejected(graph):
    with pytest.raises(ValueError, match="Unknown intent"):
        repair(LEFT, graph, "guess")


def test_service_rejects_stale_snapshot_before_repair(tmp_path):
    store = Store(tmp_path / "stale.db")
    snapshot = demo_snapshot()
    snapshot.captured_at = "2000-01-01T00:00:00+00:00"
    store.ingest(snapshot)
    with pytest.raises(ValueError, match="expired"):
        ContextService(store).repair_sql(LEFT, "preserve_base")
