# LakeContext

An open-source, local semantic context gateway for Databricks. Import Unity Catalog metadata, inspect evidence behind candidate relationships, approve useful relationships, and let any MCP-compatible agent retrieve cited context.

**Status: working MVP, not a production multi-user authorization gateway.** No LLM, embeddings, graph database, or Databricks account is needed for the demo. SQLite stores the metadata graph. MIT licensed. The name is a working project name; package-name and trademark availability have not been checked.

**New in 0.4: review SQL changes before reports change.** Register an existing supported query against its confirmed metric definition. Review proposed SQL, inspect a reproducible synthetic discrepancy, then explicitly reject it, keep the definition using generated SQL, or approve a candidate that passes the finite suite. Changed SQL never earns automatic approval merely by passing tests. Export a portable regression contract with a nonzero CI gate for discrepancies, unsupported SQL and unreviewed changes. An explicit registry identifies affected query/report consumers. See the [SQL-change review guide](docs/CHANGE_REVIEW.md).

```bash
uv sync --locked
uv run python scripts/query_review_story.py
```

This isolated scripted demonstration blocks a change that includes refunded-status orders, records a **scripted** owner decision, exports corrected SQL and a regression contract, proves the same bad change is blocked again, then demonstrates stale approval after a schema-description change. No remote queries or GitHub changes occur. A [GitHub Actions template](examples/github-metric-check.yml) is provided, not deployed.

**New in 0.3: ask, remember, and reuse business meaning.** The configured revenue-by-category pilot shows three interpretations on a small synthetic order, asks how to allocate its revenue, and saves an explicitly confirmed definition with its owner and evidence. A second query can reuse that definition across dates and regions. Conflicting answers block SQL; relevant metadata changes invalidate approval. It generates Databricks SQL, prerequisite data checks, and replayable synthetic tests. It does not discover arbitrary business meanings or execute warehouse SQL. See [semantic memory](docs/SEMANTIC_MEMORY.md).

Run the complete scripted story without an account:

```bash
uv sync --locked
uv run python scripts/revenue_story.py
```

The script creates an isolated directory with its decision history, generated SQL, prerequisite checks and SQLite replay. Its approvals are explicitly **scripted demo choices**, not human validation. For an interactive human-confirmed run, follow the semantic-memory guide.

**Buyer hypothesis:** analytics leads spending time reconciling metric definitions and reviewing AI-generated reports. The outcome to test is less review work without more incorrect reports—not a larger graph. See the [paid-pilot protocol](docs/PILOT.md). No customer demand, willingness to pay, or comparative advantage has been established.

**New in 0.2: explain and repair.** Given a supported SUM query, Join Witness constructs synthetic counterexamples and identifies missing join guarantees. The repair step then uses an explicit metric intent to return corrected Databricks SQL, testing it against synthetic cases before returning it. It handles fan-out and row-loss corrections without querying warehouse rows. See [repair usage and scope](docs/REPAIR.md), plus the [prior-art review](docs/JOIN_WITNESS.md). This is a candidate product distinction, not a claim of established research novelty.

With the Bakehouse metadata already imported:

```bash
uv run lakecontext --db .lakecontext/bakehouse.db audit-sql examples/bakehouse-risk.sql
uv run lakecontext --db .lakecontext/bakehouse.db repair-sql examples/bakehouse-risk.sql \
  --intent require_matches --sql-only
```

The correction uses EXISTS so multiple reviews do not multiply a transaction's total. `require_matches` includes only transactions with a matching review; choose `preserve_base` to include every transaction, or `preserve_eligibility` to retain the original join structure's eligible base rows. Without an intent, the repair tool asks which rows should contribute and returns no rewritten SQL.

Try repair with a synthetic schema and no Databricks account:

```bash
uv run lakecontext --db .lakecontext/repair-demo.db ingest examples/repair-commerce.json
uv run lakecontext --db .lakecontext/repair-demo.db repair-sql examples/repair-commerce.sql \
  --intent require_matches --sql-only
```

