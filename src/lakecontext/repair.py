"""Intent-driven repairs for the auditor's restricted aggregation fragment.

Repairs are constructed as SQL ASTs, then the delivered Databricks SQL is
transpiled and tested on synthetic fixtures against a separate bag-semantics oracle.
No input query or repaired query is submitted to a warehouse.
"""

import copy
import sqlite3
from collections import defaultdict
from itertools import combinations
from typing import Literal

import sqlglot
from sqlglot import exp

from lakecontext.witness import audit, fingerprint, parse_plan, replay, seed_rows

RepairIntent = Literal["preserve_base", "preserve_eligibility", "require_matches"]

INTENTS = {
    "preserve_base": "Sum every base-table row once, even if joined records are missing.",
    "preserve_eligibility": "Sum each base-table row once if it survives the original INNER/LEFT join structure.",
    "require_matches": "Sum each base-table row once only when all joined tables have a coherent matching path; LEFT joins become requirements too.",
}


def required_nodes(plan, intent):
    """Paths with INNER joins are required, including any LEFT ancestors."""
    if intent == "preserve_base":
        return set()
    if intent == "require_matches":
        return {j["child"] for j in plan["joins"]}
    parents = {j["child"]: j["parent"] for j in plan["joins"]}
    required = set()
    for join in plan["joins"]:
        if join["kind"] == "INNER":
            node = join["child"]
            while node != plan["root"]:
                required.add(node)
                node = parents[node]
    return required


def generate_sql(plan, intent):
    if intent not in INTENTS:
        raise ValueError("Unknown repair intent")
    required = required_nodes(plan, intent)
    children = defaultdict(list)
    for join in plan["joins"]:
        if join["child"] in required:
            children[join["parent"]].append(join)

    def table(alias):
        catalog, schema, name = plan["nodes"][alias]["name"].split(".")
        return exp.Table(
            this=exp.to_identifier(name, quoted=True),
            db=exp.to_identifier(schema, quoted=True),
            catalog=exp.to_identifier(catalog, quoted=True),
            alias=exp.TableAlias(this=exp.to_identifier(alias, quoted=True)),
        )

    def column(alias, name):
        return exp.column(name, table=alias, quoted=True)

    def exists(join):
        parent, child = join["parent"], join["child"]
        conditions = [
            exp.EQ(this=column(parent, a), expression=column(child, b)) for a, b in join["pairs"]
        ]
        # All dependent branches live inside the same parent-row scope. Separate
        # sibling EXISTS at the outer scope could combine incompatible parent rows.
        conditions.extend(exists(descendant) for descendant in children[child])
        query = exp.select(exp.Literal.number(1)).from_(table(child)).where(exp.and_(*conditions))
        return exp.Exists(this=query)

    metric = exp.Sum(this=column(plan["root"], plan["measure"]))
    if plan["output_alias"] is not None:
        metric = exp.alias_(metric, plan["output_alias"], quoted=True)
    query = exp.select(metric).from_(table(plan["root"]))
    conditions = [exists(join) for join in children[plan["root"]]]
    if conditions:
        query = query.where(exp.and_(*conditions))
    return query.sql(dialect="databricks", pretty=True) + ";"


def joined_bag(plan, rows, require_all=False):
    """Independent Python interpreter; row indices preserve base-row multiplicity."""
    states = [(index, {plan["root"]: row}) for index, row in enumerate(rows[plan["root"]])]
    for join in plan["joins"]:
        next_states = []
        for index, state in states:
            parent = state[join["parent"]]
            matches = []
            if parent is not None:
                matches = [
                    row
                    for row in rows[join["child"]]
                    if all(
                        parent[a] is not None and row[b] is not None and parent[a] == row[b]
                        for a, b in join["pairs"]
                    )
                ]
            if not matches and join["kind"] == "LEFT" and not require_all:
                matches = [None]
            for match in matches:
                next_states.append((index, {**state, join["child"]: match}))
        states = next_states
    return states


