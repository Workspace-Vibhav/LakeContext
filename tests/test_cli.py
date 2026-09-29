import json
from pathlib import Path

import pytest

from lakecontext.cli import main


def test_demo_search_and_review_cli(tmp_path, capsys):
    options = ["--db", str(tmp_path / "cli.db")]
    main([*options, "demo"])
    assert json.loads(capsys.readouterr().out)["tables"] == 3
    main([*options, "relationships", "--include-proposed"])
    edge = json.loads(capsys.readouterr().out)["relationships"][0]
    main(
        [
            *options,
            "review",
            edge["id"],
            "approved",
            "--reviewer",
            "test-reviewer",
            "--reason",
            "synthetic test approval",
        ]
    )
    assert json.loads(capsys.readouterr().out)["status"] == "approved"
    main([*options, "relationships"])
    result = json.loads(capsys.readouterr().out)
    assert len(result["relationships"]) == 1
    assert result["relationships"][0]["id"] == edge["id"]


def test_invalid_json_ingest_exits_nonzero_and_keeps_snapshot(tmp_path, capsys):
    options = ["--db", str(tmp_path / "cli.db")]
    main([*options, "demo"])
    capsys.readouterr()
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"source": "incomplete"}')
    with pytest.raises(SystemExit) as error:
        main([*options, "ingest", str(invalid)])
    assert error.value.code == 1
    assert "Error:" in capsys.readouterr().err
    main([*options, "search", "booked revenue"])
    assert json.loads(capsys.readouterr().out)["tables"][0]["name"] == "demo.sales.orders"


def test_repair_cli_requires_intent_and_can_emit_only_sql(tmp_path, capsys):
    options = ["--db", str(tmp_path / "repair.db")]
    main([*options, "demo"])
    capsys.readouterr()
    example = str(Path(__file__).resolve().parents[1] / "examples/demo-risk.sql")
    main([*options, "repair-sql", example])
    assert json.loads(capsys.readouterr().out)["repair"]["status"] == "needs_intent"
    main([*options, "repair-sql", example, "--intent", "preserve_eligibility", "--sql-only"])
    sql = capsys.readouterr().out
    assert "EXISTS" in sql and "SUM" in sql
    with pytest.raises(SystemExit) as error:
        main([*options, "repair-sql", example, "--sql-only"])
    assert error.value.code == 1


def test_revenue_cli_human_confirmation_reuse_and_invalidation(tmp_path, capsys):
    options = ["--db", str(tmp_path / "revenue.db")]
    examples = Path(__file__).resolve().parents[1] / "examples"
    main([*options, "revenue-demo"])
    assert json.loads(capsys.readouterr().out)["status"] == "registered"
    main([*options, "revenue-plan", str(examples / "revenue-request.json")])
    question = json.loads(capsys.readouterr().out)
    assert question["status"] == "needs_clarification"
    attribution = ["--owner", "test owner", "--evidence", "Scripted synthetic CLI test"]
    main([*options, "revenue-confirm", question["question_id"], "equal_categories", *attribution])
    rule = json.loads(capsys.readouterr().out)["rule"]
    main([*options, "revenue-plan", str(examples / "revenue-followup.json")])
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "compiled"
    assert plan["rule_reused"]
    assert plan["strategy"] == "equal_categories"
    main([*options, "revenue-invalidate", rule["rule_id"], *attribution])
    assert json.loads(capsys.readouterr().out)["status"] == "stale"
    main([*options, "revenue-history"])
    assert json.loads(capsys.readouterr().out)["rules"][0]["status"] == "stale"
