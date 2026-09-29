"""Local, versioned semantic decisions. Human confirmations are CLI-only.

Receipts, rules and events are scoped to the metadata database and model.
Owner strings are attribution, not authenticated identities or tamper-proof signatures.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from lakecontext.models import utcnow
from lakecontext.revenue import (
    STRATEGIES,
    RevenueModel,
    RevenueRequest,
    compile_plan,
    dependencies,
    scope,
    supported,
    witness,
)
from lakecontext.witness import fingerprint


def init_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS semantic_models (model_id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS semantic_rules (
            rule_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, scope_id TEXT NOT NULL,
            revision INTEGER NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS semantic_one_active_rule
        ON semantic_rules(scope_id) WHERE status='active';
        CREATE TABLE IF NOT EXISTS semantic_questions (
            question_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, scope_id TEXT NOT NULL,
            status TEXT NOT NULL, body TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_conflicts (
            conflict_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, status TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_events (
            event_id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, kind TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS protected_queries (
            query_id TEXT PRIMARY KEY, body TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS query_reviews (
            review_id TEXT PRIMARY KEY, status TEXT NOT NULL, body TEXT NOT NULL
        );
    """)


def event(db, kind, **body):
    db.execute(
        "INSERT INTO semantic_events(occurred_at,kind,body) VALUES(?,?,?)",
        (utcnow(), kind, json.dumps(body)),
    )


def invalidate_rules(db, graph):
    models = {
        key: RevenueModel.model_validate_json(body)
        for key, body in db.execute("SELECT model_id,body FROM semantic_models")
    }
    current = {}
    for key, model in models.items():
        try:
            current[key] = dependencies(model, graph)
        except ValueError:
            current[key] = None
    for rule_id, model_id, body in db.execute(
        "SELECT rule_id,model_id,body FROM semantic_rules WHERE status='active'"
    ).fetchall():
        rule = json.loads(body)
        if current.get(model_id) != rule["dependencies"]:
            db.execute("UPDATE semantic_rules SET status='stale' WHERE rule_id=?", (rule_id,))
            event(
                db,
                "rule_invalidated",
                rule_id=rule_id,
                reason="Relevant schema, model, or metric-description dependency changed",
            )
    for question_id, model_id, body in db.execute(
        "SELECT question_id,model_id,body FROM semantic_questions WHERE status IN ('pending','conflict')"
    ).fetchall():
        if current.get(model_id) != json.loads(body)["dependencies"]:
            db.execute(
                "UPDATE semantic_questions SET status='stale' WHERE question_id=?", (question_id,)
            )
    for conflict_id, body in db.execute(
        "SELECT conflict_id,body FROM semantic_conflicts WHERE status='open'"
    ).fetchall():
        conflict = json.loads(body)
        if current.get(conflict["model_id"]) != conflict["dependencies"]:
            db.execute(
                "UPDATE semantic_conflicts SET status='stale' WHERE conflict_id=?", (conflict_id,)
            )
            event(
                db,
                "conflict_invalidated",
                conflict_id=conflict_id,
                reason="Dependencies changed; ask again",
            )


def _attribution(owner, evidence):
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
        raise ValueError("A named owner is required (maximum 200 characters)")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 4000:
        raise ValueError("Supporting evidence is required (maximum 4000 characters)")


