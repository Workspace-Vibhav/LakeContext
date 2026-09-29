import json
from datetime import UTC, datetime, timedelta

import pytest

from lakecontext.demo import demo_snapshot
from lakecontext.service import ContextService
from lakecontext.store import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "graph.db")
    db.ingest(demo_snapshot())
    return db


def test_review_controls_retrieval_and_records_attribution(store):
    service = ContextService(store)
    assert service.relationships()["relationships"] == []
    edges = service.relationships(include_proposed=True)["relationships"]
    store.review(edges[0]["id"], "approved", "reviewer", "checked join semantics")
    store.review(edges[1]["id"], "rejected", "reviewer", "different grain")
    assert len(service.relationships()["relationships"]) == 1
    assert len(service.relationships(include_proposed=True)["relationships"]) == 1
    assert service.relationships()["relationships"][0]["review"]["reviewer"] == "reviewer"
    assert "rejected" in json.dumps(store.read())


def test_sync_keeps_approval_but_type_change_resets_it(store):
    edge = store.read()["relationships"][0]
    store.review(edge["id"], "approved", "human", "verified")
    snapshot = demo_snapshot()
    store.ingest(snapshot)
    assert ContextService(store).relationships()["relationships"]
    endpoint = edge["left"]
    table = next(t for t in snapshot.tables if t.name == endpoint["table"])
    next(c for c in table.columns if c.name == endpoint["column"]).data_type = "STRING"
    store.ingest(snapshot)
    assert ContextService(store).relationships()["relationships"] == []
    store.ingest(demo_snapshot())
    assert ContextService(store).relationships()["relationships"] == []


def test_deleted_table_and_edges_do_not_reappear_in_context(store):
    snapshot = demo_snapshot()
    snapshot.tables = [t for t in snapshot.tables if t.name != "demo.finance.payments"]
    snapshot.queries = snapshot.queries[:1]
    store.ingest(snapshot)
    assert "demo.finance.payments" not in json.dumps(store.read())
    assert len(store.read()["relationships"]) == 1


@pytest.mark.parametrize(
    "field,value", [("principal", "other-user"), ("source", "other-workspace")]
)
def test_identity_and_source_cannot_replace_snapshot(store, field, value):
    before = store.read()
    snapshot = demo_snapshot()
    setattr(snapshot, field, value)
    with pytest.raises(ValueError, match="mismatch"):
        store.ingest(snapshot)
    assert store.read() == before


def test_business_term_search_and_citations(store):
    service = ContextService(store)
    result = service.search("booked revenue")
    assert result["tables"][0]["name"] == "demo.sales.orders"
    context = service.context("booked revenue")
    assert context["terms"][0]["citation"] == "term:Booked revenue"
    assert context["relationships"] == []
    assert context["tables"][0]["citation"] == "table:demo.sales.orders"
    assert service.context("zzzzunknown")["has_matches"] is False


def test_context_expands_only_approved_one_hop_and_respects_budget(store):
    for edge in store.read()["relationships"]:
        store.review(edge["id"], "approved", "human", "verified")
    service = ContextService(store)
    context = service.context("booked revenue", limit=3)
    assert len(context["tables"]) == 3
    assert len(context["relationships"]) == 2
    limited = service.context("booked revenue", limit=1)
    assert len(limited["tables"]) == 1
    assert limited["relationships"] == []


@pytest.mark.parametrize("hours", [-25, 1])
def test_expired_and_future_snapshots_fail_closed(store, hours):
    snapshot = demo_snapshot()
    snapshot.captured_at = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
    store.ingest(snapshot)
    with pytest.raises(ValueError, match="expired|future"):
        ContextService(store).search("orders")
    assert store.read()["tables"]  # Local admin export remains available.


def test_database_owner_only_and_no_original_sql(store):
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert b"SELECT" not in store.path.read_bytes()


def test_invalid_review_and_limit(store):
    with pytest.raises(ValueError, match="Unknown relationship"):
        store.review("missing", "approved", "human", "reason")
    with pytest.raises(ValueError, match="required"):
        store.review("missing", "approved", "", "")
    with pytest.raises(ValueError, match="limit"):
        ContextService(store).search("orders", 100)
