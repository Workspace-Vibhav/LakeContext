"""One explicit business-ambiguity case: booked merchandise revenue by category.

This is a configured semantic model, not automatic discovery of business meanings.
All examples and SQL checks use generated data. Source rows are never accessed.
"""

import copy
import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from typing import Literal

import sqlglot
from pydantic import Field, model_validator
from sqlglot import exp

from lakecontext.models import Model, Snapshot
from lakecontext.witness import fingerprint, quoted

MAX_MINOR = 10**12
BASIS = "net_line_discounts_excluding_tax_shipping_before_refunds"
STRATEGIES = {
    "item_value": "Use each category's actual net item amounts.",
    "equal_categories": "Split each order equally across its distinct categories.",
    "full_credit": "Credit the full order amount to every category (overlapping attribution).",
}


class RevenueModel(Model):
    model_id: str = "commerce"
    metric: str = "booked_merchandise_revenue"
    definition_version: str = "booked-v1"
    currency: Literal["INR"] = "INR"
    basis: str = BASIS
    orders: str = "demo.commerce.orders"
    items: str = "demo.commerce.order_items"
    order_columns: dict[str, str] = Field(
        default_factory=lambda: {
            "id": "order_id",
            "net_minor": "net_minor",
            "status": "status",
            "currency": "currency",
            "booked_on": "booked_on",
            "region": "region",
        }
    )
    item_columns: dict[str, str] = Field(
        default_factory=lambda: {
            "id": "item_id",
            "order_id": "order_id",
            "category": "category",
            "line_minor": "line_minor",
        }
    )
    paid_status: str = "paid"

    @model_validator(mode="after")
    def validate_mapping(self):
        for mapping, roles in (
            (self.order_columns, {"id", "net_minor", "status", "currency", "booked_on", "region"}),
            (self.item_columns, {"id", "order_id", "category", "line_minor"}),
        ):
            if set(mapping) != roles or len(set(mapping.values())) != len(mapping):
                raise ValueError("Each model role needs a distinct column mapping")
        if self.orders == self.items or self.basis != BASIS:
            raise ValueError(
                "This prototype requires distinct order/item tables and its supported net-revenue basis"
            )
        if not all(
            (
                self.model_id.strip(),
                self.metric.strip(),
                self.definition_version.strip(),
                self.paid_status.strip(),
            )
        ):
            raise ValueError("Model identifiers and paid status must be nonempty")
        return self


class RevenueRequest(Model):
    model_id: str = "commerce"
    metric: str = "booked_merchandise_revenue"
    definition_version: str = "booked-v1"
    currency: str = "INR"
    basis: str = BASIS
    group_by: list[str] = Field(default_factory=lambda: ["category"])
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def valid_dates(self):
        if self.end_date == date.min:
            raise ValueError("end_date must leave room for a representable example date")
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValueError("Date range must be nonempty; end_date is exclusive")
        return self


def scope(model):
    return {
        key: getattr(model, key)
        for key in ("model_id", "metric", "definition_version", "currency", "basis")
    }


def supported(model, request):
    if any(getattr(request, key) != value for key, value in scope(model).items()):
        return "Metric, definition version, currency or accounting basis is outside the configured scope"
    if request.group_by not in (["category"], ["category", "region"]):
        return "Only category, or category plus region, grouping is supported"
    return None


def dependencies(model, graph):
    tables = {t["name"]: t for t in graph["tables"]}
    selected = []
    for name, mapping in ((model.orders, model.order_columns), (model.items, model.item_columns)):
        if name not in tables:
            raise ValueError(f"Missing semantic-model table: {name}")
        table = tables[name]
        columns = {c["name"]: c for c in table["columns"]}
        for role, col in mapping.items():
            if col not in columns:
                raise ValueError(f"Missing semantic-model column: {name}.{col}")
            expected = (
                "bigint"
                if role in {"id", "order_id", "net_minor", "line_minor"}
                else "date"
                if role == "booked_on"
                else "string"
            )
            if columns[col]["data_type"].casefold() != expected:
                raise ValueError(f"{name}.{col} requires {expected}")
        selected.append(
            {
                "name": name,
                "description": table["description"],
                "columns": [columns[c] for c in sorted(mapping.values())],
            }
        )
    terms = sorted(
        (t for t in graph["terms"] if {model.orders, model.items} & set(t["tables"])),
        key=lambda t: t["name"],
    )
    return fingerprint(
        {
            "source": graph["source"],
            "principal": graph["principal"],
            "model": model.model_dump(),
            "tables": selected,
            "terms": terms,
        }
    )