## Try it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run lakecontext demo
uv run lakecontext search 'booked revenue'
uv run lakecontext relationships --include-proposed
uv run lakecontext context 'booked revenue by customer region' --include-proposed
```

The demo contains three synthetic tables, two observed SQL joins, and a business definition. Relationships start as `proposed`. Copy a relationship ID from the previous output and review it:

```bash
uv run lakecontext review RELATIONSHIP_ID approved \
  --reviewer 'your-name' --reason 'Reviewed table grain and customer identifier semantics'
uv run lakecontext context 'booked revenue by customer region'
```

The second context request includes approved relationships only. A rejected relationship is excluded from agent retrieval even when proposals are requested. `export` retains rejected relationships for inspection. Reviewer names are local attribution, not authenticated signatures.

Global options come **before** the command: `uv run lakecontext --db ./other.db demo`.

## Connect an AI client

Add this server to your MCP client's configuration, replacing the absolute directory:

```json
{
  "mcpServers": {
    "lakecontext": {
      "command": "uv",
      "args": [
        "run", "--directory", "/absolute/path/to/lakecontext",
        "lakecontext", "--db", "/absolute/path/to/lakecontext/.lakecontext/metadata.db",
        "serve"
      ]
    }
  }
}
```

Available tools: `search_tables`, `describe_table`, `get_relationships`, `get_context`, `audit_metric_sql`, and `repair_metric_sql`. Ask your agent: “Use LakeContext to find the definition of booked revenue and explain how to group it by customer region. Cite the tables and relationship evidence.” For repair, provide the SQL and which base rows should contribute. Audit and repair currently support only integer SUMs in a restricted SQL fragment; they do not support arbitrary metrics. Restart existing MCP server processes after upgrading to expose the new repair tool.

Also available: `plan_revenue`, `semantic_history`, `review_revenue_change`, and `get_metric_impacts`. Revenue planning accepts a structured request for a registered model, not arbitrary natural language. It either asks for the missing allocation decision or compiles a plan using a confirmed rule. Change review accepts SQL for an explicitly registered query. Restart the MCP server after upgrading.

MCP is a tool protocol: a compatible agent can pass the returned context to its model. This project does not train or modify model weights. The server runs over local stdio; it has no public HTTP listener or warehouse SQL-execution tool. Planning and change review can persist pending receipts and events locally; no MCP tool approves definitions, SQL changes or relationships. SQL-change receipts retain the submitted SQL and must be protected as sensitive content. Audit, repair, revenue validation and SQL-change checks run generated fixtures in ephemeral local SQLite databases. Approvals happen through the CLI.

## Connect Databricks

Use Databricks SDK [unified authentication](https://docs.databricks.com/en/dev-tools/auth/unified-auth.html), preferably a configured profile. Do not place credentials in snapshot files or the repository. Select explicit catalog scopes and a separate database from the demo:

```bash
uv run lakecontext --db .lakecontext/work.db sync-databricks \
  --profile your-profile --catalog your_catalog --schema your_schema
