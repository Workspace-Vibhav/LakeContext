import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lakecontext.change_review import ChangeReviewService
from lakecontext.cli import main
from lakecontext.querycheck import check_regression
from lakecontext.revenue import RevenueModel, RevenueRequest, demo_snapshot
from lakecontext.semantics import SemanticService
from lakecontext.store import Store

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
BASELINE = (EXAMPLES / "revenue-approved.sql").read_text()
CANDIDATE = (EXAMPLES / "revenue-proposed.sql").read_text()


@pytest.fixture
def services(tmp_path):
    store = Store(tmp_path / "review.db")
    store.ingest(demo_snapshot())
    semantics = SemanticService(store)
    semantics.register(RevenueModel(), "test owner", "Synthetic test configuration")
    question = semantics.plan(RevenueRequest())
    semantics.confirm(
        question["question_id"], "item_value", "test owner", "Synthetic test definition"
    )
    reviews = ChangeReviewService(store)
    result = reviews.register(
        "revenue",
        RevenueRequest(),
        BASELINE,
        "test owner",
        "Test baseline",
        ["Weekly report", "Sales dashboard"],
    )
    assert result["status"] == "registered"
    return store, semantics, reviews


def test_complete_owner_decision_exports_regression_and_retains_history(services):
    store, _, reviews = services
    review = reviews.review("revenue", CANDIDATE)
    assert review["status"] == "blocked"
    assert review["consumers"] == ["Sales dashboard", "Weekly report"]
    assert "approve_candidate" not in review["allowed_decisions"]
    decision = reviews.decide(
        review["review_id"], "keep_definition", "metric owner", "Keep paid-status-only metric"
    )
    assert decision["regression"]["query_revision"] == 2
    assert "preflight_sql" in decision
    restarted = ChangeReviewService(Store(store.path))
    bundle = restarted.export("revenue")
    assert check_regression(bundle, CANDIDATE)["status"] == "blocked"
    assert check_regression(bundle, decision["approved_sql"])["status"] == "passed"
    assert restarted.history()["reviews"][0]["decision"]["owner"] == "metric owner"
    with pytest.raises(ValueError, match="already decided"):
        restarted.decide(review["review_id"], "keep_definition", "owner", "Replayed decision")


def test_changed_sql_passing_fixtures_still_needs_owner_review(services):
    _, _, reviews = services
    different_sql = BASELINE.replace("SUM(i.line_minor)", "SUM(i.line_minor + 0)")
    review = reviews.review("revenue", different_sql)
    assert review["status"] == "review_required"
    assert review["comparison"]["mismatch_count"] == 0
    assert check_regression(reviews.export("revenue"), different_sql)["status"] == "review_required"
    decision = reviews.decide(
        review["review_id"],
        "approve_candidate",
        "owner",
        "Reviewed semantically equivalent rewrite",
    )
    assert decision["approved_sql"] == different_sql
    assert check_regression(decision["regression"], different_sql)["status"] == "passed"


def test_failing_candidate_cannot_be_waved_through(services):
    _, _, reviews = services
    review = reviews.review("revenue", CANDIDATE)
    with pytest.raises(ValueError, match="Cannot approve"):
        reviews.decide(review["review_id"], "approve_candidate", "owner", "Override")
    assert reviews.history()["reviews"][0]["review_status"] == "pending"
    decision = reviews.decide(
        review["review_id"], "reject_change", "owner", "Reject unintended change"
    )
    assert decision["approved_sql"] == BASELINE
    assert decision["regression"]["query_revision"] == 1


def test_unchanged_and_unsupported_do_not_create_approval_receipts(services):
    _, _, reviews = services
    assert reviews.review("revenue", BASELINE)["status"] == "unchanged"
    assert reviews.review("revenue", "DROP TABLE demo.commerce.orders")["status"] == "unsupported"
    assert reviews.history()["reviews"] == []


def test_bad_baseline_cannot_be_registered_and_replace_is_explicit(services):
    _, _, reviews = services
    assert (
        reviews.register("bad", RevenueRequest(), CANDIDATE, "owner", "Test candidate")["status"]
        == "registration_refused"
    )
    with pytest.raises(ValueError, match="already registered"):
        reviews.register("revenue", RevenueRequest(), BASELINE, "owner", "Implicit replacement")
    assert [q["query_id"] for q in reviews.impacts()["queries"]] == ["revenue"]


def test_registration_requires_confirmed_definition(tmp_path):
    store = Store(tmp_path / "unconfirmed.db")
    store.ingest(demo_snapshot())
    SemanticService(store).register(RevenueModel(), "owner", "Synthetic mapping")
    with pytest.raises(ValueError, match="confirmed"):
        ChangeReviewService(store).register(
            "revenue", RevenueRequest(), BASELINE, "owner", "No confirmed meaning"
        )


