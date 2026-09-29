import json
from datetime import UTC, datetime, timedelta

import pytest

from lakecontext.models import Column, Term
from lakecontext.revenue import RevenueModel, RevenueRequest, demo_snapshot
from lakecontext.semantics import SemanticService
from lakecontext.store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "semantic.db")
    store.ingest(demo_snapshot())
    service = SemanticService(store)
    service.register(RevenueModel(), "test owner", "Synthetic mapped schema")
    return service


def confirm(service, strategy="item_value"):
    question = service.plan(RevenueRequest())
    return service.confirm(
        question["question_id"], strategy, "metric owner", "Test-only approved definition"
    )["rule"]


def test_first_query_asks_then_second_query_reuses_persisted_definition(service):
    question = service.plan(RevenueRequest())
    assert question["status"] == "needs_clarification"
    assert "sql" not in question
    rule = service.confirm(
        question["question_id"], "item_value", "finance owner", "Reviewed synthetic example"
    )["rule"]
    restarted = SemanticService(Store(service.store.path))
    followup = restarted.plan(
        RevenueRequest(
            group_by=["category", "region"], start_date="2026-01-01", end_date="2026-02-01"
        )
    )
    assert followup["status"] == "compiled"
    assert followup["rule_reused"] is True
    assert followup["rule"]["rule_id"] == rule["rule_id"]
    assert followup["rule"]["owner"] == "finance owner"
    assert "question_id" not in followup
    with pytest.raises(ValueError, match="answered"):
        restarted.confirm(question["question_id"], "full_credit", "other", "Late reply")


def test_conflicting_answer_blocks_sql_until_versioned_resolution(service):
    original = confirm(service)
    question = service.plan(RevenueRequest(), review_definition=True)
    conflict = service.confirm(
        question["question_id"], "full_credit", "marketing", "Attribution request"
    )
    blocked = service.plan(RevenueRequest())
    assert blocked["status"] == "conflict"
    assert "sql" not in blocked
    cid = conflict["conflict"]["conflict_id"]
    with pytest.raises(ValueError, match="conflicting"):
        service.resolve(cid, "equal_categories", "owner", "Unproposed option")
    resolved = service.resolve(
        cid, "item_value", "metric owner", "Attribution is a separate metric"
    )
    assert resolved["rule"]["revision"] == 2
    assert resolved["rule"]["supersedes"] == original["rule_id"]
    assert service.plan(RevenueRequest())["rule_reused"]
    history = service.history()
    assert [r["status"] for r in history["rules"]] == ["superseded", "active"]
    assert history["conflicts"][0]["status"] == "resolved"
    assert any(e["kind"] == "conflict_resolved" for e in history["events"])


def test_none_retires_old_rule_and_outstanding_receipts(service):
    confirm(service)
    first = service.plan(RevenueRequest(), review_definition=True)
    second = service.plan(RevenueRequest(), review_definition=True)
    assert (
        service.confirm(first["question_id"], "none", "owner", "Need refund allocation")["status"]
        == "unsupported"
    )
    assert service.history()["rules"][0]["status"] == "stale"
    with pytest.raises(ValueError, match="stale"):
        service.confirm(second["question_id"], "item_value", "owner", "Outdated answer")
    assert service.plan(RevenueRequest())["status"] == "needs_clarification"


@pytest.mark.parametrize("owner,evidence", [("", "reason"), ("owner", ""), (" ", " ")])
def test_missing_attribution_never_confirms(service, owner, evidence):
    question = service.plan(RevenueRequest())
    with pytest.raises(ValueError, match="required"):
        service.confirm(question["question_id"], "item_value", owner, evidence)
    assert service.history()["rules"] == []


def test_identical_refresh_or_irrelevant_column_preserves_rule(service):
    original = confirm(service)
    snapshot = demo_snapshot()
    service.store.ingest(snapshot)
    assert service.plan(RevenueRequest())["rule"]["rule_id"] == original["rule_id"]
    snapshot.tables[0].columns.append(Column(name="irrelevant", data_type="STRING"))
    service.store.ingest(snapshot)
    assert service.plan(RevenueRequest())["rule_reused"]


