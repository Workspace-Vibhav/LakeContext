"""Synthetic SQL-change review, explicitly scripted approvals, no remote calls."""

import json
import tempfile
from pathlib import Path

from lakecontext.change_review import ChangeReviewService
from lakecontext.querycheck import check_regression
from lakecontext.revenue import RevenueModel, RevenueRequest, demo_snapshot
from lakecontext.semantics import SemanticService
from lakecontext.store import Store


def run(output_root=Path(".lakecontext/query-review-story")):
    output_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=output_root))
    store = Store(directory / "story.db")
    store.ingest(demo_snapshot())
    semantics = SemanticService(store)
    owner = "scripted-demo-owner"
    evidence = "SCRIPTED synthetic example, not a customer approval or expert review"
    semantics.register(RevenueModel(), owner, evidence)
    question = semantics.plan(RevenueRequest())
    semantics.confirm(question["question_id"], "item_value", owner, evidence)
    reviews = ChangeReviewService(store)
    examples = Path(__file__).resolve().parents[1] / "examples"
    baseline = (examples / "revenue-approved.sql").read_text()
    candidate = (examples / "revenue-proposed.sql").read_text()
    registered = reviews.register(
        "revenue-by-category",
        RevenueRequest(),
        baseline,
        owner,
        evidence,
        ["Weekly sales report", "Category performance dashboard"],
    )
    review = reviews.review("revenue-by-category", candidate)
    if review["status"] != "blocked":
        raise RuntimeError("The proposed change was not blocked")
    decision = reviews.decide(review["review_id"], "keep_definition", owner, evidence)
    contract = reviews.export("revenue-by-category")
    bad = check_regression(contract, candidate)
    good = check_regression(contract, decision["approved_sql"])
    snapshot = demo_snapshot()
    snapshot.tables[0].description = "Synthetic changed policy description"
    store.ingest(snapshot)
    impact = reviews.impacts("demo.commerce.orders")
    stale = reviews.review("revenue-by-category", candidate)
    checks = {
        "baseline_registered": registered["status"] == "registered",
        "change_blocked": review["status"] == "blocked",
        "counterexample_replayable": bool(review["comparison"]["witness"]["fixture_sql"]),
        "decision_versioned": decision["regression"]["query_revision"] == 2,
        "regression_rejects_bad_sql": bad["status"] == "blocked",
        "regression_accepts_approved_sql": good["status"] == "passed",
        "schema_change_blocks_review": stale["status"] == "needs_reapproval",
        "declared_reports_identified": len(impact["queries"][0]["consumers"]) == 2,
    }
    if not all(checks.values()):
        raise RuntimeError(f"Story failed: {checks}")
    report = {
        "kind": "scripted synthetic development demonstration",
        "expert_reviewed": False,
        "live_databricks_execution": False,
        "checks": checks,
        "review": review,
        "decision": decision["decision"],
        "regression_bad": bad,
        "regression_good": good,
        "impact_after_change": impact,
        "history": reviews.history(),
    }
    for name, body in (("report.json", report), ("revenue.contract.json", contract)):
        (directory / name).write_text(json.dumps(body, indent=2) + "\n")
    (directory / "approved.sql").write_text(decision["approved_sql"] + "\n")
    (directory / "preflight.databricks.sql").write_text(decision["preflight_sql"] + "\n")
    witness = review["comparison"]["witness"]
    (directory / "counterexample.sqlite.sql").write_text(
        "\n".join(witness[key] for key in ("fixture_sql", "preflight_sqlite", "query_sqlite"))
        + "\n"
    )
    return {"directory": str(directory.resolve()), "checks": checks}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