def demo_snapshot():
    model = RevenueModel()
    tables = []
    for name, mapping in ((model.orders, model.order_columns), (model.items, model.item_columns)):
        tables.append(
            {
                "name": name,
                "description": "Synthetic revenue-allocation model; no real business data.",
                "columns": [
                    {
                        "name": col,
                        "data_type": "BIGINT"
                        if role in {"id", "order_id", "net_minor", "line_minor"}
                        else "DATE"
                        if role == "booked_on"
                        else "STRING",
                    }
                    for role, col in mapping.items()
                ],
            }
        )
    return Snapshot.model_validate(
        {"source": "demo://revenue-ambiguity", "principal": "synthetic-demo", "tables": tables}
    )


def synthetic_fixture(amounts=(90000, 10000), booked_on="2026-01-15", model=None):
    model = model or RevenueModel()
    return {
        "orders": [
            {
                "id": 1,
                "net_minor": sum(amounts),
                "status": model.paid_status,
                "currency": model.currency,
                "booked_on": booked_on,
                "region": "North",
            }
        ],
        "items": [
            {"id": i + 1, "order_id": 1, "category": category, "line_minor": amount}
            for i, (category, amount) in enumerate(
                zip(("Books", "Games", "Music"), amounts, strict=False)
            )
        ],
    }


def eligible(order, model, request):
    return (
        order["status"] == model.paid_status
        and order["currency"] == model.currency
        and (
            request.start_date is None
            or (
                order["booked_on"] is not None
                and order["booked_on"] >= request.start_date.isoformat()
            )
        )
        and (
            request.end_date is None
            or (
                order["booked_on"] is not None and order["booked_on"] < request.end_date.isoformat()
            )
        )
    )


def expected(fixture, model, request, strategy):
    if strategy not in STRATEGIES:
        raise ValueError("Unsupported allocation strategy")
    totals = defaultdict(int)
    for order in fixture["orders"]:
        if not eligible(order, model, request):
            continue
        cats = defaultdict(int)
        for item in fixture["items"]:
            if item["order_id"] == order["id"]:
                cats[item["category"]] += item["line_minor"]
        for index, category in enumerate(sorted(cats)):
            if strategy == "item_value":
                value = cats[category]
            elif strategy == "full_credit":
                value = order["net_minor"]
            else:
                quotient, remainder = divmod(order["net_minor"], len(cats))
                value = quotient + (index < remainder)
            key = (category, order["region"]) if "region" in request.group_by else (category,)
            totals[key] += value
    return [
        dict(zip(request.group_by, key, strict=True), revenue_minor=value)
        for key, value in sorted(totals.items(), key=lambda item: json.dumps(item[0]))
    ]


def _qt(name):
    return ".".join("`" + part.replace("`", "``") + "`" for part in name.split("."))


def _qc(name):
    return "`" + name.replace("`", "``") + "`"


def _literal(value, dialect="databricks"):
    return exp.Literal.string(value).sql(dialect=dialect)