@pytest.mark.parametrize(
    "change", ["type", "column_description", "table_description", "term", "missing_column"]
)
def test_dependency_changes_invalidate_and_reverting_does_not_revive(service, change):
    confirm(service)
    pending = service.plan(RevenueRequest(), review_definition=True)
    snapshot = demo_snapshot()
    if change == "type":
        snapshot.tables[0].columns[0].data_type = "STRING"
    elif change == "column_description":
        snapshot.tables[0].columns[0].description = "Meaning changed"
    elif change == "table_description":
        snapshot.tables[0].description = "Different accounting basis"
    elif change == "missing_column":
        snapshot.tables[0].columns.pop()
    else:
        snapshot.terms = [
            Term(name="Revenue", definition="New interpretation", tables=[snapshot.tables[0].name])
        ]
    service.store.ingest(snapshot)
    assert service.history()["rules"][0]["status"] == "stale"
    service.store.ingest(demo_snapshot())
    assert service.plan(RevenueRequest())["status"] == "needs_clarification"
    with pytest.raises(ValueError, match="stale"):
        service.confirm(pending["question_id"], "item_value", "owner", "Old question")


def test_definition_version_change_requires_new_approval(service):
    confirm(service)
    service.register(
        RevenueModel(definition_version="booked-v2"), "owner", "Business definition changed"
    )
    assert service.plan(RevenueRequest())["status"] == "unsupported"
    assert (
        service.plan(RevenueRequest(definition_version="booked-v2"))["status"]
        == "needs_clarification"
    )


def test_explicit_business_change_invalidates_conflict_and_pending_questions(service):
    rule = confirm(service)
    first = service.plan(RevenueRequest(), review_definition=True)
    second = service.plan(RevenueRequest(), review_definition=True)
    conflict = service.confirm(first["question_id"], "full_credit", "owner", "Proposal")["conflict"]
    service.invalidate(rule["rule_id"], "owner", "Undetected business policy change")
    assert service.plan(RevenueRequest())["status"] == "needs_clarification"
    with pytest.raises(ValueError, match="no longer open"):
        service.resolve(conflict["conflict_id"], "item_value", "owner", "Old resolution")
    with pytest.raises(ValueError, match="stale"):
        service.confirm(second["question_id"], "item_value", "owner", "Old response")


@pytest.mark.parametrize("hours", [-25, 1])
def test_expired_or_future_metadata_prevents_reuse(service, hours):
    confirm(service)
    snapshot = demo_snapshot()
    snapshot.captured_at = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
    service.store.ingest(snapshot)
    with pytest.raises(ValueError, match="expired|future"):
        service.plan(RevenueRequest())


def test_expired_question_cannot_be_confirmed(service):
    question = service.plan(RevenueRequest())
    with service.store.connection() as db:
        body = json.loads(
            db.execute(
                "SELECT body FROM semantic_questions WHERE question_id=?",
                (question["question_id"],),
            ).fetchone()[0]
        )
        body["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        db.execute(
            "UPDATE semantic_questions SET body=? WHERE question_id=?",
            (json.dumps(body), question["question_id"]),
        )
    with pytest.raises(ValueError, match="expired"):
        service.confirm(question["question_id"], "item_value", "owner", "Late reply")


@pytest.mark.parametrize(
    "updates",
    [
        {"metric": "recognized_revenue"},
        {"currency": "USD"},
        {"group_by": ["customer"]},
        {"basis": "after_refunds"},
    ],
)
def test_existing_rule_does_not_escape_its_scope(service, updates):
    confirm(service)
    result = service.plan(RevenueRequest(**updates))
    assert result["status"] == "unsupported"
    assert result["rule_reused"] is False
    assert "sql" not in result