def test_schema_change_reports_impacts_and_blocks_pending_approval_even_after_revert(services):
    store, _, reviews = services
    review = reviews.review("revenue", CANDIDATE)
    snapshot = demo_snapshot()
    snapshot.tables[0].columns[1].description = "Amount now includes shipping"
    store.ingest(snapshot)
    impacted = reviews.impacts("demo.commerce.orders")["queries"]
    assert impacted[0]["status"] == "needs_reapproval"
    assert len(impacted[0]["consumers"]) == 2
    assert reviews.impacts("other.schema.table")["queries"] == []
    store.ingest(demo_snapshot())
    with pytest.raises(ValueError, match="confirmed"):
        reviews.decide(review["review_id"], "keep_definition", "owner", "Old review")
    assert reviews.review("revenue", BASELINE)["status"] == "needs_reapproval"


def test_identical_metadata_refresh_preserves_query_approval(services):
    store, _, reviews = services
    store.ingest(demo_snapshot())
    assert reviews.review("revenue", BASELINE)["status"] == "unchanged"
    assert reviews.impacts()["queries"][0]["status"] == "current"


def test_definition_conflict_blocks_query_approval_and_export(services):
    _, semantics, reviews = services
    review = reviews.review("revenue", CANDIDATE)
    question = semantics.plan(RevenueRequest(), review_definition=True)
    semantics.confirm(question["question_id"], "full_credit", "other owner", "Conflicting meaning")
    assert reviews.review("revenue", BASELINE)["status"] == "needs_reapproval"
    with pytest.raises(ValueError, match="conflict"):
        reviews.decide(review["review_id"], "keep_definition", "owner", "Old review")
    with pytest.raises(ValueError, match="conflict"):
        reviews.export("revenue")


def test_intervening_query_revision_invalidates_receipt(services):
    _, _, reviews = services
    first = reviews.review("revenue", CANDIDATE)
    second = reviews.review("revenue", CANDIDATE)
    reviews.decide(first["review_id"], "keep_definition", "owner", "First approval")
    with pytest.raises(ValueError, match="changed after review"):
        reviews.decide(second["review_id"], "keep_definition", "owner", "Stale approval")


def test_expired_receipt_and_missing_owner_refused(services):
    store, _, reviews = services
    review = reviews.review("revenue", CANDIDATE)
    with pytest.raises(ValueError, match="required"):
        reviews.decide(review["review_id"], "keep_definition", "", "No owner")
    with store.connection() as db:
        body = json.loads(
            db.execute(
                "SELECT body FROM query_reviews WHERE review_id=?", (review["review_id"],)
            ).fetchone()[0]
        )
        body["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        db.execute(
            "UPDATE query_reviews SET body=? WHERE review_id=?",
            (json.dumps(body), review["review_id"]),
        )
    with pytest.raises(ValueError, match="expired"):
        reviews.decide(review["review_id"], "keep_definition", "owner", "Late response")


def test_modified_exported_expectations_and_unsupported_bundle_version_refused(services):
    _, _, reviews = services
    bundle = reviews.export("revenue")
    corrupt = copy.deepcopy(bundle)
    corrupt["cases"][0]["expected"][0]["revenue_minor"] = 123
    with pytest.raises(ValueError, match="differ"):
        check_regression(corrupt, BASELINE)
    bundle["bundle_version"] = 2
    with pytest.raises(ValueError):
        check_regression(bundle, BASELINE)


def test_cli_nonzero_gates_and_offline_check_does_not_create_metadata_database(
    services, tmp_path, capsys
):
    store, _, _reviews = services
    options = ["--db", str(store.path)]
    with pytest.raises(SystemExit) as error:
        main([*options, "query-review", "revenue", str(EXAMPLES / "revenue-proposed.sql")])
    assert error.value.code == 1
    review = json.loads(capsys.readouterr().out)
    main(
        [
            *options,
            "query-decide",
            review["review_id"],
            "keep_definition",
            "--owner",
            "test owner",
            "--evidence",
            "Test CLI approval",
        ]
    )
    decision = json.loads(capsys.readouterr().out)
    contract = tmp_path / "contract.json"
    approved = tmp_path / "approved.sql"
    approved.write_text(decision["approved_sql"])
    main([*options, "query-export", "revenue", "--output", str(contract)])
    assert json.loads(capsys.readouterr().out)["status"] == "exported"
    assert contract.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SystemExit):
        main([*options, "query-export", "revenue", "--output", str(contract)])
    capsys.readouterr()
    absent_db = tmp_path / "must-not-exist.db"
    main(["--db", str(absent_db), "check-regression", str(contract), str(approved)])
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
    assert not absent_db.exists()
    with pytest.raises(SystemExit) as error:
        main(["check-regression", str(contract), str(EXAMPLES / "revenue-proposed.sql")])
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


@pytest.mark.parametrize(
    "sql,exit_code,status",
    [
        (BASELINE.replace("SUM(i.line_minor)", "SUM(i.line_minor + 0)"), 2, "review_required"),
        ("SELECT load_extension('/tmp/no')", 3, "unsupported"),
    ],
)
def test_cli_review_required_and_unsupported_are_nonzero(
    services, tmp_path, capsys, sql, exit_code, status
):
    store, _, _reviews = services
    path = tmp_path / "candidate.sql"
    path.write_text(sql)
    with pytest.raises(SystemExit) as error:
        main(["--db", str(store.path), "query-review", "revenue", str(path)])
    assert error.value.code == exit_code
    assert json.loads(capsys.readouterr().out)["status"] == status