def queries(model, request, strategy):
    error = supported(model, request)
    if error or strategy not in STRATEGIES:
        raise ValueError(error or "Unsupported allocation strategy")
    o, i = model.order_columns, model.item_columns
    filters = [
        f"o.{_qc(o['status'])} = {_literal(model.paid_status)}",
        f"o.{_qc(o['currency'])} = {_literal(model.currency)}",
    ]
    if request.start_date:
        filters.append(f"o.{_qc(o['booked_on'])} >= DATE '{request.start_date.isoformat()}'")
    if request.end_date:
        filters.append(f"o.{_qc(o['booked_on'])} < DATE '{request.end_date.isoformat()}'")
    eligible_sql = (
        f"SELECT o.{_qc(o['id'])} AS order_id, o.{_qc(o['net_minor'])} AS net_minor, o.{_qc(o['region'])} AS region, o.{_qc(o['booked_on'])} AS booked_on FROM {_qt(model.orders)} o WHERE "
        + " AND ".join(filters)
    )
    categories = f"SELECT o.order_id, o.net_minor, o.region, i.{_qc(i['category'])} AS category, SUM(i.{_qc(i['line_minor'])}) AS item_minor FROM eligible_orders o JOIN {_qt(model.items)} i ON o.order_id = i.{_qc(i['order_id'])} GROUP BY o.order_id, o.net_minor, o.region, i.{_qc(i['category'])}"
    ctes = f"WITH eligible_orders AS ({eligible_sql}), category_amounts AS ({categories})"
    value = "item_minor" if strategy == "item_value" else "net_minor"
    source = "category_amounts"
    if strategy == "equal_categories":
        ctes += ", ranked AS (SELECT *, COUNT(*) OVER (PARTITION BY order_id) AS category_count, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY category) AS category_rank FROM category_amounts)"
        # Subtract the remainder before DIV so the SQLite float-based transpilation
        # divides an exactly divisible, bounded integer. Preconditions cap magnitudes.
        value = "((net_minor - (net_minor % category_count)) DIV category_count) + CASE WHEN category_rank <= (net_minor % category_count) THEN 1 ELSE 0 END"
        source = "ranked"
    dims = ", ".join(request.group_by)
    sql = f"{ctes} SELECT {dims}, SUM({value}) AS revenue_minor FROM {source} GROUP BY {dims} ORDER BY {dims}"
    # Checks are delivered with SQL. They must pass on real data before execution.
    checks = [
        (
            "order_id_not_unique_or_null",
            f"SELECT {_qc(o['id'])} FROM {_qt(model.orders)} GROUP BY {_qc(o['id'])} HAVING COUNT(*) > 1 OR {_qc(o['id'])} IS NULL",
        ),
        (
            "item_id_not_unique_or_null",
            f"SELECT {_qc(i['id'])} FROM {_qt(model.items)} GROUP BY {_qc(i['id'])} HAVING COUNT(*) > 1 OR {_qc(i['id'])} IS NULL",
        ),
        (
            "missing_booked_date",
            f"SELECT * FROM {_qt(model.orders)} o WHERE o.{_qc(o['status'])} = {_literal(model.paid_status)} AND o.{_qc(o['currency'])} = {_literal(model.currency)} AND o.{_qc(o['booked_on'])} IS NULL",
        ),
        (
            "invalid_eligible_order",
            f"SELECT * FROM eligible_orders WHERE net_minor IS NULL OR net_minor < 0 OR net_minor > {MAX_MINOR} OR booked_on IS NULL",
        ),
        (
            "invalid_eligible_item",
            f"SELECT i.* FROM {_qt(model.items)} i JOIN eligible_orders o ON i.{_qc(i['order_id'])} = o.order_id WHERE i.{_qc(i['line_minor'])} IS NULL OR i.{_qc(i['line_minor'])} < 0 OR i.{_qc(i['line_minor'])} > {MAX_MINOR} OR i.{_qc(i['category'])} IS NULL OR TRIM(i.{_qc(i['category'])}) = ''",
        ),
        (
            "missing_items_or_unreconciled_total",
            f"SELECT o.order_id FROM eligible_orders o LEFT JOIN {_qt(model.items)} i ON o.order_id = i.{_qc(i['order_id'])} GROUP BY o.order_id, o.net_minor HAVING COUNT(i.{_qc(i['id'])}) = 0 OR SUM(i.{_qc(i['line_minor'])}) != o.net_minor",
        ),
        (
            "eligible_total_exceeds_supported_range",
            f"SELECT SUM(net_minor) FROM eligible_orders HAVING SUM(net_minor) > {MAX_MINOR}",
        ),
    ]
    preflight = f"WITH eligible_orders AS ({eligible_sql}) " + " UNION ALL ".join(
        f"SELECT '{name}' AS check_name, COUNT(*) AS violation_count FROM ({check}) failures"
        for name, check in checks
    )
    return {
        "sql": sqlglot.parse_one(sql, read="databricks").sql(dialect="databricks", pretty=True)
        + ";",
        "preflight_sql": sqlglot.parse_one(preflight, read="databricks").sql(
            dialect="databricks", pretty=True
        )
        + ";",
    }


