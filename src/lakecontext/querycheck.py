"""Bounded, local SQL change checks against a confirmed revenue definition.

Only generated fixtures are executed. A passing suite is not an equivalence proof.
The syntax allowlist and SQLite authorizer are intentionally conservative.
"""

import copy
import hashlib
import json
import random
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from typing import Literal

import sqlglot
from pydantic import Field
from sqlglot import exp

from lakecontext.models import Model
from lakecontext.revenue import (
    RevenueModel,
    RevenueRequest,
    evaluate,
    expected,
    queries,
    supported,
    synthetic_fixture,
)

MAX_SQL = 16000
MAX_NODES = 700
MAX_STEPS = 200000
ALLOWED_NODES = frozenset(
    [
        "Select",
        "Alias",
        "And",
        "Or",
        "Not",
        "CTE",
        "Column",
        "EQ",
        "NEQ",
        "GT",
        "GTE",
        "LT",
        "LTE",
        "From",
        "Group",
        "Identifier",
        "Join",
        "Literal",
        "Order",
        "Ordered",
        "Table",
        "TableAlias",
        "Where",
        "With",
        "Sum",
        "Count",
        "Add",
        "Sub",
        "Mul",
        "IntDiv",
        "Mod",
        "Paren",
        "Case",
        "If",
        "RowNumber",
        "Star",
        "Window",
        "Cast",
        "DataType",
        "Distinct",
        "Is",
        "Null",
        "Boolean",
        "Neg",
        "Having",
        "In",
        "Between",
        "Subquery",
    ]
)


class UnsupportedSQL(ValueError):
    pass


def checked_tree(sql, model):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL:
        raise UnsupportedSQL(f"Provide one nonempty SELECT of at most {MAX_SQL} characters")
    try:
        statements = sqlglot.parse(sql, read="databricks")
    except (sqlglot.errors.SqlglotError, RecursionError) as exc:
        raise UnsupportedSQL("SQL could not be parsed in the supported Databricks dialect") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise UnsupportedSQL("Only a single SELECT is supported; no writes or multiple statements")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > MAX_NODES:
        raise UnsupportedSQL("Query exceeds the supported AST size")
    unknown = {type(n).__name__ for n in nodes} - ALLOWED_NODES
    if unknown:
        raise UnsupportedSQL("Unsupported SQL syntax: " + ", ".join(sorted(unknown)))
    ctes = [cte.alias.casefold() for cte in tree.find_all(exp.CTE)]
    if len(ctes) != len(set(ctes)) or len(ctes) > 6:
        raise UnsupportedSQL("CTE names must be unique; at most six CTEs are supported")
    if any(w.args.get("recursive") for w in tree.find_all(exp.With)):
        raise UnsupportedSQL("Recursive CTEs are not supported")
    if len(list(tree.find_all(exp.Join))) > 6:
        raise UnsupportedSQL("At most six joins are supported")
    for join in tree.find_all(exp.Join):
        if (
            join.args.get("side", "").upper() not in {"", "LEFT"}
            or join.args.get("kind", "").upper() not in {"", "INNER"}
            or not join.args.get("on")
            or join.args.get("using")
        ):
            raise UnsupportedSQL("Only INNER/LEFT JOIN with explicit ON is supported")
    for cast in tree.find_all(exp.Cast):
        if (
            cast.args["to"].this != exp.DataType.Type.DATE
            or not isinstance(cast.this, exp.Literal)
            or not cast.this.is_string
        ):
            raise UnsupportedSQL("Only literal DATE casts are supported")
        try:
            date.fromisoformat(cast.this.this)
        except ValueError as exc:
            raise UnsupportedSQL("Invalid date literal") from exc
    for literal in tree.find_all(exp.Literal):
        if not literal.is_string:
            digits = literal.this.lstrip("0") or "0"
            if not digits.isdigit() or len(digits) > 13 or int(digits) > 10**12:
                raise UnsupportedSQL("Only bounded integer numeric literals are supported")
    physical = {model.orders.casefold(), model.items.casefold()}
    for table in tree.find_all(exp.Table):
        name = ".".join(part.name for part in table.parts).casefold()
        if name not in physical and not (len(table.parts) == 1 and name in ctes):
            raise UnsupportedSQL("Only mapped order/item tables and local CTEs are allowed")
        if name in {"synthetic_orders", "synthetic_items"}:
            raise UnsupportedSQL("Reserved fixture table name")
    if set(ctes) & {"synthetic_orders", "synthetic_items"}:
        raise UnsupportedSQL("Reserved CTE name")
    return tree


def sql_hash(sql, model):
    tree = checked_tree(sql, model)
    for node in tree.walk():
        node.comments = None
    return hashlib.sha256(tree.sql(dialect="databricks").encode()).hexdigest()