class SemanticService:
    def __init__(self, store, max_age_hours=24):
        if not 0 < max_age_hours <= 8760:
            raise ValueError("Invalid metadata lifetime")
        self.store, self.max_age_hours = store, max_age_hours

    def _graph(self, db):
        graph = self.store._read(db)
        age = (datetime.now(UTC) - datetime.fromisoformat(graph["captured_at"])).total_seconds()
        if age > self.max_age_hours * 3600 or age < -300:
            raise ValueError(
                "Metadata snapshot expired or timestamp is in the future; refresh metadata"
            )
        return graph

    def _model(self, db, model_id, graph):
        row = db.execute(
            "SELECT body FROM semantic_models WHERE model_id=?", (model_id,)
        ).fetchone()
        if not row:
            raise ValueError("No configured revenue model with that ID")
        model = RevenueModel.model_validate_json(row[0])
        return model, dependencies(model, graph)

    def register(self, model, owner, evidence):
        _attribution(owner, evidence)
        model = RevenueModel.model_validate(model)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            graph = self._graph(db)
            dep = dependencies(model, graph)
            previous = db.execute(
                "SELECT body FROM semantic_models WHERE model_id=?", (model.model_id,)
            ).fetchone()
            body = model.model_dump_json()
            if not previous or previous[0] != body:
                db.execute(
                    "INSERT OR REPLACE INTO semantic_models VALUES(?,?)", (model.model_id, body)
                )
                invalidate_rules(db, graph)
                event(
                    db, "model_registered", model=model.model_dump(), owner=owner, evidence=evidence
                )
        return {"status": "registered", "model_id": model.model_id, "dependencies": dep}

    @staticmethod
    def _active(db, scope_id):
        row = db.execute(
            "SELECT body FROM semantic_rules WHERE scope_id=? AND status='active'", (scope_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _conflict(db, scope_id):
        row = db.execute(
            "SELECT body FROM semantic_conflicts WHERE scope_id=? AND status='open'", (scope_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def plan(self, request, review_definition=False):
        request = RevenueRequest.model_validate(request)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            graph = self._graph(db)
            model, dep = self._model(db, request.model_id, graph)
            if reason := supported(model, request):
                return {"status": "unsupported", "reason": reason, "rule_reused": False}
            key = fingerprint(scope(model))
            if conflict := self._conflict(db, key):
                return {
                    "status": "conflict",
                    "conflict": conflict,
                    "message": "Resolve the conflicting definitions before generating SQL",
                }
            active = self._active(db, key)
            if active and active["dependencies"] == dep and not review_definition:
                compiled = compile_plan(model, request, active["strategy"])
                return {**compiled, "rule_reused": True, "rule": active}
            example = witness(model, request)
            question_id = uuid.uuid4().hex
            receipt = {
                "question_id": question_id,
                "request": request.model_dump(mode="json"),
                "scope": scope(model),
                "dependencies": dep,
                "example": example,
                "created_at": utcnow(),
                "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
            }
            db.execute(
                "INSERT INTO semantic_questions VALUES(?,?,?,?,?)",
                (question_id, model.model_id, key, "pending", json.dumps(receipt)),
            )
            event(
                db,
                "question_issued",
                question_id=question_id,
                scope=scope(model),
                existing_rule_id=active["rule_id"] if active else None,
            )
            return {
                "status": "needs_clarification",
                "question_id": question_id,
                "scope": scope(model),
                **example,
                "existing_rule": active,
                "rule_reused": False,
                "boundary": "Paid booked merchandise, net of line discounts, before refunds; excludes tax and shipping. Only the allocation choice is unresolved in this configured case.",
            }

    def _receipt(self, db, question_id, allowed=("pending",)):
        row = db.execute(
            "SELECT status,body FROM semantic_questions WHERE question_id=?", (question_id,)
        ).fetchone()
        if not row or row[0] not in allowed:
            raise ValueError("Question is unknown, already answered, or stale")
        receipt = json.loads(row[1])
        if datetime.fromisoformat(receipt["expires_at"]) < datetime.now(UTC):
            raise ValueError("Question expired; request a new example")
        graph = self._graph(db)
        model, dep = self._model(db, receipt["scope"]["model_id"], graph)
        if dep != receipt["dependencies"]:
            raise ValueError("Question dependencies changed; request a new example")
        return receipt, model

    def _save_rule(self, db, model, receipt, strategy, owner, evidence, previous=None):
        key = fingerprint(scope(model))
        revision = db.execute(
            "SELECT COALESCE(MAX(revision),0)+1 FROM semantic_rules WHERE scope_id=?", (key,)
        ).fetchone()[0]
        if previous:
            db.execute(
                "UPDATE semantic_rules SET status='superseded' WHERE rule_id=?",
                (previous["rule_id"],),
            )
        rule = {
            "rule_id": uuid.uuid4().hex,
            "revision": revision,
            "scope": scope(model),
            "strategy": strategy,
            "owner": owner,
            "evidence": evidence,
            "confirmed_at": utcnow(),
            "dependencies": receipt["dependencies"],
            "question_id": receipt["question_id"],
            "supersedes": previous["rule_id"] if previous else None,
            "applicable_groupings": [["category"], ["category", "region"]],
            "date_scope": "Any half-open booked-date interval under this definition version",
            "rounding": "Residual minor units to categories in binary lexical order",
            "additive_across_categories": strategy != "full_credit",
        }
        db.execute(
            "INSERT INTO semantic_rules VALUES(?,?,?,?,?,?)",
            (rule["rule_id"], model.model_id, key, revision, "active", json.dumps(rule)),
        )
        event(db, "rule_confirmed", **rule)
        return rule

    def confirm(self, question_id, strategy, owner, evidence):
        _attribution(owner, evidence)
        if strategy not in {*STRATEGIES, "none"}:
            raise ValueError("Choose a displayed interpretation or 'none'")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt, model = self._receipt(db, question_id)
            if strategy == "none":
                key = fingerprint(scope(model))
                active = self._active(db, key)
                if active:
                    db.execute(
                        "UPDATE semantic_rules SET status='stale' WHERE rule_id=?",
                        (active["rule_id"],),
                    )
                    event(
                        db,
                        "rule_invalidated",
                        rule_id=active["rule_id"],
                        owner=owner,
                        reason="Owner declined all offered interpretations during review",
                    )
                db.execute(
                    "UPDATE semantic_questions SET status='stale' WHERE scope_id=? AND status IN ('pending','conflict')",
                    (key,),
                )
                db.execute(
                    "UPDATE semantic_conflicts SET status='stale' WHERE scope_id=? AND status='open'",
                    (key,),
                )
                db.execute(
                    "UPDATE semantic_questions SET status='declined' WHERE question_id=?",
                    (question_id,),
                )
                event(
                    db,
                    "interpretations_declined",
                    question_id=question_id,
                    owner=owner,
                    evidence=evidence,
                )
                return {
                    "status": "unsupported",
                    "message": "No rule saved; a new interpretation must be modeled",
                }
            key = fingerprint(scope(model))
            active = self._active(db, key)
            if existing := self._conflict(db, key):
                return {"status": "conflict", "conflict": existing}
            if active and active["strategy"] != strategy:
                conflict = {
                    "conflict_id": uuid.uuid4().hex,
                    "model_id": model.model_id,
                    "dependencies": receipt["dependencies"],
                    "question_id": question_id,
                    "existing_rule": active,
                    "proposed_strategy": strategy,
                    "proposed_owner": owner,
                    "proposed_evidence": evidence,
                    "created_at": utcnow(),
                }
                db.execute(
                    "INSERT INTO semantic_conflicts VALUES(?,?,?,?)",
                    (conflict["conflict_id"], key, "open", json.dumps(conflict)),
                )
                db.execute(
                    "UPDATE semantic_questions SET status='conflict' WHERE question_id=?",
                    (question_id,),
                )
                event(db, "definition_conflict", **conflict)
                return {
                    "status": "conflict",
                    "conflict": conflict,
                    "message": "Reuse blocked until explicit resolution",
                }
            rule = active or self._save_rule(db, model, receipt, strategy, owner, evidence)
            db.execute(
                "UPDATE semantic_questions SET status='confirmed' WHERE question_id=?",
                (question_id,),
            )
            if active:
                event(
                    db,
                    "rule_reaffirmed",
                    rule_id=active["rule_id"],
                    question_id=question_id,
                    owner=owner,
                    evidence=evidence,
                )
            return {"status": "confirmed", "rule": rule}

    def resolve(self, conflict_id, strategy, owner, evidence):
        _attribution(owner, evidence)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM semantic_conflicts WHERE conflict_id=? AND status='open'",
                (conflict_id,),
            ).fetchone()
            if not row:
                raise ValueError("Conflict is unknown or no longer open")
            conflict = json.loads(row[0])
            receipt, model = self._receipt(db, conflict["question_id"], allowed=("conflict",))
            if strategy not in {
                conflict["existing_rule"]["strategy"],
                conflict["proposed_strategy"],
            }:
                raise ValueError("Resolve to one of the conflicting interpretations")
            active = self._active(db, fingerprint(scope(model)))
            if not active or active["rule_id"] != conflict["existing_rule"]["rule_id"]:
                raise ValueError("Active rule changed; request a new review")
            rule = self._save_rule(db, model, receipt, strategy, owner, evidence, active)
            db.execute(
                "UPDATE semantic_conflicts SET status='resolved' WHERE conflict_id=?",
                (conflict_id,),
            )
            db.execute(
                "UPDATE semantic_questions SET status='confirmed' WHERE question_id=?",
                (receipt["question_id"],),
            )
            event(
                db,
                "conflict_resolved",
                conflict_id=conflict_id,
                rule_id=rule["rule_id"],
                owner=owner,
                evidence=evidence,
            )
            return {"status": "resolved", "rule": rule}

    def invalidate(self, rule_id, owner, evidence):
        _attribution(owner, evidence)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            scope_row = db.execute(
                "SELECT scope_id FROM semantic_rules WHERE rule_id=? AND status='active'",
                (rule_id,),
            ).fetchone()
            row = db.execute(
                "UPDATE semantic_rules SET status='stale' WHERE rule_id=? AND status='active'",
                (rule_id,),
            )
            if not row.rowcount:
                raise ValueError("No active rule with that ID")
            db.execute(
                "UPDATE semantic_questions SET status='stale' WHERE scope_id=? AND status IN ('pending','conflict')",
                (scope_row[0],),
            )
            db.execute(
                "UPDATE semantic_conflicts SET status='stale' WHERE scope_id=? AND status='open'",
                (scope_row[0],),
            )
            event(db, "rule_invalidated", rule_id=rule_id, owner=owner, reason=evidence)
        return {"status": "stale", "rule_id": rule_id}

    def history(self):
        with self.store.connection() as db:
            return {
                "rules": [
                    dict(json.loads(body), status=status)
                    for status, body in db.execute(
                        "SELECT status,body FROM semantic_rules ORDER BY rowid"
                    )
                ],
                "conflicts": [
                    dict(json.loads(body), status=status)
                    for status, body in db.execute(
                        "SELECT status,body FROM semantic_conflicts ORDER BY rowid"
                    )
                ],
                "events": [
                    {"event_id": eid, "occurred_at": at, "kind": kind, "details": json.loads(body)}
                    for eid, at, kind, body in db.execute(
                        "SELECT * FROM semantic_events ORDER BY event_id"
                    )
                ],
            }
