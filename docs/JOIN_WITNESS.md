# Join Witness: an experimental direction

Join Witness converts an aggregation query and a metadata snapshot into explicit join obligations, executable synthetic failure cases, and questions an AI agent can ask before relying on the query. It does not need warehouse row access or a model call.

The concrete feature is implemented. Research novelty and competitive advantage are **not established**.

Version 0.2 adds [intent-driven repair](REPAIR.md): corrected Databricks SQL plus synthetic validation for supported queries. It offers base-total preservation, original base-row eligibility preservation, or required matching across all joined tables. The caller selects the meaning; the tool does not silently infer it.

## A working example from Bakehouse

The imported schema contains `sales_transactions.totalPrice`, `sales_transactions.franchiseID`, and `media_customer_reviews.franchiseID`. Suppose the goal is to preserve total transaction value while enriching transactions with reviews:

```sql
SELECT SUM(t.totalPrice) AS revenue
FROM samples.bakehouse.sales_transactions AS t
LEFT JOIN samples.bakehouse.media_customer_reviews AS r
  ON t.franchiseID = r.franchiseID
```

Run the implemented auditor against the locally imported metadata:

```bash
uv run lakecontext --db .lakecontext/bakehouse.db audit-sql examples/bakehouse-risk.sql
```

For this query, it constructs two synthetic databases with the same modeled schema. One transaction worth 100 and one matching review yield 100. One transaction worth 100 and two matching reviews yield 200. Both are possible without a uniqueness guarantee on the review join key. This is a demonstration of uncertainty, not a finding that the actual Bakehouse data contains an incorrect total. No currency is inferred.

The returned question is specific: “Does each non-NULL `(franchiseID)` tuple identify at most one row in `samples.bakehouse.media_customer_reviews`?” If the answer is no, the proposed query does not generally preserve the base metric. A rewrite depends on the user's intent: filtering transactions by review existence, counting reviews, allocating revenue to reviews, and grouping by review attributes require different semantics. The tool deliberately does not silently choose a rewrite.

## The candidate contribution

The hypothesis is that an agent benefits from a query-specific contract-completion loop when business metadata is incomplete:

1. Turn a supported query into sufficient uniqueness and total-matching obligations.
2. For each missing obligation, construct a failing database that satisfies every other required obligation.
3. Replay both the base aggregation and the joined aggregation in SQLite.
4. Reduce the fixture until no single row can be removed without losing the failure or violating the other obligations.
5. Return a precise clarification question with the fixture and metadata citations.
6. Accept an explicit, evidence-attributed assumption to close that obligation, while keeping the verdict conditional.
7. Bind all assumptions to the query, source, principal, schema and capture timestamp. A refresh invalidates earlier assumption IDs, even if the schema looks unchanged.

This combines known techniques into a small agent-facing workflow. The possible research question is whether executable failure cases and obligation-specific questions improve an agent's decisions relative to schema context, generic warnings, or a semantic model supplied in advance. That improvement has not been measured.

## Prior art checked on 2026-09-28