def suite_v1(model, request):
    """Stable development fixtures, not an expert-reviewed or held-out benchmark."""
    if reason := supported(model, request):
        raise ValueError(reason)
    day = request.start_date or (
        request.end_date - timedelta(days=1) if request.end_date else date(2026, 1, 15)
    )
    cases = []
    for name, amounts in (
        ("asymmetric", (90000, 10000)),
        ("balanced", (50000, 50000)),
        ("rounding", (1, 1, 2)),
        ("zero", (0, 0)),
        ("single_category", (17,)),
        ("large_integer", (10**12 - 2, 1, 1)),
    ):
        cases.append({"name": name, "fixture": synthetic_fixture(amounts, day.isoformat(), model)})
    base = copy.deepcopy(cases[0]["fixture"])
    repeated = copy.deepcopy(base)
    repeated["items"][0]["line_minor"] -= 10000
    repeated["items"].append({"id": 3, "order_id": 1, "category": "Books", "line_minor": 10000})
    cases.append({"name": "repeated_category_lines", "fixture": repeated})
    for name, key, value in (
        ("unpaid", "status", "not-" + model.paid_status),
        ("refunded", "status", "refunded" if model.paid_status != "refunded" else "not-refunded"),
        ("other_currency", "currency", "USD"),
        ("null_region", "region", None),
        ("other_region", "region", "South"),
    ):
        fixture = copy.deepcopy(base)
        fixture["orders"][0][key] = value
        cases.append({"name": name, "fixture": fixture})
    if request.start_date and request.start_date > date.min:
        fixture = copy.deepcopy(base)
        fixture["orders"][0]["booked_on"] = (request.start_date - timedelta(days=1)).isoformat()
        cases.append({"name": "before_start", "fixture": fixture})
    if request.end_date:
        fixture = copy.deepcopy(base)
        fixture["orders"][0]["booked_on"] = request.end_date.isoformat()
        cases.append({"name": "exclusive_end", "fixture": fixture})
    doubled = copy.deepcopy(base)
    doubled["orders"].append({**base["orders"][0], "id": 2})
    doubled["items"].extend({**item, "id": item["id"] + 2, "order_id": 2} for item in base["items"])
    cases.append({"name": "two_equal_orders", "fixture": doubled})
    cases.append({"name": "empty", "fixture": {"orders": [], "items": []}})
    rng = random.Random(61723)
    for index in range(12):
        fixture = {"orders": [], "items": []}
        for oid in range(1, 5):
            amount = 0
            for _ in range(rng.randint(1, 5)):
                value = rng.randrange(10000)
                amount += value
                fixture["items"].append(
                    {
                        "id": len(fixture["items"]) + 1,
                        "order_id": oid,
                        "category": rng.choice(["Books", "Games", "Music", "Kid's books"]),
                        "line_minor": value,
                    }
                )
            fixture["orders"].append(
                {
                    "id": oid,
                    "net_minor": amount,
                    "status": rng.choice(
                        [model.paid_status, model.paid_status, "not-" + model.paid_status]
                    ),
                    "currency": rng.choice(["INR", "INR", "USD"]),
                    "booked_on": day.isoformat(),
                    "region": rng.choice(["North", "South", None]),
                }
            )
        cases.append({"name": f"generated_{index:02}", "fixture": fixture})
    return cases


def run_query(sql, fixture, model, request, strategy):
    tree = checked_tree(sql, model)
    mapping = {
        model.orders.casefold(): "synthetic_orders",
        model.items.casefold(): "synthetic_items",
    }
    for table in tree.find_all(exp.Table):
        name = ".".join(part.name for part in table.parts).casefold()
        if name in mapping:
            table.set("this", exp.to_identifier(mapping[name], quoted=True))
            table.set("db", None)
            table.set("catalog", None)
    local = tree.sql(dialect="sqlite") + ";"
    # This DDL/DML is generated by us from bounded synthetic fixtures, never from user SQL.
    reference = evaluate(fixture, model, request, strategy)
    if reference["status"] != "passed":
        raise ValueError("Internal fixture failed the reference plan/preflight")
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(reference["fixture_sql"])
        db.execute("PRAGMA query_only = ON")

        def authorize(action, arg1, arg2, _database, _trigger):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and arg1 in mapping.values():
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in {
                "sum",
                "count",
                "row_number",
                "date",
            }:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        steps = 0

        def progress():
            nonlocal steps
            steps += 1000
            return int(steps > MAX_STEPS)

        db.set_authorizer(authorize)
        db.set_progress_handler(progress, 1000)
        try:
            cursor = db.execute(local)
            names = [col[0] for col in cursor.description]
            if len(names) != len(set(names)) or set(names) != {*request.group_by, "revenue_minor"}:
                raise UnsupportedSQL(
                    "Output must contain exactly the requested dimensions and revenue_minor"
                )
            rows = cursor.fetchmany(513)
            if len(rows) > 512:
                raise UnsupportedSQL("Result exceeded the local row limit")
            actual = [dict(zip(names, row, strict=True)) for row in rows]
            if any(type(row["revenue_minor"]) is not int for row in actual):
                raise UnsupportedSQL("Only integer, non-NULL revenue_minor results are supported")
            actual.sort(key=lambda row: json.dumps([row[d] for d in request.group_by]))
        except sqlite3.Error as exc:
            raise UnsupportedSQL(
                "Query could not run in the bounded SQLite validation runtime: " + str(exc)
            ) from exc
    return {
        "actual": actual,
        "expected": reference["expected"],
        "query_sqlite": local,
        "fixture_sql": reference["fixture_sql"],
        "preflight_sqlite": reference["preflight_sqlite"],
    }


