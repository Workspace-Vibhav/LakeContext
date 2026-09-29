import copy
import sqlite3
from itertools import product

import pytest

from lakecontext.compiler import compile_snapshot
from lakecontext.demo import demo_snapshot
from lakecontext.models import Column
from lakecontext.witness import (
    UnsupportedQuery,
    audit,
    contract_holds,
    obligations,
    parse_plan,
    replay,
    seed_rows,
)

LEFT = "SELECT SUM(o.order_id) FROM demo.sales.orders o LEFT JOIN demo.sales.customers c ON o.customer_id = c.customer_id"
INNER = LEFT.replace("LEFT JOIN", "INNER JOIN")


@pytest.fixture
def graph():
    return compile_snapshot(demo_snapshot())


def test_twin_worlds_same_schema_different_sum(graph):
    result = audit(LEFT, graph)
    assert result["safe_example"]["joined_sum"] == 100
    failure = result["counterexamples"][0]
    assert (failure["base_sum"], failure["joined_sum"]) == (100, 200)
    assert failure["base_rows"] == 1 and failure["joined_rows"] == 2
    assert sum(len(t["rows"]) for t in failure["tables"]) == 3
    assert len(result["questions"]) == 1
    assert "at most one" in result["questions"][0]


def test_counterexamples_are_independently_replayable(graph):
    for sql in (LEFT, INNER):
        for witness in audit(sql, graph)["counterexamples"]:
            with sqlite3.connect(":memory:") as db:
                db.executescript(witness["fixture_sql"])
                assert db.execute(witness["base_sql"]).fetchone()[0] == witness["base_sum"]
                assert db.execute(witness["query_sql"]).fetchone()[0] == witness["joined_sum"]


def test_inner_join_needs_both_uniqueness_and_total_matching(graph):
    result = audit(INNER, graph)
    assert {w["failure"] for w in result["counterexamples"]} == {"fanout", "row_loss"}
    lost = next(w for w in result["counterexamples"] if w["failure"] == "row_loss")
    assert lost["joined_sum"] is None and lost["joined_rows"] == 0
    assert len(result["questions"]) == 2


def test_assumptions_close_only_their_corresponding_questions(graph):
    first = audit(INNER, graph)
    unique = next(c for c in first["contracts"] if c["kind"] == "unique_match_key")
    assumptions = [
        {"id": unique["id"], "evidence": "Hypothetical uniqueness declaration for this test"}
    ]
    second = audit(INNER, graph, assumptions)
    assert [w["failure"] for w in second["counterexamples"]] == ["row_loss"]
    all_claims = [
        {"id": c["id"], "evidence": "Hypothetical test assumption"} for c in first["contracts"]
    ]
    closed = audit(INNER, graph, all_claims)
    assert closed["verdict"] == "preserved_under_stated_contracts"
    assert closed["counterexamples"] == []
    assert all(c["status"] == "assumed" for c in closed["contracts"])


@pytest.mark.parametrize("change", ["time", "schema", "query", "identity"])
def test_stale_assumptions_do_not_survive_context_change(graph, change):
    first = audit(LEFT, graph)
    claims = [{"id": first["contracts"][0]["id"], "evidence": "old claim"}]
    sql = LEFT
    if change == "time":
        graph["captured_at"] = "2025-01-01T00:00:00+00:00"
    elif change == "schema":
        graph["tables"][0]["columns"][0]["description"] = "Meaning changed"
    elif change == "identity":
        graph["principal"] = "someone-else"
    else:
        sql = INNER
    with pytest.raises(ValueError, match="current contract"):
        audit(sql, graph, claims)


def test_relationship_approval_does_not_imply_uniqueness(graph):
    for edge in graph["relationships"]:
        edge["status"] = "approved"
    assert audit(LEFT, graph)["verdict"] == "counterexample_found"


def test_left_ancestor_requires_coverage_when_inner_descendant_rejects_nulls(graph):
    sql = "SELECT SUM(c.customer_id) FROM demo.sales.customers c LEFT JOIN demo.sales.orders o ON c.customer_id = o.customer_id INNER JOIN demo.finance.payments p ON o.order_id = p.order_id"
    # Use a separate integer measure so it is not also a join key.
    graph["tables"][0]["columns"].append(
        {"name": "value", "data_type": "BIGINT", "description": ""}
    )
    sql = sql.replace("SUM(c.customer_id)", "SUM(c.value)")
    result = audit(sql, graph)
    assert len(result["contracts"]) == 4
    assert len(result["counterexamples"]) == 4
    coverage = [c for c in result["contracts"] if c["kind"] == "total_match"]
    assert {c["table"] for c in coverage} == {"demo.sales.orders", "demo.finance.payments"}


def test_nullable_keys_do_not_satisfy_total_match(graph):
    plan = parse_plan(INNER, graph)
    coverage = next(c for c in obligations(plan, "test") if c["kind"] == "total_match")
    rows = seed_rows(plan)
    rows["o"][0]["customer_id"] = None
    rows["c"][0]["customer_id"] = None
    assert not contract_holds(coverage, rows)


