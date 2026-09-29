# SQL-change review: protect an approved metric

This implementation closes one local workflow: approved revenue definition → registered SQL → proposed change → synthetic discrepancy → explicit owner decision → replayable regression contract. It is not an autonomous warehouse auditor or a production authorization service.

## Try the complete demonstration

```bash
uv sync --locked
uv run python scripts/query_review_story.py
```

The script creates a new isolated run directory and prints its path. It uses no Databricks credentials or rows. All owner decisions are labelled SCRIPTED, not real approvals. It writes:

- `report.json`: the review, decision, CI results, affected declared reports and history.
- `approved.sql`: generated SQL preserving the approved meaning.
- `preflight.databricks.sql`: actual-source checks required before warehouse execution.
- `counterexample.sqlite.sql`: synthetic inserts, checks and candidate-query replay.
- `revenue.contract.json`: portable approval plus the versioned development fixture suite and expected results.

In this configured example, the approved rule includes only rows whose status is exactly `paid`, currency INR, allocated using item net amounts. The proposed query includes `refunded` status too. A synthetic refunded order contributes Books 90,000 and Games 10,000 minor units in the proposal; the approved rule excludes it. These are **invented test amounts**, not a measured loss. This case does not implement refund-accounting or revenue-recognition policy.

## Interactive workflow

First use [semantic memory](SEMANTIC_MEMORY.md) to register the synthetic revenue model and explicitly confirm its `item_value` meaning in a dedicated database. Then register the initial query:

```bash
uv run lakecontext --db .lakecontext/revenue.db query-register revenue-by-category \
  examples/revenue-request.json examples/revenue-approved.sql \
  --owner 'metric-owner' --evidence 'Explain why this query implements the approved metric' \
  --consumer 'Weekly sales report' --consumer 'Category dashboard'

uv run lakecontext --db .lakecontext/revenue.db query-review revenue-by-category \
  examples/revenue-proposed.sql
```

The second command intentionally exits **1** and emits a JSON review with a `review_id`, comparison, synthetic witness, suggested SQL plus prerequisite checks, and the allowed decisions. This is a finding, not a process crash. The query is not edited automatically.

After inspecting the evidence, an owner can explicitly choose:

```bash
uv run lakecontext --db .lakecontext/revenue.db query-decide REVIEW_ID keep_definition \
  --owner 'metric-owner' --evidence 'Retain the paid-status-only definition; reject refunded-status inclusion'
```

`keep_definition` approves the generated SQL that implements the existing meaning, increments the query revision, and returns that SQL and its regression contract. It does not silently redefine the metric. `reject_change` records the rejection and retains the existing approved SQL/revision. `approve_candidate` is allowed only when the candidate passes the fixture suite; the owner must still judge what the tests cannot establish. A failing candidate cannot be approved as a bypass. If the business meaning truly changed, confirm/version that definition separately, then explicitly re-register the query with `--replace`.

Export the current approval into a **new** file:

```bash
uv run lakecontext --db .lakecontext/revenue.db query-export revenue-by-category \
  --output revenue.contract.json
uv run lakecontext check-regression revenue.contract.json examples/revenue-proposed.sql
```

The exported file is created owner-readable/writable only (`0600`); an existing path is never overwritten. The regression still blocks the proposed SQL. Save the decision's approved SQL to your query file and run the same command against that file: it passes if its normalized SQL matches the exported approval and every fixture matches the definition. Comment/formatting-only changes do not require a fresh approval; more substantive rewrites conservatively do.

## Gate results

The CLI emits machine-readable JSON and uses these exit codes:

- **0:** unchanged approved SQL passed, or a normal administrative command succeeded.
- **1:** a discrepancy was found, baseline registration was refused, or an operational/input error occurred.
- **2:** changed SQL needs owner review, or the online query approval needs renewal.
- **3:** candidate SQL is outside the supported validation fragment.

Never treat nonzero as a warning-only success. In particular, “no counterexample” is not enough to pass a changed candidate. SQL with a different normalized hash must be approved and exported before the offline gate accepts it.

## CI integration and its trust boundary

[The GitHub Actions template](../examples/github-metric-check.yml) checks candidate SQL using the contract and checker from the PR's **base** revision. It only reads the SQL file from the PR head. It does not install or execute candidate repository code, post comments, or use credentials. Copy and configure the paths in your own repository; this project has not deployed or live-tested that integration.

