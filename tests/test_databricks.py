from types import SimpleNamespace as Obj
from unittest.mock import Mock

import pytest

from lakecontext.databricks import collect
from lakecontext.demo import demo_snapshot
from lakecontext.store import Store


def workspace():
    client = Mock()
    client.config.host = "https://example.databricks.com"
    client.current_user.me.return_value = Obj(id="123")
    client.schemas.list.return_value = iter([Obj(name="sales"), Obj(name="information_schema")])
    client.tables.list.return_value = iter([Obj(full_name="main.sales.customers")])
    client.tables.get.return_value = Obj(
        comment="Customer accounts",
        owner="team",
        table_type=Obj(value="MANAGED"),
        columns=[Obj(name="customer_id", type_text="BIGINT", comment="Identifier")],
    )
    return client


def test_collect_metadata_uses_current_identity_and_explicit_scope():
    client = workspace()
    snapshot = collect(client, ["main"], ["sales"])
    assert snapshot.principal == "123"
    assert snapshot.source == "https://example.databricks.com#scope=main/sales"
    assert snapshot.tables[0].columns[0].name == "customer_id"
    client.tables.list.assert_called_once_with(catalog_name="main", schema_name="sales")
    client.tables.get.assert_called_once_with(full_name="main.sales.customers")
    client.statement_execution.execute_statement.assert_not_called()


def test_failed_collection_does_not_replace_existing_graph(tmp_path):
    store = Store(tmp_path / "graph.db")
    store.ingest(demo_snapshot())
    before = store.read()
    client = workspace()
    client.tables.get.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        store.ingest(collect(client, ["main"]))
    assert store.read() == before


def test_missing_column_metadata_fails_instead_of_publishing_empty_schema():
    client = workspace()
    client.tables.get.return_value.columns = None
    with pytest.raises(ValueError, match="Column metadata unavailable"):
        collect(client, ["main"])


def test_schema_filter_excludes_unselected_schema():
    client = workspace()
    snapshot = collect(client, ["main"], ["finance"])
    assert snapshot.tables == []
    client.tables.list.assert_not_called()