**MetricFlow already has a semantic graph and fan-out restrictions.** Its [join documentation](https://docs.getdbt.com/docs/build/join-logic) explicitly describes choosing joins using entity types and preventing fan-out/chasm joins. We cannot claim novelty for semantic graph joins or static fan-out prevention.

**Looker already prevents aggregate double-counting.** Its [symmetric aggregates documentation](https://cloud.google.com/looker/docs/best-practices/understanding-symmetric-aggregates) explains how unique primary keys and declared join relationships preserve measures. We cannot claim novelty for detecting or fixing doubled totals.

**VeriEQL already generates SQL counterexamples under constraints.** The [project](https://github.com/VeriEQL/VeriEQL) and its [OOPSLA 2024 paper](https://doi.org/10.1145/3649849) describe bounded SQL equivalence verification with integrity constraints and counterexamples. Join Witness is not a replacement for that general verifier and cannot claim invention of counterexample-based SQL checking.

**Cosette already reasons about SQL equivalence.** Its [repository](https://github.com/uwdb/Cosette) documents equivalence solving and points to QED as a successor. That broader line of research must be included in any deeper novelty review.

These sources were directly read, not inferred from product names. This is a limited prior-art review, not an exhaustive search or a patent opinion. The reviewed documentation does not establish whether the exact proposed workflow is absent from those products or other systems. A general web-search attempt did not yield usable results; no broad search coverage is claimed. Further work should investigate constraint abduction, query repair, test-data generation, and interactive semantic-model construction.

## Supported fragment and semantics

The input must be one flat SELECT containing only `SUM(base_alias.integer_column)`, optionally aliased, with a fully qualified FROM table and at most seven INNER/LEFT joins. Each joined table connects through up to eight distinct column equality pairs to exactly one earlier table. Compound keys are treated as tuples. Join-key types must be identical integer or string types. Aliases and schemas are validated against the imported snapshot.

The fixed objective is preservation of SUM over **every base-table row**, with joins serving only as enrichment. It is not automatically the user's correct metric definition. Filters, grouping, expressions inside SUM, DISTINCT, decimal measures, multiple measures, self-joins, CTEs, subqueries, casts, non-equality predicates, OR, and more general join structures are rejected. Unsupported input never receives a safety verdict.

Uniqueness means no duplicate non-NULL child-key tuple. Total matching means every parent row has non-NULL join keys with at least one matching child. Every join needs uniqueness. Every INNER join and every ancestor on its path to the base needs total matching; this includes LEFT ancestors whose null-padded rows would be removed by an INNER descendant.

Under those stated global conditions, each join introduces at most one match, and all paths that could remove a base row have a match. Each base row therefore survives once in the supported query fragment. This is the reason for a conditional preservation verdict. Global conditions can be stronger than necessary on the subset of rows reachable from a particular base table. We do not claim to infer the weakest possible constraints.

Fixture minimization is deletion-minimal within a constructed finite witness, not globally minimum over all SQL databases. Source keys, constraints, nullability and semantic rules that the importer did not capture remain unknown. Fixtures contain only referenced columns. They are synthetic possibilities under that partial model.

## Agent interface and trust

The `audit_metric_sql` MCP tool returns the same results as the CLI. Each missing contract has a question, table citations, a context-bound ID and a replayable fixture. Supply assumptions as an array of `{"id": "current-contract-id", "evidence": "source of this claim"}` objects. Through the CLI, use `--assumptions path/to/claims.json`.

Assumptions are not authenticated human approvals or validated warehouse facts. Even with all assumptions supplied, the verdict is `preserved_under_stated_contracts`, not unconditional safety. A relationship being human-approved in the metadata graph does not establish uniqueness or total matching. Contracts are returned to the caller; this version does not persist them or silently promote them into the graph.

The auditor rejects arbitrary SQL operations and never executes the input SQL. It generates quoted SQL from its validated plan, populates an in-memory SQLite database with generated values, then runs only its own small SELECT statements. It does not use the Databricks SQL API. No real rows or credentials are embedded in fixtures.

## Validation and remaining work

Tests independently replay returned fixture SQL, cover duplicated and lost rows, NULL keys, mixed LEFT/INNER chains, composite keys, stale assumptions, approval independence, unsupported queries, and MCP tool calls. A bounded test enumerates 2,028 combinations of small key bags across four two-join patterns and checks metric preservation whenever all required contracts hold. This is a behavioral check, not a general proof or a performance benchmark.

The Bakehouse demonstration uses its actually imported schema but entirely synthetic rows. We have not queried the underlying tables to confirm cardinality, benchmarked against existing products, evaluated LLM behavior, or established research novelty. No paid/model calls are needed for this implementation.

Next experiments should compare baseline schema-only context, schema plus generic fan-out warnings, and witness-plus-question context on held-out tasks with explicit user metric intent. Measure false-safe decisions, unnecessary abstentions, clarification burden and correction success. Include ambiguous intent and intentionally filtering joins; merely testing obvious fan-out examples would overstate usefulness.
