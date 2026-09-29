# Roadmap

The current MVP establishes a testable local import → evidence → review → context workflow. A graph database, UI and additional connectors can be added when required by real use.

## Implemented in 0.4: SQL-change review and regression export

Explicitly register supported metric queries, review candidate SQL against the confirmed definition, inspect generated counterexamples, and record an owner decision. Export versioned synthetic regression contracts for an offline nonzero CI gate. Even candidates passing all fixtures require review when their normalized SQL changes. Registered consumer labels enable bounded impact reporting. MCP tools can request review and inspect impacts but cannot approve changes. The included GitHub template is not deployed; dbt model discovery and semantic-layer YAML export remain unimplemented. See [change-review scope and trust boundary](docs/CHANGE_REVIEW.md).

## Implemented in 0.3: one reusable business decision

The configured booked-revenue-by-category case selects a distinguishing synthetic example, asks an allocation question, persists an explicit owner answer, and reuses it across supported date/region queries. Generated SQL comes with preflight checks and synthetic replay. Conflicts block reuse; relevant dependency changes retire approvals. The current selector enumerates a fixed candidate family and is not a general ambiguity-discovery algorithm. See [scope and usage](docs/SEMANTIC_MEMORY.md).

## Next gate: a paid analytics-team pilot

Validate one recurring customer reporting problem, its metric owner, review cost and budget owner. Agree a bounded paid evaluation and compare against the team's existing process and a well-equipped coding assistant using permissioned, expert-reviewed held-out cases. No such customer evaluation has been completed. Do not expand into billing, hosted multi-user workflows or additional connectors before a demonstrated need. See [pilot protocol](docs/PILOT.md).

## Implemented in 0.2: intent-driven query repair

The `repair-sql` CLI command and `repair_metric_sql` MCP tool return corrected SQL for the supported integer-SUM fragment. Repairs either aggregate the base directly or use nested EXISTS to preserve the selected row eligibility. Every repair is replayed on synthetic cases against a separate bag-semantics evaluator. Filters, grouping, decimal measures and general query execution remain future work. See [repair semantics and usage](docs/REPAIR.md).

## Experimental differentiation: Join Witness

Implemented a restricted query-to-contract auditor that produces replayable synthetic counterexamples, deletion-minimal fixtures, specific clarification questions, and context-bound assumptions. It checks base integer-SUM preservation across tree-shaped INNER/LEFT equijoins without accessing warehouse rows. This includes NULL-sensitive row loss through mixed join chains. See [prior art and the evaluation plan](docs/JOIN_WITNESS.md); neither algorithmic novelty nor an improvement in agent outcomes has been established.

## Live validation started

User-OAuth metadata import, refresh and MCP retrieval passed against the Free Edition `samples.bakehouse` dataset on 2026-09-28. The test exposed and fixed missing support for identifier names such as `customerID`. The following broader checks remain:

- Run catalog imports with user and service-principal authentication; test pagination, views, inaccessible objects and revoked access.
- Add optional query-history and lineage ingestion using the relevant Databricks system tables, with explicit SQL warehouse configuration and restricted scopes.
- Persist human-authored enrichments separately from source snapshots with stale-definition detection.
- Evaluate retrieval against representative business questions and known correct joins. Measure table selection, invalid-join rate, missing definitions, context size and stale metadata.

## Before shared deployment

- Authenticate each request and bind it to a Databricks identity. Reauthorize metadata and any data access at the source; never approximate row/column policy enforcement from cached grants.
- Add permission-scoped caches, revocation tests, secure credential handling and an authenticated review audit.
- Model complete join contracts: composite keys, cardinality, temporal conditions, filters and aggregation fanout.
- Add resumable incremental synchronization, source cursors, deletion handling and bounded resource consumption.

## Later

- Review UI and graph exploration.
- REST API once shared authentication is designed.
- OpenMetadata/DataHub inputs, PostgreSQL and other warehouse adapters.
- Optional embedding retrieval, benchmarked against the lexical baseline.
- Metric definitions and read-only SQL planning with independent query validation.

The earlier market-gap discussion was a product hypothesis, not a verified claim of uniqueness. Validate demand and compare current alternatives before investing in a broad platform.
