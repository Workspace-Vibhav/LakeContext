"""Opt-in, read-only Bakehouse integration test; never runs in default CI."""

import json
import os
import sys

import pytest
from databricks.sdk import WorkspaceClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lakecontext.databricks import collect
from lakecontext.store import Store


@pytest.mark.skipif(
    not os.environ.get("LAKECONTEXT_LIVE_PROFILE"),
    reason="Set LAKECONTEXT_LIVE_PROFILE to opt in to live Databricks metadata requests",
)
async def test_bakehouse_import_refresh_and_mcp(tmp_path):
    client = WorkspaceClient(profile=os.environ["LAKECONTEXT_LIVE_PROFILE"])
    snapshot = collect(client, ["samples"], ["bakehouse"])
    expected_tables = {
        "samples.bakehouse.sales_customers",
        "samples.bakehouse.sales_transactions",
        "samples.bakehouse.sales_franchises",
        "samples.bakehouse.sales_suppliers",
    }
    assert expected_tables <= {table.name for table in snapshot.tables}
    path = tmp_path / "bakehouse.db"
    store = Store(path)
    first = store.ingest(snapshot)
    first_ids = {edge["id"] for edge in first["relationships"]}
    assert first_ids
    assert all(edge["status"] == "proposed" for edge in first["relationships"])
    refreshed = store.ingest(collect(client, ["samples"], ["bakehouse"]))
    assert {edge["id"] for edge in refreshed["relationships"]} == first_ids

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lakecontext.cli", "--db", str(path), "serve"],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        search = await session.call_tool("search_tables", {"query": "sales_transactions"})
        assert not search.isError
        assert (
            json.loads(search.content[0].text)["tables"][0]["name"]
            == "samples.bakehouse.sales_transactions"
        )

        description = await session.call_tool(
            "describe_table", {"name": "samples.bakehouse.sales_transactions"}
        )
        assert not description.isError
        columns = json.loads(description.content[0].text)["table"]["columns"]
        assert {"customerID", "franchiseID", "totalPrice"} <= {col["name"] for col in columns}

        approved = await session.call_tool("get_relationships", {})
        assert not approved.isError
        assert json.loads(approved.content[0].text)["relationships"] == []

        proposed = await session.call_tool(
            "get_context",
            {
                "question": "sales_transactions",
                "include_proposed": True,
            },
        )
        assert not proposed.isError
        context = json.loads(proposed.content[0].text)
        assert context["has_matches"]
        assert context["relationships"]
        assert all(table["citation"].startswith("table:") for table in context["tables"])
        assert all(edge["status"] == "proposed" for edge in context["relationships"])