def intent_result(plan, rows, intent):
    if intent == "preserve_base":
        eligible = set(range(len(rows[plan["root"]])))
    else:
        eligible = {i for i, _ in joined_bag(plan, rows, require_all=intent == "require_matches")}
    values = [
        row[plan["measure"]]
        for index, row in enumerate(rows[plan["root"]])
        if index in eligible and row[plan["measure"]] is not None
    ]
    return {"sum": sum(values) if values else None, "base_rows": len(eligible)}


def evaluate_repair(sql, plan, rows, intent):
    original = replay(plan, rows)
    mapping = {t["source_table"].casefold(): t["synthetic_name"] for t in original["tables"]}
    # Test the delivered dialect SQL, rather than regenerating an independent repair.
    query = sqlglot.parse_one(sql, read="databricks")
    for table in query.find_all(exp.Table):
        name = ".".join(part.name for part in table.parts).casefold()
        if name not in mapping:
            raise ValueError("Generated repair referenced an unexpected table")
        table.set("this", exp.to_identifier(mapping[name], quoted=True))
        table.set("db", None)
        table.set("catalog", None)
    repaired_sqlite = query.sql(dialect="sqlite") + ";"
    # Also check row eligibility, since SUM alone can hide cancellations or NULL measures.
    count_query = query.copy()
    count_query.set("expressions", [exp.Count(this=exp.Star())])
    expected = intent_result(plan, rows, intent)
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(original["fixture_sql"])
        actual = db.execute(repaired_sqlite).fetchone()[0]
        actual_count = db.execute(count_query.sql(dialect="sqlite")).fetchone()[0]
    finally:
        db.close()
    return {
        "passed": actual == expected["sum"] and actual_count == expected["base_rows"],
        "original_sum": original["joined_sum"],
        "original_joined_rows": original["joined_rows"],
        "expected_sum": expected["sum"],
        "repaired_sum": actual,
        "expected_base_rows": expected["base_rows"],
        "repaired_base_rows": actual_count,
        "fixture_sql": original["fixture_sql"],
        "original_sqlite_sql": original["query_sql"],
        "repaired_sqlite_sql": repaired_sqlite,
    }


def validation_cases(plan, diagnosis):
    seed = seed_rows(plan)
    cases = [("one_match_per_table", seed)]
    aliases = {t["name"]: alias for alias, t in plan["nodes"].items()}
    for witness in diagnosis["counterexamples"]:
        cases.append(
            (
                f"witness_{witness['failure']}_{witness['violated_contract']}",
                {aliases[t["source_table"]]: t["rows"] for t in witness["tables"]},
            )
        )
    cases.append(("all_tables_empty", {alias: [] for alias in seed}))
    for alias in seed:
        empty = copy.deepcopy(seed)
        empty[alias] = []
        cases.append((f"empty_{alias}", empty))
        duplicate = copy.deepcopy(seed)
        duplicate[alias].append(duplicate[alias][0].copy())
        cases.append((f"duplicate_{alias}", duplicate))
    all_double = {alias: [rows[0].copy(), rows[0].copy()] for alias, rows in seed.items()}
    cases.append(("all_tables_have_duplicate_rows", all_double))
    mixed = copy.deepcopy(seed)
    for value in (100, 0, -100, None):
        row = mixed[plan["root"]][0].copy()
        row[plan["measure"]] = value
        mixed[plan["root"]].append(row)
    cases.append(("equal_zero_negative_and_null_measures", mixed))
    mixed_keys = copy.deepcopy(seed)
    unmatched = mixed_keys[plan["root"]][0].copy()
    for join in plan["joins"]:
        if join["parent"] == plan["root"]:
            for col, _ in join["pairs"]:
                unmatched[col] = 2 if isinstance(unmatched[col], int) else "z"
    mixed_keys[plan["root"]].append(unmatched)
    cases.append(("matched_and_unmatched_base_rows", mixed_keys))
    all_null = copy.deepcopy(seed)
    all_null[plan["root"]][0][plan["measure"]] = None
    cases.append(("all_base_measures_null", all_null))
    for join in plan["joins"]:
        for parent_col, child_col in join["pairs"]:
            for alias, col in ((join["parent"], parent_col), (join["child"], child_col)):
                null_key = copy.deepcopy(seed)
                null_key[alias][0][col] = None
                cases.append((f"null_{alias}_{col}", null_key))
            mismatch = copy.deepcopy(seed)
            value = mismatch[join["child"]][0][child_col]
            mismatch[join["child"]][0][child_col] = 2 if isinstance(value, int) else "z"
            cases.append((f"mismatch_{join['child']}_{child_col}", mismatch))
    by_parent = defaultdict(list)
    incoming_columns = {}
    for join in plan["joins"]:
        by_parent[join["parent"]].append(join)
        incoming_columns[join["child"]] = {b for _, b in join["pairs"]}
    for parent, children in by_parent.items():
        for left, right in combinations(children, 2):
            shared = {a for a, _ in left["pairs"]} & {a for a, _ in right["pairs"]}
            shared -= incoming_columns.get(parent, set())
            if parent == plan["root"]:
                shared.discard(plan["measure"])
            if not shared:
                continue
            pivot = min(shared)
            split = copy.deepcopy(seed)
            alternate = 2 if isinstance(split[parent][0][pivot], int) else "z"
            split[parent].append({**split[parent][0], pivot: alternate})
            right_col = next(b for a, b in right["pairs"] if a == pivot)
            split[right["child"]][0][right_col] = alternate
            cases.append((f"incompatible_parent_matches_{left['child']}_{right['child']}", split))
    return cases