def compare(sql, model, request, strategy, cases=None):
    try:
        checked_tree(sql, model)
        results = []
        mismatches = []
        for case in cases or suite_v1(model, request):
            replay = run_query(sql, case["fixture"], model, request, strategy)
            matched = replay["actual"] == replay["expected"]
            results.append({"name": case["name"], "matched": matched})
            if not matched:
                mismatches.append({**case, **replay})
        witness = (
            min(mismatches, key=lambda c: len(c["fixture"]["orders"]) + len(c["fixture"]["items"]))
            if mismatches
            else None
        )
        return {
            "status": "counterexample" if witness else "no_counterexample",
            "case_count": len(results),
            "mismatch_count": len(mismatches),
            "cases": results,
            "witness": witness,
            "limitation": "Finite synthetic tests in SQLite, not SQL equivalence or Databricks runtime verification",
        }
    except UnsupportedSQL as exc:
        return {"status": "unsupported", "reason": str(exc)}


class RegressionBundle(Model):
    bundle_version: Literal[1] = 1
    suite_version: Literal[1] = 1
    query_id: str = Field(min_length=1, max_length=100)
    query_revision: int = Field(ge=1)
    model: RevenueModel
    request: RevenueRequest
    strategy: Literal["item_value", "equal_categories", "full_credit"]
    approved_sql: str = Field(min_length=1, max_length=MAX_SQL)
    rule_id: str
    dependencies: str
    owner: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=1, max_length=4000)
    approved_at: str
    consumers: list[str]
    cases: list[dict] = Field(max_length=64)


def regression_bundle(query, model):
    request = RevenueRequest.model_validate(query["request"])
    cases = [
        {**case, "expected": expected(case["fixture"], model, request, query["strategy"])}
        for case in suite_v1(model, request)
    ]
    return RegressionBundle(
        **{
            key: query[key]
            for key in (
                "query_id",
                "query_revision",
                "strategy",
                "approved_sql",
                "rule_id",
                "dependencies",
                "owner",
                "evidence",
                "approved_at",
                "consumers",
            )
        },
        model=model,
        request=request,
        cases=cases,
    ).model_dump(mode="json")


def check_regression(bundle, sql):
    bundle = RegressionBundle.model_validate(bundle)
    cases = [
        {
            **case,
            "expected": expected(case["fixture"], bundle.model, bundle.request, bundle.strategy),
        }
        for case in suite_v1(bundle.model, bundle.request)
    ]
    if bundle.cases != cases:
        raise ValueError("Regression cases or expected results differ from the versioned suite")
    baseline = compare(bundle.approved_sql, bundle.model, bundle.request, bundle.strategy, cases)
    if baseline["status"] != "no_counterexample":
        raise ValueError("Approved regression baseline no longer passes its definition")
    result = compare(sql, bundle.model, bundle.request, bundle.strategy, cases)
    status = result["status"]
    if status == "counterexample":
        status = "blocked"
    elif status == "no_counterexample":
        status = (
            "passed"
            if sql_hash(sql, bundle.model) == sql_hash(bundle.approved_sql, bundle.model)
            else "review_required"
        )
    return {
        "status": status,
        "query_id": bundle.query_id,
        "query_revision": bundle.query_revision,
        "comparison": result,
        "approval": {"owner": bundle.owner, "evidence": bundle.evidence, "rule_id": bundle.rule_id},
        "boundary": "Offline check against a trusted exported approval; no live freshness, permissions or schema verification. Changed SQL requires owner review even when these fixtures pass.",
    }


def recommended_sql(model, request, strategy):
    return queries(model, request, strategy)
