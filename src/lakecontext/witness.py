"""Query-specific join obligations with replayed, synthetic counterexamples.

This is a deliberately small SQL fragment, not a general SQL verifier.
No user SQL is executed: a validated plan is compiled to local SQLite only.
"""

import copy
import hashlib
import json
import re
import sqlite3

import sqlglot
from sqlglot import exp

INTEGERS = {"tinyint", "smallint", "int", "integer", "bigint", "byte", "short", "long"}


class UnsupportedQuery(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def quoted(value):
    return '"' + value.replace('"', '""') + '"'


def _check_args(node, allowed):
    if any(value and key not in allowed for key, value in node.args.items()):
        raise UnsupportedQuery(f"Unsupported SQL options on {type(node).__name__}")


def parse_plan(sql, graph):
    if len(sql) > 24_000:
        raise UnsupportedQuery("SQL exceeds the 24,000 character limit")
    try:
        statements = sqlglot.parse(sql, read="databricks")
    except sqlglot.errors.ParseError as exc:
        raise UnsupportedQuery("SQL could not be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise UnsupportedQuery("Expected one SELECT")
    tree = statements[0]
    _check_args(tree, {"expressions", "from_", "joins"})
    if len(tree.expressions) != 1:
        raise UnsupportedQuery("Select exactly one SUM(base_alias.integer_column)")
    metric = tree.expressions[0]
    output_alias = metric.alias if isinstance(metric, exp.Alias) else None
    if isinstance(metric, exp.Alias):
        metric = metric.this
    if not isinstance(metric, exp.Sum) or not isinstance(metric.this, exp.Column):
        raise UnsupportedQuery("Only SUM of a single base-table integer column is supported")
    _check_args(metric, {"this"})
    catalog = {t["name"].casefold(): t for t in graph["tables"]}
    nodes, joins = {}, []

    def table(node):
        if not isinstance(node, exp.Table):
            raise UnsupportedQuery("Subqueries and table functions are not supported")
        _check_args(node, {"this", "db", "catalog", "alias"})
        if node.args.get("alias"):
            _check_args(node.args["alias"], {"this"})
        if not all(isinstance(part, exp.Identifier) for part in node.parts):
            raise UnsupportedQuery("Only ordinary named tables are supported")
        name = ".".join(part.name for part in node.parts).casefold()
        if name not in catalog:
            raise UnsupportedQuery("Use fully qualified tables present in the imported snapshot")
        alias = node.alias_or_name.casefold()
        if alias in nodes or any(t["name"].casefold() == name for t in nodes.values()):
            raise UnsupportedQuery("Self-joins and reused aliases are not supported")
        nodes[alias] = catalog[name]
        return alias

    source = tree.args.get("from_")
    if not source:
        raise UnsupportedQuery("Missing base table")
    _check_args(source, {"this"})
    root = table(source.this)

    def column(node):
        if not isinstance(node, exp.Column) or len(node.parts) != 2:
            raise UnsupportedQuery("Columns must use alias.column syntax")
        alias = node.table.casefold()
        if alias not in nodes:
            raise UnsupportedQuery("Column references an unknown or future alias")
        col = next(
            (c for c in nodes[alias]["columns"] if c["name"].casefold() == node.name.casefold()),
            None,
        )
        if col is None:
            raise UnsupportedQuery("Column is not present in the imported schema")
        return alias, col["name"], col["data_type"].casefold()

    measure_alias, measure, measure_type = column(metric.this)
    if measure_alias != root or measure_type not in INTEGERS:
        raise UnsupportedQuery("Metric must be an integer column on the FROM table")

    def predicates(node):
        node = node.unnest()
        if isinstance(node, exp.And):
            return predicates(node.left) + predicates(node.right)
        if not isinstance(node, exp.EQ):
            raise UnsupportedQuery("JOIN ON must contain only conjunctive column equalities")
        return [node]

    for join in tree.args.get("joins", []):
        if len(nodes) >= 8:
            raise UnsupportedQuery("At most eight tables are supported")
        _check_args(join, {"this", "side", "kind", "on"})
        side, kind = join.side.upper(), join.kind.upper()
        if side not in {"", "LEFT"} or kind not in {"", "INNER", "OUTER"}:
            raise UnsupportedQuery("Only INNER and LEFT joins are supported")
        if (side == "LEFT" and kind == "INNER") or (not side and kind == "OUTER"):
            raise UnsupportedQuery("Unsupported join type")
        child = table(join.this)
        if join.args.get("on") is None:
            raise UnsupportedQuery("Explicit JOIN ON is required")
        parent, pairs = None, []
        for equality in predicates(join.args["on"]):
            a, b = column(equality.left), column(equality.right)
            if a[0] == child:
                a, b = b, a
            if b[0] != child or a[0] == child or (parent and parent != a[0]):
                raise UnsupportedQuery("Each joined table must connect to exactly one prior table")
            if a[2] != b[2] or not (
                a[2] in INTEGERS
                or a[2] == "string"
                or re.fullmatch(r"(?:var)?char\([1-9]\d*\)", a[2])
            ):
                raise UnsupportedQuery("Join keys require identical integer or string types")
            if (a[0] == root and a[1] == measure) or (b[0] == root and b[1] == measure):
                raise UnsupportedQuery("The metric column cannot also be a join key")
            parent = a[0]
            pair = (a[1], b[1])
            if pair not in pairs:
                pairs.append(pair)
        if len(pairs) > 8:
            raise UnsupportedQuery("At most eight key pairs per join are supported")
        if len({p[0] for p in pairs}) != len(pairs) or len({p[1] for p in pairs}) != len(pairs):
            raise UnsupportedQuery("Join keys must pair distinct columns one-to-one")
        joins.append(
            {
                "parent": parent,
                "child": child,
                "pairs": sorted(pairs),
                "kind": "LEFT" if side == "LEFT" else "INNER",
            }
        )
    return {
        "root": root,
        "measure": measure,
        "output_alias": output_alias,
        "nodes": nodes,
        "joins": joins,
    }


def obligations(plan, context_id):
    by_child = {j["child"]: j for j in plan["joins"]}
    coverage_needed = set()
    for join in plan["joins"]:
        if join["kind"] == "INNER":
            node = join["child"]
            # An INNER descendant can null-reject a LEFT ancestor's missing match.
            while node != plan["root"]:
                coverage_needed.add(node)
                node = by_child[node]["parent"]
    result = []
    for join in plan["joins"]:
        child, parent = join["child"], join["parent"]
        child_name, parent_name = plan["nodes"][child]["name"], plan["nodes"][parent]["name"]
        child_cols, parent_cols = [p[1] for p in join["pairs"]], [p[0] for p in join["pairs"]]
        kinds = ["unique_match_key"] + (["total_match"] if child in coverage_needed else [])
        for kind in kinds:
            question = (
                f"Does each non-NULL ({', '.join(child_cols)}) tuple identify at most one row in {child_name}?"
                if kind == "unique_match_key"
                else f"Does every row in {parent_name} have non-NULL ({', '.join(parent_cols)}) values and at least one match in {child_name} on ({', '.join(child_cols)})?"
            )
            result.append(
                {
                    "id": fingerprint([context_id, kind, join]),
                    "kind": kind,
                    "join": join,
                    "question": question,
                    "table": child_name,
                    "columns": child_cols,
                    "source_table": parent_name,
                    "source_columns": parent_cols,
                    "reason": "Prevent duplicated base rows"
                    if kind == "unique_match_key"
                    else "Prevent lost base rows, including null-rejection through an INNER descendant",
                    "citations": [f"table:{parent_name}", f"table:{child_name}"],
                }
            )
    return result


def contract_holds(contract, rows):
    join = contract["join"]
    right_keys = [tuple(row[p[1]] for p in join["pairs"]) for row in rows[join["child"]]]
    nonnull = [key for key in right_keys if None not in key]
    if contract["kind"] == "unique_match_key":
        return len(nonnull) == len(set(nonnull))
    return all(
        None not in (key := tuple(row[p[0]] for p in join["pairs"])) and key in nonnull
        for row in rows[join["parent"]]
    )


def seed_rows(plan):
    fields = {alias: set() for alias in plan["nodes"]}
    fields[plan["root"]].add(plan["measure"])
    for join in plan["joins"]:
        fields[join["parent"]].update(p[0] for p in join["pairs"])
        fields[join["child"]].update(p[1] for p in join["pairs"])
    rows = {}
    for alias, table in plan["nodes"].items():
        rows[alias] = [
            {
                c["name"]: 1 if c["data_type"].casefold() in INTEGERS else "k"
                for c in table["columns"]
                if c["name"] in fields[alias]
            }
        ]
    rows[plan["root"]][0][plan["measure"]] = 100
    return rows


def replay(plan, rows):
    """Execute only our own finite, quoted SELECT plan in an ephemeral database."""
    connection = sqlite3.connect(":memory:")
    fixture, mapping = [], {alias: f"synthetic_{i}" for i, alias in enumerate(plan["nodes"])}
    seed = seed_rows(plan)
    try:
        for alias, table in plan["nodes"].items():
            name = quoted(mapping[alias])
            fields = list(seed[alias][0])
            types = {
                c["name"]: "INTEGER" if c["data_type"].casefold() in INTEGERS else "TEXT"
                for c in table["columns"]
            }
            ddl = (
                f"CREATE TABLE {name} ("
                + ", ".join(f"{quoted(c)} {types[c]}" for c in fields)
                + ");"
            )
            connection.execute(ddl)
            fixture.append(ddl)
            for row in rows[alias]:
                values = [row[c] for c in fields]
                connection.execute(
                    f"INSERT INTO {name} VALUES ({', '.join('?' for _ in fields)})", values
                )
                literals = [
                    "NULL"
                    if v is None
                    else str(v)
                    if isinstance(v, int)
                    else "'" + v.replace("'", "''") + "'"
                    for v in values
                ]
                fixture.append(f"INSERT INTO {name} VALUES ({', '.join(literals)});")
        root = plan["root"]
        metric = f"SUM({quoted(root)}.{quoted(plan['measure'])})"
        base_from = f"FROM {quoted(mapping[root])} AS {quoted(root)}"
        query_from = base_from
        for join in plan["joins"]:
            child, parent = join["child"], join["parent"]
            on = " AND ".join(
                f"{quoted(parent)}.{quoted(a)} = {quoted(child)}.{quoted(b)}"
                for a, b in join["pairs"]
            )
            query_from += (
                f" {join['kind']} JOIN {quoted(mapping[child])} AS {quoted(child)} ON {on}"
            )
        base_sql, query_sql = (
            f"SELECT {metric}, COUNT(*) {base_from};",
            f"SELECT {metric}, COUNT(*) {query_from};",
        )
        base, joined = (
            connection.execute(base_sql).fetchone(),
            connection.execute(query_sql).fetchone(),
        )
        return {
            "base_sum": base[0],
            "joined_sum": joined[0],
            "base_rows": base[1],
            "joined_rows": joined[1],
            "fixture_sql": "\n".join(fixture),
            "base_sql": base_sql,
            "query_sql": query_sql,
            "tables": [
                {"synthetic_name": mapping[a], "source_table": plan["nodes"][a]["name"], "rows": r}
                for a, r in rows.items()
            ],
        }
    finally:
        connection.close()


def make_witness(plan, required, target):
    rows = seed_rows(plan)
    child = target["join"]["child"]
    if target["kind"] == "unique_match_key":
        rows[child].append(rows[child][0].copy())
    else:
        removed = {child}
        for join in plan["joins"]:
            if join["parent"] in removed:
                removed.add(join["child"])
        for alias in removed:
            rows[alias] = []

    def valid(candidate):
        if not candidate[plan["root"]] or contract_holds(target, candidate):
            return False
        if any(not contract_holds(c, candidate) for c in required if c["id"] != target["id"]):
            return False
        evaluation = replay(plan, candidate)
        return evaluation["base_sum"] != evaluation["joined_sum"]

    if not valid(rows):
        raise ValueError("Could not construct a valid witness; no safety conclusion was made")
    # Minimize by deletion within the constructed fixture, not over arbitrary databases.
    changed = True
    while changed:
        changed = False
        for alias in rows:
            for index in range(len(rows[alias])):
                candidate = copy.deepcopy(rows)
                candidate[alias].pop(index)
                if valid(candidate):
                    rows, changed = candidate, True
                    break
            if changed:
                break
    evaluation = replay(plan, rows)
    return {
        "violated_contract": target["id"],
        "failure": "fanout" if target["kind"] == "unique_match_key" else "row_loss",
        "other_required_contracts_satisfied": True,
        "minimality": "No single fixture row can be removed while retaining this witness and all other required contracts",
        **evaluation,
    }


def audit(sql, graph, assumptions=None):
    plan = parse_plan(sql, graph)
    context_id = fingerprint(
        {
            "plan": plan,
            "source": graph["source"],
            "principal": graph["principal"],
            "captured_at": graph["captured_at"],
        }
    )
    required = obligations(plan, context_id)
    known = {c["id"] for c in required}
    accepted = {}
    for item in assumptions or []:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "evidence"}
            or not isinstance(item["id"], str)
            or item["id"] not in known
            or not isinstance(item["evidence"], str)
            or not item["evidence"].strip()
            or len(item["evidence"]) > 2000
        ):
            raise ValueError(
                "Assumptions require a current contract id and nonempty evidence; refresh/schema/query changes invalidate old ids"
            )
        if item["id"] in accepted:
            raise ValueError("Duplicate assumption id")
        accepted[item["id"]] = item["evidence"]
    missing = [c for c in required if c["id"] not in accepted]
    worlds = [make_witness(plan, required, c) for c in missing]
    for contract in required:
        contract["status"] = "assumed" if contract["id"] in accepted else "missing"
        contract["evidence"] = accepted.get(contract["id"])
    return {
        "method": "join-witness/v1",
        "context_id": context_id,
        "objective": "Preserve SUM of every base-table row; joins serve only to enrich the metric",
        "verdict": "counterexample_found" if missing else "preserved_under_stated_contracts",
        "base_table": plan["nodes"][plan["root"]]["name"],
        "metric_column": plan["measure"],
        "contracts": required,
        "questions": [c["question"] for c in missing],
        "safe_example": replay(plan, seed_rows(plan)),
        "counterexamples": worlds,
        "data_access": "Synthetic rows only; no warehouse queries or real row values",
        "scope": "Flat SUM(integer) with tree-shaped INNER/LEFT equijoins, no filters or grouping",
        "limitations": [
            "A counterexample proves possibility under the modeled schema, not an error in your actual data.",
            "Preservation is conditional on supplied contracts; this tool does not validate their evidence.",
            "Contracts are sufficient global conditions in a fixed vocabulary, not the weakest possible SQL constraints.",
            "Fixtures model referenced columns and types only; unimported source constraints are unknown.",
            "Intentional filtering or allocation of metrics requires a different objective.",
            "SQLite replays demonstrate this restricted fragment; they do not verify arbitrary Databricks SQL.",
        ],
    }
