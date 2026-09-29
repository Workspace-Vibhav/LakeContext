import argparse
import json
import os
import sys
from pathlib import Path

from lakecontext.models import Snapshot
from lakecontext.service import ContextService
from lakecontext.store import Store


def parser():
    root = argparse.ArgumentParser(description="Evidence-backed metadata context for AI agents")
    root.add_argument(
        "--db", default=".lakecontext/metadata.db", help="One database per source/identity"
    )
    root.add_argument(
        "--max-age-hours", type=float, default=24, help="Context snapshot expiry (default 24h)"
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Load synthetic commerce metadata")
    ingest = commands.add_parser(
        "ingest", help="Atomically replace metadata from a version-1 JSON snapshot"
    )
    ingest.add_argument("file", type=Path)
    live = commands.add_parser(
        "sync-databricks", help="Import Unity Catalog metadata using unified auth"
    )
    live.add_argument("--catalog", action="append", required=True)
    live.add_argument(
        "--schema", action="append", help="Optional schema name filter across selected catalogs"
    )
    live.add_argument("--profile", help="Databricks configuration profile")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    describe = commands.add_parser("describe")
    describe.add_argument("table")
    context = commands.add_parser("context")
    context.add_argument("question")
    context.add_argument("--limit", type=int, default=5)
    context.add_argument("--include-proposed", action="store_true")
    relationships = commands.add_parser("relationships")
    relationships.add_argument("--table")
    relationships.add_argument("--include-proposed", action="store_true")
    review = commands.add_parser("review", help="Human review, never exposed as an MCP tool")
    review.add_argument("id")
    review.add_argument("status", choices=["approved", "rejected"])
    review.add_argument("--reviewer", required=True)
    review.add_argument("--reason", required=True)
    commands.add_parser(
        "export", help="Export the local graph, including rejected edges; no raw SQL"
    )
    commands.add_parser("serve", help="Start local MCP; planning saves clarification receipts")
    audit = commands.add_parser(
        "audit-sql", help="Construct synthetic metric-loss witnesses; no warehouse SQL"
    )
    audit.add_argument("file", type=Path, help="SQL file containing one supported SELECT SUM")
    audit.add_argument(
        "--assumptions", type=Path, help="JSON array of current contract id/evidence objects"
    )
    repair = commands.add_parser(
        "repair-sql", help="Generate intent-driven corrected SQL and synthetic validation"
    )
    repair.add_argument("file", type=Path)
    repair.add_argument(
        "--intent", choices=["preserve_base", "preserve_eligibility", "require_matches"]
    )
    repair.add_argument(
        "--sql-only", action="store_true", help="Print only the validated corrected Databricks SQL"
    )
    commands.add_parser(
        "revenue-demo", help="Load the synthetic revenue model, without confirming an allocation"
    )
    register = commands.add_parser(
        "revenue-register", help="Register an explicit order/item semantic mapping"
    )
    register.add_argument("file", type=Path)
    planning = commands.add_parser(
        "revenue-plan", help="Ask the deciding question or reuse a confirmed scoped rule"
    )
    planning.add_argument("file", type=Path, help="Structured RevenueRequest JSON")
    planning.add_argument("--review-definition", action="store_true")
    confirm = commands.add_parser(
        "revenue-confirm", help="Human confirmation of a displayed interpretation"
    )
    confirm.add_argument("question_id")
    confirm.add_argument(
        "strategy", choices=["item_value", "equal_categories", "full_credit", "none"]
    )
    resolve = commands.add_parser(
        "revenue-resolve", help="Explicitly resolve an existing definition conflict"
    )
    resolve.add_argument("conflict_id")
    resolve.add_argument("strategy", choices=["item_value", "equal_categories", "full_credit"])
    invalidate = commands.add_parser(
        "revenue-invalidate", help="Require owner review after a business-definition change"
    )
    invalidate.add_argument("rule_id")
    for command in (register, confirm, resolve, invalidate):
        command.add_argument("--owner", required=True)
        command.add_argument("--evidence", required=True)
    commands.add_parser(
        "revenue-history", help="Inspect versioned definitions and the local decision log"
    )
    protect = commands.add_parser(
        "query-register", help="Approve a supported query against a confirmed revenue rule"
    )
    protect.add_argument("query_id")
    protect.add_argument("request", type=Path)
    protect.add_argument("sql", type=Path)
    protect.add_argument("--consumer", action="append", default=[])
    protect.add_argument("--replace", action="store_true")
    change = commands.add_parser(
        "query-review", help="Review proposed SQL against a registered metric query"
    )
    change.add_argument("query_id")
    change.add_argument("sql", type=Path)
    decide = commands.add_parser(
        "query-decide", help="Record an explicit owner decision on a SQL change"
    )
    decide.add_argument("review_id")
    decide.add_argument(
        "decision", choices=["keep_definition", "reject_change", "approve_candidate"]
    )
    for command in (protect, decide):
        command.add_argument("--owner", required=True)
        command.add_argument("--evidence", required=True)
    export_query = commands.add_parser(
        "query-export", help="Export a portable regression contract from a current approval"
    )
    export_query.add_argument("query_id")
    export_query.add_argument(
        "--output", type=Path, help="Create a NEW file; never overwrite an existing contract"
    )
    impact = commands.add_parser(
        "query-impacts", help="List registered queries and declared consumers affected by a table"
    )
    impact.add_argument("--table")
    commands.add_parser("query-history")
    regression = commands.add_parser(
        "check-regression",
        help="Offline CI gate using a trusted regression contract; no metadata DB required",
    )
    regression.add_argument("contract", type=Path)
    regression.add_argument("sql", type=Path)
    return root


def revenue_command(args, store):
    from lakecontext.revenue import RevenueModel, demo_snapshot
    from lakecontext.semantics import SemanticService

    semantics = SemanticService(store, args.max_age_hours)
    if args.command == "revenue-demo":
        store.ingest(demo_snapshot())
        return semantics.register(
            RevenueModel(),
            "demo scaffold",
            "Synthetic schema and explicit net merchandise basis; no allocation choice confirmed",
        )
    if args.command == "revenue-register":
        return semantics.register(json.loads(args.file.read_text()), args.owner, args.evidence)
    if args.command == "revenue-plan":
        return semantics.plan(json.loads(args.file.read_text()), args.review_definition)
    if args.command == "revenue-confirm":
        return semantics.confirm(args.question_id, args.strategy, args.owner, args.evidence)
    if args.command == "revenue-resolve":
        return semantics.resolve(args.conflict_id, args.strategy, args.owner, args.evidence)
    if args.command == "revenue-invalidate":
        return semantics.invalidate(args.rule_id, args.owner, args.evidence)
    return semantics.history()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            from lakecontext.server import create_server

            create_server(args.db, args.max_age_hours).run(transport="stdio")
            return
        if args.command == "check-regression":
            from lakecontext.querycheck import check_regression

            result = check_regression(
                json.loads(args.contract.read_text()), _read_query_sql(args.sql)
            )
            print(json.dumps(result, indent=2))
            _gate(result)
            return
        store = Store(args.db)
        service = ContextService(store, args.max_age_hours)
        if args.command.startswith("revenue-"):
            result = revenue_command(args, store)
        elif args.command.startswith("query-"):
            result = query_command(args, store)
        elif args.command in {"demo", "ingest", "sync-databricks"}:
            if args.command == "demo":
                from lakecontext.demo import demo_snapshot

                snapshot = demo_snapshot()
            elif args.command == "ingest":
                snapshot = Snapshot.model_validate_json(args.file.read_text())
            else:
                from databricks.sdk import WorkspaceClient

                from lakecontext.databricks import collect

                workspace = (
                    WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
                )
                snapshot = collect(workspace, args.catalog, args.schema)
            graph = store.ingest(snapshot)
            result = {
                "tables": len(graph["tables"]),
                "relationships": len(graph["relationships"]),
                "warnings": graph["warnings"],
                "captured_at": graph["captured_at"],
            }
        elif args.command == "search":
            result = service.search(args.query, args.limit)
        elif args.command == "describe":
            result = service.table(args.table)
        elif args.command == "context":
            result = service.context(args.question, args.limit, args.include_proposed)
        elif args.command == "relationships":
            result = service.relationships(args.table, args.include_proposed)
        elif args.command == "review":
            result = store.review(args.id, args.status, args.reviewer, args.reason)
        elif args.command == "audit-sql":
            claims = json.loads(args.assumptions.read_text()) if args.assumptions else None
            result = service.audit_sql(args.file.read_text(), claims)
        elif args.command == "repair-sql":
            result = service.repair_sql(args.file.read_text(), args.intent)
            if args.sql_only:
                repaired = result["repair"]
                if repaired["status"] != "repaired":
                    raise ValueError(
                        "No validated repair available. Select --intent or inspect the JSON response."
                    )
                print(repaired["corrected_sql"])
                return
        else:
            result = store.read()
        print(json.dumps(result, indent=2))
        if args.command in {"query-register", "query-review"}:
            _gate(result)
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; never print SDK credential details
        # SDK authentication failures may contain sensitive configuration details.
        if args.command == "sync-databricks":
            print(
                f"Databricks sync failed ({type(exc).__name__}). Check authentication, scope and metadata permissions. Existing snapshot was not replaced.",
                file=sys.stderr,
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def _gate(result):
    code = {
        "blocked": 1,
        "registration_refused": 1,
        "review_required": 2,
        "needs_reapproval": 2,
        "unsupported": 3,
    }.get(result["status"], 0)
    if code:
        raise SystemExit(code)


def _read_query_sql(path):
    from lakecontext.querycheck import MAX_SQL

    with path.open() as source:
        return source.read(MAX_SQL + 1)


def query_command(args, store):
    from lakecontext.change_review import ChangeReviewService

    service = ChangeReviewService(store, args.max_age_hours)
    if args.command == "query-register":
        return service.register(
            args.query_id,
            json.loads(args.request.read_text()),
            _read_query_sql(args.sql),
            args.owner,
            args.evidence,
            args.consumer,
            args.replace,
        )
    if args.command == "query-review":
        return service.review(args.query_id, _read_query_sql(args.sql))
    if args.command == "query-decide":
        return service.decide(args.review_id, args.decision, args.owner, args.evidence)
    if args.command == "query-impacts":
        return service.impacts(args.table)
    if args.command == "query-export":
        result = service.export(args.query_id)
        if args.output:
            with open(
                args.output, "x", opener=lambda path, flags: os.open(path, flags, 0o600)
            ) as output:
                output.write(json.dumps(result, indent=2) + "\n")
            return {"status": "exported", "path": str(args.output.resolve())}
        return result
    return service.history()


if __name__ == "__main__":
    main()