def evaluate(fixture, model, request, strategy):
    generated = queries(model, request, strategy)
    mapping = {model.orders: "synthetic_orders", model.items: "synthetic_items"}
    statements = []
    db = sqlite3.connect(":memory:")
    try:
        for name, roles in (("orders", model.order_columns), ("items", model.item_columns)):
            table = "synthetic_" + name
            ddl = (
                "CREATE TABLE "
                + quoted(table)
                + " ("
                + ", ".join(
                    quoted(col)
                    + (
                        " INTEGER"
                        if role in {"id", "order_id", "net_minor", "line_minor"}
                        else " TEXT"
                    )
                    for role, col in roles.items()
                )
                + ");"
            )
            db.execute(ddl)
            statements.append(ddl)
            for row in fixture[name]:
                values = [row[role] for role in roles]
                db.execute(
                    f"INSERT INTO {quoted(table)} VALUES ({','.join('?' for _ in values)})", values
                )
                literals = [
                    "NULL" if v is None else str(v) if isinstance(v, int) else _literal(v, "sqlite")
                    for v in values
                ]
                statements.append(f"INSERT INTO {quoted(table)} VALUES ({','.join(literals)});")

        def local(sql):
            tree = sqlglot.parse_one(sql, read="databricks")
            for table in tree.find_all(exp.Table):
                name = ".".join(part.name for part in table.parts)
                if name in mapping:
                    table.set("this", exp.to_identifier(mapping[name], quoted=True))
                    table.set("db", None)
                    table.set("catalog", None)
            return tree.sql(dialect="sqlite") + ";"

        preflight = local(generated["preflight_sql"])
        violations = {name: count for name, count in db.execute(preflight) if count}
        output = {
            "fixture_sql": "\n".join(statements),
            "preflight_sqlite": preflight,
            "query_sqlite": local(generated["sql"]),
            "violations": violations,
        }
        if violations:
            return {**output, "status": "blocked_by_preflight"}
        cursor = db.execute(output["query_sqlite"])
        actual = [
            dict(zip([d[0] for d in cursor.description], row, strict=True))
            for row in cursor.fetchall()
        ]
        actual.sort(key=lambda r: json.dumps([r[d] for d in request.group_by]))
        oracle = expected(fixture, model, request, strategy)
        return {
            **output,
            "status": "passed" if actual == oracle else "failed",
            "actual": actual,
            "expected": oracle,
        }
    finally:
        db.close()


def witness(model, request):
    day = request.start_date or (
        request.end_date - timedelta(days=1) if request.end_date else date(2026, 1, 15)
    )
    candidates = [(50000, 50000), (80000, 20000), (90000, 10000), (60000, 30000, 10000)]
    ranked = []
    for amounts in candidates:
        fixture = synthetic_fixture(amounts, day.isoformat(), model)
        results = {s: expected(fixture, model, request, s) for s in STRATEGIES}
        separation = 0
        distance = 0
        for a, b in combinations(results.values(), 2):
            if a != b:
                separation += 1
            distance += sum(
                abs(x["revenue_minor"] - y["revenue_minor"]) for x, y in zip(a, b, strict=True)
            )
        ranked.append(((separation, -len(amounts), distance), fixture, results))
    score, fixture, results = max(ranked, key=lambda entry: entry[0])
    # Independently replay each interpretation before presenting its example.
    checks = {s: evaluate(fixture, model, request, s) for s in STRATEGIES}
    if any(c["status"] != "passed" for c in checks.values()):
        raise ValueError("Synthetic ambiguity example failed validation")
    return {
        "question": "This order spans multiple categories. How should its revenue be divided?",
        "options": [
            {
                "id": s,
                "label": label,
                "results": results[s],
                "additive_across_categories": s != "full_credit",
            }
            for s, label in STRATEGIES.items()
        ]
        + [{"id": "none", "label": "None of these describes our business rule"}],
        "fixture": fixture,
        "unit": f"{model.currency} minor units (100 = 1 currency unit)",
        "selection": {
            "candidate_count": len(candidates),
            "separated_interpretation_pairs": score[0],
            "total_fixture_rows": 1 - score[1],
            "pairwise_difference_minor": score[2],
            "method": "Maximize separated pairs, then minimize rows, then maximize illustrated result difference",
            "limitation": "Fixed candidate family; not a global optimum, learned selector, probability estimate, or production-impact estimate",
        },
        "replays": checks,
    }