def repair(sql, graph, intent: RepairIntent | None = None):
    plan = parse_plan(sql, graph)
    if intent is None:
        return {
            "status": "needs_intent",
            "question": "Which base rows should contribute to this total?",
            "available_intents": INTENTS,
            "message": "No SQL was rewritten. Select the metric's intended row eligibility.",
        }
    if intent not in INTENTS:
        raise ValueError(f"Unknown intent; choose one of: {', '.join(INTENTS)}")
    diagnosis = audit(sql, graph)
    corrected = generate_sql(plan, intent)
    results = [
        dict(name=name, **evaluate_repair(corrected, plan, rows, intent))
        for name, rows in validation_cases(plan, diagnosis)
    ]
    if not all(case["passed"] for case in results):
        # Never return a usable correction when validation fails.
        return {
            "status": "validation_failed",
            "intent": intent,
            "failed_cases": [r for r in results if not r["passed"]],
            "message": "Repair withheld because a synthetic check failed.",
        }
    required = required_nodes(plan, intent)
    dropped = [
        t["name"]
        for alias, t in plan["nodes"].items()
        if alias != plan["root"] and alias not in required
    ]
    semantics_change = (
        "Removes join eligibility restrictions as requested; all base rows contribute."
        if intent == "preserve_base"
        else "Preserves the original join tree's base-row eligibility while removing join multiplication."
        if intent == "preserve_eligibility"
        else "Requires a coherent match in every joined table, including tables originally LEFT-joined."
    )
    return {
        "status": "repaired",
        "intent": intent,
        "objective": INTENTS[intent],
        "corrected_sql": corrected,
        "dialect": "databricks",
        "repair_id": fingerprint([diagnosis["context_id"], intent, corrected]),
        "context_id": diagnosis["context_id"],
        "strategy": "correlated_exists" if required else "base_aggregation",
        "explanation": semantics_change,
        "removed_tables": dropped,
        "guarantee": "For this supported fragment and explicit intent, each eligible physical base row contributes once. No uniqueness or total-matching assumption is required.",
        "validation": {
            "status": "passed",
            "case_count": len(results),
            "oracle": "Independent Python bag-semantics evaluator; delivered SQL replayed in SQLite",
            "cases": results,
        },
        "citations": [f"table:{t['name']}" for t in plan["nodes"].values()],
        "scope": diagnosis["scope"],
        "limitations": [
            "Correctness is relative to the selected metric intent, which the tool cannot infer from SQL alone.",
            "Synthetic validation is not a test of Databricks runtime behavior, permissions, performance, or actual data.",
            "Filters, grouping, decimal measures, allocation across dimensions, and arbitrary SQL remain unsupported.",
            "No warehouse query was executed and no remote data or query was changed.",
        ],
    }