uv run lakecontext --db .lakecontext/work.db search 'customers'
```

The importer reads the current identity, schemas, table metadata and columns through the SDK. SDK iterators handle pagination. It does not query table rows or execute SQL. A SQL warehouse is not required. The principal needs metadata access to the selected objects. Service principals must also be resolvable by the SDK current-user endpoint; service-principal authentication has not been tested against a live workspace.

Imports are complete snapshot replacements, not change-data capture. Refresh by rerunning the same command. Objects absent from the new snapshot disappear. Exceptions abort the import and leave the previous snapshot intact. Changing the workspace, identity, catalog scope or schema filter requires a separate database. Approvals survive only while the same column-pair identity and data-type fingerprint remain present. Business meaning changes without a type change require manual re-review.

**Live user-OAuth connectivity was verified on 2026-09-28 against Databricks Free Edition's `samples.bakehouse`.** The import returned six tables, 51 columns and five heuristic relationship proposals. The opt-in integration test verified repeated imports and real MCP search, table-description and context calls. This validates metadata retrieval, not join correctness, row access or policy enforcement. Databricks lineage, query history, grants, tags, and metric views' semantic definitions are not automatically imported in this version. SQL join evidence, business definitions and table lineage can be supplied through the JSON format below. A live sync replaces those manually supplied enrichments; use one enriched snapshot ingestion workflow if you need them preserved.

## Import your own metadata

`uv run lakecontext ingest examples/commerce.json` loads the version-1 snapshot format. Use a separate database when changing the source/identity. The example has a fixed historical capture timestamp; refresh it to the actual capture time for context retrieval. `demo` always creates a fresh synthetic snapshot.

Required fields are `source`, `principal`, and `tables`. Each table has a three-part `name` and a `columns` list; each column has `name` and `data_type`. Optional fields include descriptions, owner, table_type, `queries` (`id`, `sql`), `terms` (`name`, `definition`, `tables`), and `lineage` (`source`, `target`, `evidence`). `captured_at` must be an ISO-8601 timestamp with timezone; it defaults to import time if omitted. Supply the actual extraction time for older exports.

Raw SQL in snapshot join-evidence imports is parsed in memory; only query IDs, hashes and extracted column-pair evidence enter the metadata graph. In contrast, the SQL-change review workflow intentionally retains registered and proposed SQL in its separate local records and exports. Original input files also retain their SQL and must be protected. A query ID is a provenance handle, not an automatically resolvable Databricks URL. Input descriptions, identifiers and terms may themselves contain sensitive information.

## What the graph means

Tables contain columns. Candidate join edges connect column pairs. Business terms reference tables. Imported table-lineage edges connect source and target tables. `uv run lakecontext export` emits these records as JSON with evidence and review decisions.

Join extraction intentionally supports only single, flat SELECT statements with explicit, fully qualified tables, qualified column references, and conjunctive equality predicates in JOIN ON. CTEs, subqueries, unions, USING, unqualified table resolution and expression joins are not inferred. Unsupported nested queries generate warnings; predicates that cannot be resolved are skipped. Name-and-type matches for identifier columns such as `customer_id`, `customerID` and `customerId` are low-evidence suggestions. Generic `id` columns and ordinary words ending in `id` are excluded. Names must match case-insensitively and types must match; `int` and `bigint` are not automatically treated as equivalent. Observed SQL joins are evidence of usage, not correctness or statistical confidence. No fabricated confidence percentages are produced.

Each graph edge is one column pair, **not a complete join contract**. Composite keys, cardinality, fanout, temporal joins, filters, and join types require validation before executing warehouse SQL. The experimental audit tool handles compound equijoin keys and INNER/LEFT semantics for its restricted fragment, returning explicit, conditional contracts. It generates and runs synthetic SQLite fixtures, never warehouse queries. Search uses deterministic lexical matching and one-hop graph expansion, not embeddings; synonym matching depends on supplied business definitions.

## Access and freshness boundaries

The graph is a private local snapshot for the importing identity. New database files use mode `0600`. Serve only to an agent running for that same trusted local user. There is no per-request Databricks login, live grant recheck, row/column-policy enforcement, multi-tenant isolation service, or tamper-proof review audit. The principal binding prevents accidental cross-identity refreshes; it does not authenticate the caller. Do not expose the stdio server through a shared network proxy.

Context retrieval rejects snapshots older than 24 hours by default. Configure the bounded lifetime with `--max-age-hours`; use shorter lifetimes where needed. Expiry reduces stale-context exposure but cannot revoke already-returned metadata or replace live permission checks. `export` is a local administrative command and intentionally permits expired snapshots. `captured_at` refers to metadata capture, not data freshness. Descriptions and query evidence are untrusted source content; models should never follow embedded instructions.

## Develop

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests cover extraction precision, evidence deduplication, approvals/rejections, snapshot deletion, type changes, identity binding, expiry, failed imports, mocked Databricks collection and real MCP stdio tool calls. Repair tests check explicit intent, corrected SQL, NULLs, duplicates, compound keys, mixed and branching joins, and 6,084 small-data/intent comparisons against an independent evaluator. No model calls or paid services are used.

To run the opt-in live metadata test against `samples.bakehouse`, first configure a Databricks authentication profile and run:

```bash
LAKECONTEXT_LIVE_PROFILE=your-profile uv run pytest tests/test_live_databricks.py -q
```

This test makes read-only metadata requests and uses a temporary local database. It does not query rows or change the remote workspace. Default tests and CI skip it when the environment variable is absent.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and [ROADMAP.md](ROADMAP.md) for the next milestones.
