# Repair supported metric queries

LakeContext 0.2 generates corrected Databricks SQL after the caller explicitly chooses which base rows should contribute. It validates the correction with synthetic data, returning replayable before/after cases. The repair does not need a uniqueness assertion and does not submit queries to Databricks.

## Run without a Databricks account

```bash
uv sync --locked
uv run lakecontext --db .lakecontext/repair-demo.db ingest examples/repair-commerce.json
uv run lakecontext --db .lakecontext/repair-demo.db repair-sql examples/repair-commerce.sql
```

Without `--intent`, the result is `needs_intent` and contains a clarification question and the supported choices. It contains no corrected SQL. Select the business meaning explicitly:

- `preserve_base`: sum every base-table row once. All joins can be removed because the supported projection references only the base measure and has no filters or grouping. This intentionally removes any INNER-join eligibility restriction.
- `preserve_eligibility`: sum each base-table row once if it would survive the original join tree. INNER joins and their ancestor paths become requirements. LEFT-only branches are optional and can be removed.
- `require_matches`: sum each base-table row once only if there is a coherent matching path through every joined table. This intentionally makes previously optional LEFT joins required.

```bash
uv run lakecontext --db .lakecontext/repair-demo.db repair-sql examples/repair-commerce.sql \
  --intent require_matches --sql-only
```

The correction uses a correlated EXISTS:

```sql
SELECT SUM(o.total_cents) AS revenue_cents
FROM demo.sales.orders AS o
WHERE EXISTS (
  SELECT 1 FROM demo.sales.reviews AS r
  WHERE o.customer_id = r.customer_id
)
```

Two reviews for an order's customer no longer multiply its contribution. With `preserve_base`, the correction is simply the base aggregation. Choosing between those meanings is a business decision; the tool does not guess it.

Omit `--sql-only` for the explanation, removed tables, input scope, context-bound repair ID and all synthetic validation cases. The same workflow is available through MCP as `repair_metric_sql(sql, intent)`. Restart an existing MCP server process after upgrading to expose the new tool.

## Bakehouse example

For the imported `samples.bakehouse` schema:

```bash
uv run lakecontext --db .lakecontext/bakehouse.db repair-sql examples/bakehouse-risk.sql \
  --intent require_matches --sql-only
```

The returned query sums `sales_transactions.totalPrice` only where a matching `media_customer_reviews.franchiseID` exists. On the synthetic counterexample, the original query returns 200 and the correction returns 100. These numbers are generated examples, not measured Bakehouse revenue, and no currency is inferred.

Export the SQL and a standalone replay:

```bash
uv run python scripts/export_repair_demo.py --intent require_matches
```

This creates `.lakecontext/repair-demo/corrected.sql` for Databricks, `validation.json` with the checks, and `replay.sql` for a new, empty SQLite database. No real rows or credentials are included. The script also accepts `--db`, `--sql`, and `--output` to use another supported example.

## How the repair preserves the chosen meaning

The rewrite operates on a validated tree of equijoins. For eligibility preservation, every INNER join and its ancestors are required; an INNER descendant can reject null-padded rows from a LEFT ancestor. Optional subtrees without INNER descendants do not determine whether a base row survives.

Each required child becomes an EXISTS scoped to its parent. Multiple dependent branches stay inside the same parent-row scope. This matters: one parent row matching branch A and a different parent row matching branch B do not jointly establish a valid match if the original query required both branches on one parent row.

The outer query scans the base table directly. Each eligible physical base row contributes once, including distinct rows with identical measure values or identical projected keys. It does not use `SUM(DISTINCT measure)`, arbitrary DISTINCT, or unverified primary-key assumptions. Empty input and all-NULL measures retain SQL's NULL SUM behavior.

This argument applies to the stated supported fragment and explicit intent. It does not establish the user's metric definition, warehouse runtime compatibility or performance.

## Validation

The input query is parsed but never executed. The corrected SQL is constructed as an AST and rendered for Databricks. For validation, the **delivered SQL** is parsed back, its tables are mapped to synthetic local tables, and it is executed in SQLite. Expected results come from a separate Python evaluator of bag semantics, not from a second generated SQL rewrite.

Each repair tests original audit witnesses plus empty tables, duplicate base and child rows, matched/unmatched rows, partial compound-key mismatches, NULL join keys, equal/negative/zero/NULL measures, and applicable incompatible-parent branch cases. Both SUM and eligible row counts are compared, so cancellation or NULL measures cannot hide an eligibility mismatch. A failed comparison withholds corrected SQL and returns `validation_failed`.

The automated suite also checks 6,084 data/intent combinations across four two-join patterns, including missing and duplicate keys and NULLs. Separate regressions cover branching, compound keys, projection aliases, refusal on unsupported input, stale metadata, CLI behavior and real MCP tool calls. These are synthetic behavioral checks, not a formal verification system or a production-data benchmark.

## Current boundaries

Input support remains one integer SUM on the base table, up to seven tree-shaped INNER/LEFT joins, exact-type integer/string equality keys, and an optional result alias. Filters, GROUP BY, decimal measures, multiple measures, allocation across dimensions, self-joins, CTEs, subqueries and arbitrary SQL are rejected. The repair can generate EXISTS even though EXISTS is not accepted as *input* to the auditor; do not feed the corrected query back into `audit-sql`. Its validation is attached to the repair response.

The original input's column names, result alias and fully qualified source tables are preserved with dialect-appropriate quoting. Assumptions from the auditor are not needed for repair. The correction changes join multiplication deliberately, so it is not equivalent to an original query whose intended measure is per joined row.

There is no remote execution, EXPLAIN, data profiling, row/column-policy evaluation or automatic replacement of saved Databricks queries. Review the selected intent, then use the returned SQL in your normal warehouse workflow. A future expansion must model filters and grouping explicitly rather than dropping them.

EXISTS-based repairs are established SQL techniques. This release makes the explain → intent → repair → synthetic-test workflow concrete; it does not establish a novel SQL transformation algorithm.