def test_composite_contract_covers_whole_tuple():
    snapshot = demo_snapshot()
    for table in snapshot.tables[:2]:
        table.columns.append(Column(name="tenant_id", data_type="BIGINT"))
    graph = compile_snapshot(snapshot)
    sql = LEFT + " AND o.tenant_id = c.tenant_id"
    result = audit(sql, graph)
    assert set(result["contracts"][0]["columns"]) == {"tenant_id", "customer_id"}
    assert result["counterexamples"][0]["joined_sum"] == 200
    single = audit(LEFT, graph)["contracts"][0]
    with pytest.raises(ValueError, match="current contract"):
        audit(sql, graph, [{"id": single["id"], "evidence": "wrong key"}])


def test_minimizer_removes_irrelevant_left_branch(graph):
    sql = LEFT + " LEFT JOIN demo.finance.payments p ON o.customer_id = p.payment_id"
    result = audit(sql, graph)
    assert len(result["counterexamples"]) == 2
    assert all(sum(len(t["rows"]) for t in w["tables"]) == 3 for w in result["counterexamples"])


def test_no_join_requires_no_external_contract(graph):
    result = audit("SELECT SUM(o.order_id) FROM demo.sales.orders o", graph)
    assert result["contracts"] == []
    assert result["verdict"] == "preserved_under_stated_contracts"


@pytest.mark.parametrize(
    "first,second", [("LEFT", "LEFT"), ("LEFT", "INNER"), ("INNER", "LEFT"), ("INNER", "INNER")]
)
def test_contract_sufficiency_over_small_domains_including_nulls(graph, first, second):
    sql = (
        LEFT.replace("LEFT JOIN", f"{first} JOIN")
        + f" {second} JOIN demo.finance.payments p ON c.customer_id = p.payment_id"
    )
    plan = parse_plan(sql, graph)
    required = obligations(plan, "exhaustive-test")
    domain = (None, 1, 2)
    bags = [(), *[(k,) for k in domain], *product(domain, repeat=2)]
    checked = 0
    for base_key, customer_keys, payment_keys in product(domain, bags, bags):
        rows = {
            "o": [{"order_id": 100, "customer_id": base_key}],
            "c": [{"customer_id": key} for key in customer_keys],
            "p": [{"payment_id": key} for key in payment_keys],
        }
        if all(contract_holds(c, rows) for c in required):
            result = replay(plan, rows)
            assert result["base_sum"] == result["joined_sum"], (sql, rows)
            checked += 1
    assert checked > 0


def test_string_keys_use_valid_one_character_synthetic_values(graph):
    for table in graph["tables"]:
        for col in table["columns"]:
            if col["name"] == "customer_id":
                col["data_type"] = "varchar(1)"
    result = audit(LEFT, graph)
    assert result["counterexamples"][0]["joined_sum"] == 200
    assert result["safe_example"]["tables"][0]["rows"][0]["customer_id"] == "k"


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE demo.sales.orders",
        LEFT + "; DELETE FROM demo.sales.orders",
        LEFT + " WHERE o.order_id > 5",
        LEFT + " GROUP BY c.customer_id",
        LEFT + " LIMIT 1",
        LEFT.replace("SUM(o.order_id)", "SUM(DISTINCT o.order_id)"),
        LEFT.replace("SUM(o.order_id)", "SUM(o.order_id + 1)"),
        LEFT.replace("SUM(o.order_id)", "AVG(o.order_id)"),
        LEFT.replace("SUM(o.order_id)", "SUM(o.amount)"),
        LEFT.replace("SUM(o.order_id)", "SUM(c.customer_id)"),
        LEFT.replace("SUM(o.order_id)", "SUM(o.customer_id)"),
        LEFT.replace("LEFT JOIN", "FULL OUTER JOIN"),
        LEFT.replace("LEFT JOIN", "RIGHT JOIN"),
        LEFT.replace("o.customer_id = c.customer_id", "o.customer_id = c.customer_id OR 1=1"),
        LEFT.replace("o.customer_id = c.customer_id", "o.customer_id > c.customer_id"),
        LEFT.replace("demo.sales.customers", "missing.sales.customers"),
        LEFT.replace("demo.sales.customers", "demo.sales.orders"),
        LEFT.replace("SUM(o.order_id)", "SUM(order_id)"),
        "SELECT SUM(o.order_id) FROM (SELECT * FROM demo.sales.orders) o",
        "SELECT SUM(o.order_id) FROM demo.sales.orders o CROSS JOIN demo.sales.customers c",
    ],
)
def test_unsupported_sql_never_gets_a_safety_verdict(graph, sql):
    before = copy.deepcopy(graph)
    with pytest.raises(UnsupportedQuery):
        audit(sql, graph)
    assert graph == before
