"""Local owner-reviewed SQL changes, impact registry and portable regressions."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from lakecontext.models import utcnow
from lakecontext.querycheck import (
    compare,
    recommended_sql,
    regression_bundle,
    sql_hash,
)
from lakecontext.revenue import RevenueRequest, scope, supported
from lakecontext.semantics import SemanticService, _attribution, event
from lakecontext.witness import fingerprint


class ChangeReviewService:
    def __init__(self, store, max_age_hours=24):
        self.store = store
        self.semantics = SemanticService(store, max_age_hours)

    def _definition(self, db, request):
        graph = self.semantics._graph(db)
        model, dep = self.semantics._model(db, request.model_id, graph)
        if reason := supported(model, request):
            raise ValueError(reason)
        key = fingerprint(scope(model))
        if self.semantics._conflict(db, key):
            raise ValueError("Definition conflict must be resolved first")
        rule = self.semantics._active(db, key)
        if not rule or rule["dependencies"] != dep:
            raise ValueError("A current confirmed semantic rule is required")
        return model, rule, dep

    @staticmethod
    def _query(db, query_id):
        row = db.execute(
            "SELECT body FROM protected_queries WHERE query_id=?", (query_id,)
        ).fetchone()
        if not row:
            raise ValueError("Unknown registered query")
        return json.loads(row[0])

    def _ready(self, db, query):
        request = RevenueRequest.model_validate(query["request"])
        model, rule, dep = self._definition(db, request)
        if query["dependencies"] != dep or query["rule_id"] != rule["rule_id"]:
            raise ValueError(
                "Query approval is stale; explicitly re-register against the current definition"
            )
        return model, request

    def register(self, query_id, request, sql, owner, evidence, consumers=None, replace=False):
        _attribution(owner, evidence)
        if not isinstance(query_id, str) or not query_id.strip() or len(query_id) > 100:
            raise ValueError("Provide a query ID of 1–100 characters")
        consumers = consumers or []
        if len(consumers) > 30 or any(
            not isinstance(c, str) or not c.strip() or len(c) > 200 for c in consumers
        ):
            raise ValueError("At most 30 nonempty consumer labels of 200 characters each")
        request = RevenueRequest.model_validate(request)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            model, rule, dep = self._definition(db, request)
            previous = db.execute(
                "SELECT body FROM protected_queries WHERE query_id=?", (query_id,)
            ).fetchone()
            if previous and not replace:
                raise ValueError(
                    "Query already registered; use explicit --replace for a new approval"
                )
            result = compare(sql, model, request, rule["strategy"])
            if result["status"] != "no_counterexample":
                return {"status": "registration_refused", "comparison": result}
            query = {
                "query_id": query_id,
                "query_revision": json.loads(previous[0])["query_revision"] + 1 if previous else 1,
                "request": request.model_dump(mode="json"),
                "model": model.model_dump(),
                "strategy": rule["strategy"],
                "rule_id": rule["rule_id"],
                "dependencies": dep,
                "approved_sql": sql,
                "approved_sql_hash": sql_hash(sql, model),
                "owner": owner,
                "evidence": evidence,
                "approved_at": utcnow(),
                "consumers": sorted(set(consumers)),
            }
            db.execute(
                "INSERT OR REPLACE INTO protected_queries VALUES(?,?)",
                (query_id, json.dumps(query)),
            )
            event(db, "query_registered", **query)
            return {
                "status": "registered",
                "query": query,
                "comparison": result,
                "boundary": "Explicit local owner attribution, not authenticated identity or proof of SQL correctness",
            }

    def review(self, query_id, candidate_sql):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            query = self._query(db, query_id)
            try:
                model, request = self._ready(db, query)
            except ValueError as exc:
                return {
                    "status": "needs_reapproval",
                    "reason": str(exc),
                    "query_id": query_id,
                    "consumers": query["consumers"],
                }
            result = compare(candidate_sql, model, request, query["strategy"])
            if result["status"] == "unsupported":
                return {"status": "unsupported", "comparison": result, "query_id": query_id}
            # Baselines are also replayed, so a broken runtime or changed checker cannot silently pass.
            baseline = compare(query["approved_sql"], model, request, query["strategy"])
            if baseline["status"] != "no_counterexample":
                return {
                    "status": "needs_reapproval",
                    "reason": "Approved baseline failed the current suite",
                    "comparison": baseline,
                }
            same = sql_hash(candidate_sql, model) == query["approved_sql_hash"]
            if same:
                return {"status": "unchanged", "query_id": query_id, "comparison": result}
            review_id = uuid.uuid4().hex
            recommendation = recommended_sql(model, request, query["strategy"])
            receipt = {
                "review_id": review_id,
                "query_id": query_id,
                "query_revision": query["query_revision"],
                "rule_id": query["rule_id"],
                "dependencies": query["dependencies"],
                "candidate_sql": candidate_sql,
                "candidate_sql_hash": sql_hash(candidate_sql, model),
                "comparison": result,
                "recommendation": recommendation,
                "created_at": utcnow(),
                "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
            }
            db.execute(
                "INSERT INTO query_reviews VALUES(?,?,?)",
                (review_id, "pending", json.dumps(receipt)),
            )
            event(
                db,
                "query_review_requested",
                review_id=review_id,
                query_id=query_id,
                candidate_sql_hash=receipt["candidate_sql_hash"],
                result=result["status"],
            )
            return {
                "status": "blocked" if result["status"] == "counterexample" else "review_required",
                **receipt,
                "consumers": query["consumers"],
                "decision_question": "Should this query keep the approved metric definition? A different business meaning needs a separately confirmed definition, not an override of a failing check.",
                "allowed_decisions": ["keep_definition", "reject_change"]
                + (["approve_candidate"] if result["status"] == "no_counterexample" else []),
                "boundary": "Synthetic discrepancy, not measured production impact. Passing cases never automatically approve changed SQL.",
            }

    def decide(self, review_id, decision, owner, evidence):
        _attribution(owner, evidence)
        if decision not in {"keep_definition", "reject_change", "approve_candidate"}:
            raise ValueError("Unsupported review decision")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,body FROM query_reviews WHERE review_id=?", (review_id,)
            ).fetchone()
            if not row or row[0] != "pending":
                raise ValueError("Review is unknown or already decided")
            receipt = json.loads(row[1])
            if datetime.fromisoformat(receipt["expires_at"]) < datetime.now(UTC):
                raise ValueError("Review expired; check the current candidate again")
            query = self._query(db, receipt["query_id"])
            model, request = self._ready(db, query)
            if any(
                query[key] != receipt[key] for key in ("query_revision", "rule_id", "dependencies")
            ):
                raise ValueError("Query or definition changed after review; check again")
            if decision == "approve_candidate":
                sql = receipt["candidate_sql"]
            elif decision == "keep_definition":
                sql = recommended_sql(model, request, query["strategy"])["sql"]
            else:
                sql = query["approved_sql"]
            comparison = compare(sql, model, request, query["strategy"])
            if comparison["status"] != "no_counterexample":
                raise ValueError(
                    "Cannot approve SQL that fails the confirmed definition or supported scope"
                )
            if decision != "reject_change":
                query.update(
                    approved_sql=sql,
                    approved_sql_hash=sql_hash(sql, model),
                    query_revision=query["query_revision"] + 1,
                    owner=owner,
                    evidence=evidence,
                    approved_at=utcnow(),
                )
                db.execute(
                    "UPDATE protected_queries SET body=? WHERE query_id=?",
                    (json.dumps(query), query["query_id"]),
                )
            receipt["decision"] = {
                "decision": decision,
                "owner": owner,
                "evidence": evidence,
                "decided_at": utcnow(),
                "resulting_query_revision": query["query_revision"],
            }
            db.execute(
                "UPDATE query_reviews SET status='decided',body=? WHERE review_id=?",
                (json.dumps(receipt), review_id),
            )
            event(
                db, "query_review_decided", review_id=review_id, query=query, **receipt["decision"]
            )
            return {
                "status": "decided",
                "decision": receipt["decision"],
                "approved_sql": sql,
                "preflight_sql": recommended_sql(model, request, query["strategy"])[
                    "preflight_sql"
                ],
                "regression": regression_bundle(query, model),
            }

    def export(self, query_id):
        with self.store.connection() as db:
            query = self._query(db, query_id)
            model, _request = self._ready(db, query)
            return regression_bundle(query, model)

    def impacts(self, table=None):
        with self.store.connection() as db:
            entries = []
            for (body,) in db.execute("SELECT body FROM protected_queries ORDER BY query_id"):
                query = json.loads(body)
                model = query["model"]
                tables = [model["orders"], model["items"]]
                if table and table.casefold() not in {name.casefold() for name in tables}:
                    continue
                try:
                    self._ready(db, query)
                    status, reason = "current", None
                except ValueError as exc:
                    status, reason = "needs_reapproval", str(exc)
                entries.append(
                    {
                        "query_id": query["query_id"],
                        "query_revision": query["query_revision"],
                        "tables": tables,
                        "columns": {
                            model["orders"]: sorted(model["order_columns"].values()),
                            model["items"]: sorted(model["item_columns"].values()),
                        },
                        "consumers": query["consumers"],
                        "owner": query["owner"],
                        "rule_id": query["rule_id"],
                        "status": status,
                        "reason": reason,
                    }
                )
            return {
                "queries": entries,
                "boundary": "Only explicitly registered queries and consumer labels; not discovered warehouse-wide lineage or measured financial impact",
            }

    def history(self):
        with self.store.connection() as db:
            return {
                "reviews": [
                    dict(json.loads(body), review_status=status)
                    for status, body in db.execute(
                        "SELECT status,body FROM query_reviews ORDER BY rowid"
                    )
                ],
                "events": [
                    json.loads(body)
                    for (body,) in db.execute(
                        "SELECT body FROM semantic_events WHERE kind='query_review_decided' ORDER BY event_id"
                    )
                ],
            }
