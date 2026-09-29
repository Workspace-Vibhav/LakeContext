"""Conservative join extraction. SQL text never enters the stored graph."""

import hashlib
import re
from collections import defaultdict
from itertools import combinations

import sqlglot
from sqlglot import exp

from lakecontext.models import Snapshot


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]


def is_identifier(name: str) -> bool:
    """Recognize a delimited ID suffix, not words such as paid, grid or valid."""
    return name.casefold().endswith("_id") or bool(re.search(r"(?<=[a-z0-9])(?:Id|ID)$", name))


def compile_snapshot(snapshot: Snapshot) -> dict:
    tables = {t.name: t for t in snapshot.tables}
    canonical = {name.casefold(): name for name in tables}
    columns = {(t.name, c.name.casefold()): c for t in tables.values() for c in t.columns}
    edges: dict[str, dict] = {}
    warnings = []

    def add(left, right, evidence):
        left, right = sorted((left, right))
        if left[0] == right[0]:
            return
        edge_id = stable_id("join", *left, *right)
        edge = edges.setdefault(
            edge_id,
            {
                "id": edge_id,
                "kind": "join",
                "left": {"table": left[0], "column": left[1]},
                "right": {"table": right[0], "column": right[1]},
                "evidence": [],
                "status": "proposed",
                "definition_fingerprint": stable_id(
                    *left,
                    columns[(left[0], left[1].casefold())].data_type,
                    *right,
                    columns[(right[0], right[1].casefold())].data_type,
                ),
            },
        )
        if evidence not in edge["evidence"]:
            edge["evidence"].append(evidence)

    # Name similarity is only a suggestion, never proof of a valid join.
    by_column = defaultdict(list)
    for table in tables.values():
        for col in table.columns:
            if is_identifier(col.name):
                by_column[(col.name.casefold(), col.data_type.casefold())].append(
                    (table.name, col.name)
                )
    for candidates in by_column.values():
        if len(candidates) > 50:
            warnings.append("Skipped an overly common identifier shared by more than 50 tables")
            continue
        for left, right in combinations(candidates, 2):
            add(
                left,
                right,
                {
                    "kind": "matching_name_type",
                    "citation": "schema",
                    "detail": "Same identifier name and type; semantics unverified",
                },
            )

    for query in snapshot.queries:
        try:
            statements = sqlglot.parse(query.sql, read="databricks")
        except sqlglot.errors.ParseError:
            warnings.append(f"Query {query.id}: parse failed")
            continue
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            warnings.append(f"Query {query.id}: only a single SELECT is supported")
            continue
        tree = statements[0]
        if any(tree.find_all(exp.Subquery, exp.CTE, exp.Union)):
            warnings.append(f"Query {query.id}: nested SQL skipped to avoid ambiguous lineage")
            continue
        aliases = {}
        for node in tree.find_all(exp.Table):
            name = ".".join(part.name for part in node.parts)
            resolved = canonical.get(name.casefold())
            alias = node.alias_or_name.casefold()
            if alias in aliases:
                aliases[alias] = None
            else:
                aliases[alias] = resolved

        def endpoint(node, scope=aliases):
            if not isinstance(node, exp.Column):
                return None
            table = scope.get(node.table.casefold())
            if node.db or node.catalog:
                qualified = ".".join(part.name for part in node.parts[:-1])
                if not table or qualified.casefold() != table.casefold():
                    return None
            col = columns.get((table, node.name.casefold()))
            return (table, col.name) if table and col else None

        for join in tree.find_all(exp.Join):
            on = join.args.get("on")
            if on is None:
                continue
            # Only conjunctive equalities are evidence. OR/NOT predicates are not joins.
            predicates = list(on.flatten()) if isinstance(on, exp.And) else [on]
            for predicate in predicates:
                if not isinstance(predicate, exp.EQ):
                    continue
                left, right = endpoint(predicate.left), endpoint(predicate.right)
                if left and right:
                    add(
                        left,
                        right,
                        {
                            "kind": "observed_sql_join",
                            "citation": f"query:{query.id}",
                            "query_hash": hashlib.sha256(query.sql.encode()).hexdigest(),
                        },
                    )
    for edge in edges.values():
        edge["observed_query_count"] = sum(
            e["kind"] == "observed_sql_join" for e in edge["evidence"]
        )
        edge["evidence_strength"] = "observed" if edge["observed_query_count"] else "heuristic"

    return {
        "version": 1,
        "source": snapshot.source,
        "principal": snapshot.principal,
        "captured_at": snapshot.captured_at,
        "tables": [t.model_dump() for t in snapshot.tables],
        "terms": [t.model_dump() for t in snapshot.terms],
        "lineage": [e.model_dump() for e in snapshot.lineage],
        "relationships": sorted(edges.values(), key=lambda e: e["id"]),
        "warnings": warnings,
    }
