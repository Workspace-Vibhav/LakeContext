"""Reproducible synthetic story. Approvals below are SCRIPTED, not expert reviews.

Run: uv run python scripts/revenue_story.py
Creates an isolated run directory; never modifies the user's metadata database.
"""

import json
import tempfile
from pathlib import Path

from lakecontext.revenue import RevenueModel, RevenueRequest, demo_snapshot
from lakecontext.semantics import SemanticService
from lakecontext.store import Store


def run(output_root=Path(".lakecontext/revenue-story")):
    output_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=output_root))
    store = Store(directory / "story.db")
    store.ingest(demo_snapshot())
    service = SemanticService(store)
    owner = "scripted-demo-owner"
    evidence = "Synthetic demonstration choice; NOT expert-reviewed or a real business approval"
    service.register(RevenueModel(), owner, evidence)
    first = service.plan(RevenueRequest())
    confirmed = service.confirm(first["question_id"], "item_value", owner, evidence)
    followup = service.plan(
        RevenueRequest(
            group_by=["category", "region"], start_date="2026-01-01", end_date="2026-02-01"
        )
    )
    unsupported = service.plan(RevenueRequest(metric="recognized_revenue"))
    review = service.plan(RevenueRequest(), review_definition=True)
    conflict = service.confirm(review["question_id"], "full_credit", owner, evidence)
    blocked = service.plan(RevenueRequest())
    resolved = service.resolve(conflict["conflict"]["conflict_id"], "item_value", owner, evidence)
    snapshot = demo_snapshot()
    snapshot.tables[0].description = "Synthetic description change requires re-review"
    store.ingest(snapshot)
    changed = service.plan(RevenueRequest())
    checks = {
        "first_query_asks": first["status"] == "needs_clarification",
        "human_choice_saved": confirmed["status"] == "confirmed",
        "second_query_reuses": followup["status"] == "compiled" and followup["rule_reused"],
        "unsupported_meaning_refused": unsupported["status"] == "unsupported"
        and "sql" not in unsupported,
        "conflict_blocks_sql": blocked["status"] == "conflict" and "sql" not in blocked,
        "resolution_versioned": resolved["rule"]["revision"] == 2,
        "schema_change_requires_review": changed["status"] == "needs_clarification",
    }
    if not all(checks.values()):
        raise RuntimeError(f"Story failed: {checks}")
    story = {
        "kind": "scripted synthetic development demonstration",
        "expert_reviewed": False,
        "live_databricks_execution": False,
        "comparative_benchmark": False,
        "checks": checks,
        "first_question": first,
        "confirmation": confirmed,
        "followup": followup,
        "unsupported": unsupported,
        "conflict": conflict,
        "resolution": resolved,
        "after_schema_change": changed,
        "history": service.history(),
    }
    (directory / "story.json").write_text(json.dumps(story, indent=2) + "\n")
    (directory / "followup.databricks.sql").write_text(followup["sql"] + "\n")
    (directory / "preflight.databricks.sql").write_text(followup["preflight_sql"] + "\n")
    replay = followup["validation"]["cases"][0]
    (directory / "replay.sqlite.sql").write_text(
        "\n".join(replay[key] for key in ("fixture_sql", "preflight_sqlite", "query_sqlite")) + "\n"
    )
    return {"directory": str(directory.resolve()), "checks": checks}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