def compile_plan(model, request, strategy):
    if error := supported(model, request):
        return {"status": "unsupported", "reason": error}
    day = request.start_date or (
        request.end_date - timedelta(days=1) if request.end_date else date(2026, 1, 15)
    )
    fixture = synthetic_fixture(booked_on=day.isoformat(), model=model)
    fixtures = [("asymmetric_categories", fixture, True)]
    odd = synthetic_fixture((33334, 33334, 33333), day.isoformat(), model)
    fixtures.append(("rounding_and_three_categories", odd, True))
    repeated = copy.deepcopy(fixture)
    repeated["items"][0]["line_minor"] -= 10000
    repeated["items"].append({"id": 3, "order_id": 1, "category": "Books", "line_minor": 10000})
    fixtures.append(("multiple_lines_in_one_category", repeated, True))
    transferred = copy.deepcopy(fixture)
    transferred["orders"].append(
        {**fixture["orders"][0], "id": 2, "region": "South", "net_minor": 70000}
    )
    transferred["items"].append({"id": 3, "order_id": 2, "category": "Books", "line_minor": 70000})
    fixtures.append(("second_region", transferred, True))
    for label, key, value in (
        ("unpaid", "status", "cancelled"),
        ("other_currency", "currency", "USD" if model.currency != "USD" else "INR"),
    ):
        other = copy.deepcopy(fixture)
        other["orders"][0][key] = value
        fixtures.append((label, other, True))
    zero = synthetic_fixture((0, 0), day.isoformat(), model)
    fixtures.extend([("zero_revenue", zero, True), ("empty", {"orders": [], "items": []}, True)])
    if request.start_date and request.start_date > date.min:
        outside = copy.deepcopy(fixture)
        outside["orders"][0]["booked_on"] = (request.start_date - timedelta(days=1)).isoformat()
        fixtures.append(("before_start", outside, True))
    if request.end_date:
        outside = copy.deepcopy(fixture)
        outside["orders"][0]["booked_on"] = request.end_date.isoformat()
        fixtures.append(("exclusive_end", outside, True))
    for label in (
        "duplicate_order",
        "duplicate_item",
        "missing_items",
        "mismatched_total",
        "null_category",
        "negative_item",
    ):
        invalid = copy.deepcopy(fixture)
        if label == "duplicate_order":
            invalid["orders"].append(invalid["orders"][0].copy())
        elif label == "duplicate_item":
            invalid["items"].append(invalid["items"][0].copy())
        elif label == "missing_items":
            invalid["items"] = []
        elif label == "mismatched_total":
            invalid["orders"][0]["net_minor"] += 1
        elif label == "null_category":
            invalid["items"][0]["category"] = None
        else:
            invalid["items"][0]["line_minor"] = -1
        fixtures.append((label, invalid, False))
    results = [
        dict(name=name, should_be_valid=valid, **evaluate(data, model, request, strategy))
        for name, data, valid in fixtures
    ]
    if any(
        r["status"] != ("passed" if r["should_be_valid"] else "blocked_by_preflight")
        for r in results
    ):
        return {"status": "validation_failed", "cases": results}
    return {
        "status": "compiled",
        **queries(model, request, strategy),
        "strategy": strategy,
        "request": request.model_dump(mode="json"),
        "unit": f"{model.currency} minor units",
        "additive_across_categories": strategy != "full_credit",
        "rounding": "Equal allocation uses integer minor units; residual units go to categories in binary lexical order",
        "execution_gate": "Run preflight_sql on the actual source and require all violation_count values to be zero before running sql",
        "preconditions": [
            "Unique non-NULL order and item IDs",
            "Each eligible order has nonnegative item amounts summing to its order net amount",
            "Nonempty category labels; booked date present",
            f"Eligible total <= {MAX_MINOR} minor units; default binary string collation",
        ],
        "validation": {
            "case_count": len(results),
            "cases": results,
            "method": "Generated Databricks SQL transpiled to SQLite, checked against independent Python allocation logic",
        },
        "limitations": [
            "Synthetic validation only; no actual data, runtime, permissions or preflight execution verified",
            "Configured meanings, not automatic natural-language or ontology discovery",
            "Refunds, recognition timing, tax/shipping allocation, mixed currencies, and other groupings require new definitions",
        ],
    }
