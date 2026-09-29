import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lakecontext.demo import demo_snapshot
from lakecontext.store import Store


async def test_real_stdio_handshake_and_context_tool(tmp_path):
    path = tmp_path / "mcp.db"
    Store(path).ingest(demo_snapshot())
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lakecontext.cli", "--db", str(path), "serve"],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        initialized = await session.initialize()
        assert initialized.serverInfo.name == "LakeContext"
        listing = await session.list_tools()
        assert {tool.name for tool in listing.tools} == {
            "search_tables",
            "describe_table",
            "get_relationships",
            "get_context",
            "audit_metric_sql",
            "repair_metric_sql",
            "plan_revenue",
            "semantic_history",
            "review_revenue_change",
            "get_metric_impacts",
        }
        result = await session.call_tool("get_context", {"question": "booked revenue"})
        assert not result.isError
        payload = json.loads(result.content[0].text)
        assert payload["tables"][0]["name"] == "demo.sales.orders"
        assert payload["relationships"] == []
        error = await session.call_tool("describe_table", {"name": "unknown"})
        assert error.isError
        audited = await session.call_tool(
            "audit_metric_sql",
            {
                "sql": "SELECT SUM(o.order_id) FROM demo.sales.orders o LEFT JOIN demo.sales.customers c ON o.customer_id = c.customer_id",
            },
        )
        assert not audited.isError
        audit = json.loads(audited.content[0].text)["audit"]
        assert audit["counterexamples"][0]["joined_sum"] == 200
        unsafe = await session.call_tool(
            "audit_metric_sql", {"sql": "DROP TABLE demo.sales.orders"}
        )
        assert unsafe.isError
        sql = "SELECT SUM(o.order_id) AS total FROM demo.sales.orders o LEFT JOIN demo.sales.customers c ON o.customer_id = c.customer_id"
        question = await session.call_tool("repair_metric_sql", {"sql": sql})
        assert not question.isError
        assert json.loads(question.content[0].text)["repair"]["status"] == "needs_intent"
        repaired = await session.call_tool(
            "repair_metric_sql", {"sql": sql, "intent": "require_matches"}
        )
        assert not repaired.isError
        result = json.loads(repaired.content[0].text)["repair"]
        assert result["status"] == "repaired"
        assert "EXISTS" in result["corrected_sql"]
        assert result["validation"]["status"] == "passed"


async def test_semantic_question_then_human_confirmation_then_mcp_reuse(tmp_path):
    from lakecontext.revenue import RevenueModel, RevenueRequest
    from lakecontext.revenue import demo_snapshot as revenue_snapshot
    from lakecontext.semantics import SemanticService

    path = tmp_path / "revenue.db"
    store = Store(path)
    store.ingest(revenue_snapshot())
    semantics = SemanticService(store)
    semantics.register(RevenueModel(), "test owner", "Synthetic model")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "lakecontext.cli", "--db", str(path), "serve"]
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "plan_revenue", {"request": RevenueRequest().model_dump(mode="json")}
        )
        assert not result.isError
        question = json.loads(result.content[0].text)
        assert question["status"] == "needs_clarification"
        assert semantics.history()["rules"] == []
        semantics.confirm(
            question["question_id"], "item_value", "human test owner", "Scripted test answer"
        )
        request = RevenueRequest(group_by=["category", "region"]).model_dump(mode="json")
        result = await session.call_tool("plan_revenue", {"request": request})
        assert not result.isError
        plan = json.loads(result.content[0].text)
        assert plan["status"] == "compiled"
        assert plan["rule_reused"] is True
        history = await session.call_tool("semantic_history", {})
        assert json.loads(history.content[0].text)["rules"][0]["owner"] == "human test owner"

        from pathlib import Path

        from lakecontext.change_review import ChangeReviewService

        examples = Path(__file__).resolve().parents[1] / "examples"
        reviews = ChangeReviewService(store)
        reviews.register(
            "revenue",
            RevenueRequest(),
            (examples / "revenue-approved.sql").read_text(),
            "human test owner",
            "Synthetic query approval",
            ["Test report"],
        )
        result = await session.call_tool(
            "review_revenue_change",
            {"query_id": "revenue", "sql": (examples / "revenue-proposed.sql").read_text()},
        )
        assert not result.isError
        assert json.loads(result.content[0].text)["status"] == "blocked"
        impacts = await session.call_tool("get_metric_impacts", {"table": "demo.commerce.orders"})
        assert json.loads(impacts.content[0].text)["queries"][0]["consumers"] == ["Test report"]
        assert reviews.history()["reviews"][0]["review_status"] == "pending"