Protect the workflow, checker, lockfile and contract with repository ownership/review rules, and make the check required. For production, pin actions to reviewed commit SHAs. Do not change it to use `pull_request_target` or give it secrets. Do not read the expected contract from the untrusted PR head: an author could otherwise update the expectations along with bad SQL. Missing contracts, unavailable files and unsupported checks fail rather than silently skipping.

For an intentionally approved change, an authorized maintainer must promote the newly approved contract through the protected workflow, then rerun the candidate against that trusted approval. This manual two-step promotion is conservative and can temporarily make the old SQL differ from the new contract; plan the promotion/merge together. Shared approval UI, signed approvals, transactional contract promotion, PR comment posting, dbt model discovery and dbt semantic-layer YAML export are **not** implemented.

An exported contract is an offline snapshot. It cannot discover a changed live schema, revoked permissions or a later semantic-rule decision. Refresh/export from a current local approval as part of your release process. A modified contract is not cryptographically authenticated: protection must come from the trusted repository and reviewers. Stored cases are checked against the versioned generator and independent expected logic to catch accidental edits, not to establish signature security.

## Impact and stale approvals

```bash
uv run lakecontext --db .lakecontext/revenue.db query-impacts --table demo.commerce.orders
uv run lakecontext --db .lakecontext/revenue.db query-history
```

Impacts include only explicitly registered queries and declared consumer labels. Dependencies cover the configured model columns, not inferred warehouse-wide lineage. Relevant metadata/definition changes, expired metadata and unresolved semantic conflicts block new reviews, decisions and exports until addressed. Unchanged refreshes preserve approval. Reverting a schema change does not revive a retired semantic rule. A review receipt expires after 24 hours and is single-use. An intervening query revision prevents an old receipt from deciding the new version.

`review_revenue_change` and `get_metric_impacts` expose review and impact lookup over MCP. Review may store a pending receipt. There is no MCP approval tool. As elsewhere in the project, owner strings are attribution—not authenticated identities—and an agent separately granted shell access could invoke the approval CLI. The SQL, evidence, dates and consumer labels retained in the local database and exports can be sensitive. Do not expose this service as a shared network endpoint.

## Supported validation scope

Queries must use only the mapped order/item tables and local, nonrecursive CTEs, output exactly the requested dimensions plus integer `revenue_minor`, and remain within the configured revenue scope. The checker accepts a conservative AST allowlist including SELECT, INNER/LEFT JOIN with ON, WHERE/HAVING, grouping, comparisons, bounded integer arithmetic, SUM/COUNT, and the CASE/window constructs needed for the three allocation strategies. It refuses writes, external table readers, unknown functions, recursion, unions, LIMIT and other unsupported syntax. Not all Databricks SQL is supported.

Input is limited to 16,000 characters, 700 AST nodes, six CTEs and six joins. Each candidate replay has a SQLite instruction budget and a result-row cap. An authorizer permits only reads of the two synthetic tables and a small function list; query-only mode is enabled. There is no model call, source-row read, uploaded data, attached database or extension loading. These checks are defensive limits, not a claim that this Python process is an OS-grade sandbox suitable for an untrusted public service.

The version-1 suite has 26 cases without date boundaries, up to 28 with both boundaries: asymmetric allocations, repeated categories, equal orders, integer rounding, large supported values, eligibility exclusions, regions including NULL, empty data, and deterministic generated cases. The witness is the smallest failing fixture **among these cases**, not a globally minimal counterexample. It is not an expert-reviewed held-out benchmark.

Databricks SQL is transpiled to SQLite for these replays. A passing replay does not establish target-runtime parity, complete SQL equivalence, business accuracy, or production data quality. Generated recommended SQL still requires actual-source preflight checks against the same consistent snapshot and supported collation/types before any warehouse execution. This version does not run those checks on Databricks.

## What this establishes

The tool can catch demonstrated changes, require a review decision, retain evidence, and reject the same failing change on later local runs. Whether it saves enough analyst time to justify payment must be measured in a permissioned customer pilot, alongside existing semantic layers and review tools. No claim of unique capability, production readiness or willingness to pay follows from these tests.
