import json

import pytest

from lakecontext.compiler import compile_snapshot
from lakecontext.demo import demo_snapshot
from lakecontext.models import Query, Snapshot


def observed(graph):
    return [e for e in graph["relationships"] if e["observed_query_count"]]


@pytest.mark.parametrize("name", ["customer_id", "CUSTOMER_ID", "customerID", "customerId"])
def test_identifier_conventions_produce_only_unverified_proposals(name):
    snapshot = Snapshot.model_validate(
        {
            "source": "test://identifier-conventions",
            "principal": "test-user",
            "tables": [
                {"name": f"test.sales.{table}", "columns": [{"name": name, "data_type": "bigint"}]}
                for table in ["customers", "transactions"]
            ],
        }
    )
    edges = compile_snapshot(snapshot)["relationships"]
    assert len(edges) == 1
    assert edges[0]["status"] == "proposed"
    assert edges[0]["evidence_strength"] == "heuristic"
    assert edges[0]["observed_query_count"] == 0


@pytest.mark.parametrize("name", ["paid", "grid", "valid", "ID", "id", "customerid"])
def test_ordinary_words_and_generic_ids_do_not_create_name_based_joins(name):
    snapshot = Snapshot.model_validate(
        {
            "source": "test://identifier-conventions",
            "principal": "test-user",
            "tables": [
                {"name": f"test.sales.{table}", "columns": [{"name": name, "data_type": "bigint"}]}
                for table in ["customers", "transactions"]
            ],
        }
    )
    assert compile_snapshot(snapshot)["relationships"] == []


def test_same_identifier_with_different_type_is_not_inferred():
    snapshot = Snapshot.model_validate(
        {
            "source": "test://identifier-conventions",
            "principal": "test-user",
            "tables": [
                {
                    "name": "test.sales.franchises",
                    "columns": [{"name": "franchiseID", "data_type": "bigint"}],
                },
                {
                    "name": "test.sales.reviews",
                    "columns": [{"name": "franchiseID", "data_type": "int"}],
                },
            ],
        }
    )
    assert compile_snapshot(snapshot)["relationships"] == []


def test_demo_evidence_and_no_raw_sql():
    graph = compile_snapshot(demo_snapshot())
    assert len(observed(graph)) == 2
    assert all(e["status"] == "proposed" for e in graph["relationships"])
    assert all(len(e["evidence"]) == 2 for e in graph["relationships"])
    assert "SELECT" not in json.dumps(graph)
    assert "WHERE" not in json.dumps(graph)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
        "SELECT * FROM demo.sales.orders o JOIN demo.sales.customers c ON customer_id = customer_id",
        "SELECT * FROM demo.sales.orders o JOIN demo.sales.customers c ON o.missing = c.customer_id",
        "SELECT * FROM demo.sales.orders o JOIN demo.sales.customers c ON o.customer_id = c.customer_id OR o.amount > 2",
        "SELECT * FROM demo.sales.orders o JOIN demo.sales.customers c ON NOT o.customer_id = c.customer_id",
        "SELECT * FROM demo.sales.orders o JOIN demo.sales.customers c USING(customer_id)",
        "WITH o AS (SELECT * FROM demo.sales.orders) SELECT * FROM o JOIN demo.sales.customers c ON o.customer_id = c.customer_id",
        "SELECT * FROM (SELECT * FROM demo.sales.orders) o JOIN demo.sales.customers c ON o.customer_id = c.customer_id",
        "SELECT * FROM demo.sales.orders o JOIN demo.sales.customers c ON CAST(o.customer_id AS STRING) = c.customer_id",
        "DELETE FROM demo.sales.orders",
        "SELECT 1; SELECT 2",
        "SELECT * FROM demo.sales.orders x JOIN demo.sales.customers x ON x.customer_id = x.customer_id",
        "SELECT * FROM demo.sales.orders o JOIN demo.sales.customers c ON other.sales.o.customer_id = c.customer_id",
    ],
)
def test_ambiguous_or_unsupported_sql_does_not_create_observed_edges(sql):
    snapshot = demo_snapshot()
    snapshot.queries = [Query(id="unsupported", sql=sql)]
    assert observed(compile_snapshot(snapshot)) == []


def test_conjunctive_predicates_and_case_insensitive_identifiers():
    snapshot = demo_snapshot()
    snapshot.queries = [
        Query(
            id="q",
            sql="SELECT * FROM DEMO.SALES.ORDERS O JOIN demo.sales.customers C ON O.CUSTOMER_ID = C.customer_id AND O.status = 'paid'",
        )
    ]
    graph = compile_snapshot(snapshot)
    assert len(observed(graph)) == 1
    assert observed(graph)[0]["observed_query_count"] == 1


def test_duplicate_predicate_does_not_inflate_evidence():
    snapshot = demo_snapshot()
    snapshot.queries = [
        Query(
            id="q",
            sql="SELECT * FROM demo.sales.orders o JOIN demo.sales.customers c ON o.customer_id = c.customer_id AND c.customer_id = o.customer_id",
        )
    ]
    assert observed(compile_snapshot(snapshot))[0]["observed_query_count"] == 1


def test_invalid_reference_and_duplicate_ids_rejected():
    data = demo_snapshot().model_dump()
    data["terms"][0]["tables"] = ["unknown.table.name"]
    with pytest.raises(ValueError, match="Unknown table"):
        type(demo_snapshot()).model_validate(data)
    data = demo_snapshot().model_dump()
    data["queries"].append(data["queries"][0])
    with pytest.raises(ValueError, match="Duplicate query IDs"):
        type(demo_snapshot()).model_validate(data)
