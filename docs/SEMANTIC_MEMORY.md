# Revenue meaning: ask once, reuse within scope

This is one implemented ambiguity case, not a general autonomous semantic-discovery engine. It extends Join Witness beyond row duplication: several SQL results can be structurally valid but represent different business decisions.

For one synthetic INR 1,000 order with INR 900 in Books and INR 100 in Games, the tool presents:

- Actual item amounts: Books 900, Games 100.
- Equal allocation across distinct categories: Books 500, Games 500.
- Full order credit per category: Books 1,000, Games 1,000. This attribution result is not additive across categories.
- None of these: do not save an allocation rule.

Nothing chooses an answer for the owner. The scripted story selects item value only to demonstrate the lifecycle and labels that approval as synthetic.

## Interactive run

Use a separate database from any existing demo or Databricks snapshot. Global flags precede the command.

```bash
uv run lakecontext --db .lakecontext/revenue.db revenue-demo
uv run lakecontext --db .lakecontext/revenue.db revenue-plan examples/revenue-request.json
```

Read the question, scope, options and example results. Copy the returned `question_id`. Only if item-value allocation is your intended demonstration answer:

```bash
uv run lakecontext --db .lakecontext/revenue.db revenue-confirm QUESTION_ID item_value \
  --owner 'your-name' --evidence 'Explain the approved allocation policy and its source'
uv run lakecontext --db .lakecontext/revenue.db revenue-plan examples/revenue-followup.json
uv run lakecontext --db .lakecontext/revenue.db revenue-history
```

The follow-up adds a January booked-date interval and region grouping. It returns `rule_reused: true`, the rule/version/evidence, generated SQL, preflight SQL, and replayable synthetic validation cases. It does not ask the same allocation question again. Alternatives are `equal_categories`, `full_credit`, or `none`.

To review an existing definition, plan with `--review-definition`. Confirming a different answer creates a conflict and blocks further SQL generation. Resolve it explicitly:

```bash
uv run lakecontext --db .lakecontext/revenue.db revenue-resolve CONFLICT_ID item_value \
  --owner 'metric-owner' --evidence 'Explain why this definition governs this metric'
```

This creates a new revision and retains the previous rule and conflicting proposal. A business-policy change that metadata cannot reveal requires explicit invalidation:

```bash
uv run lakecontext --db .lakecontext/revenue.db revenue-invalidate RULE_ID \
  --owner 'metric-owner' --evidence 'Policy changed; this definition requires review'
```

## Exact supported scope

The pilot supports a configured order/item model, INR integer minor units, paid booked merchandise net of line discounts, excluding tax and shipping, before refunds. Grouping is category or category plus region, with optional half-open booked-date intervals. A missing date on a paid INR order blocks preflight even when a date filter would otherwise exclude it.

An owner first supplies the explicit mapping of order ID, amount, status, currency, booked date, region, and item ID/order ID/category/net amount. `RevenueModel` in `src/lakecontext/revenue.py` defines the configuration format. Export its defaults as a starting point:

```bash
uv run python -c 'from lakecontext.revenue import RevenueModel; print(RevenueModel().model_dump_json(indent=2))'
```

Save and edit that JSON, then register it against a metadata database using `revenue-register MODEL.json --owner NAME --evidence REASON`. Registration validates referenced columns and types, not the business accuracy of the mapping. Current types are BIGINT identifiers/amounts, DATE booked date, and STRING dimensions/status/currency. No automatic mapping to Bakehouse is claimed: its available transaction schema is not this order/item model.

Rules are bound to the model, metric, definition version, currency, accounting basis, source identity and referenced metadata. Dates and the two supported groupings may vary. Recognized revenue, refunds, other currencies, arbitrary SQL and other dimensions are refused. Do not relabel a different accounting basis to force it through this template.

Equal allocation is across distinct categories, not item rows. It preserves minor units exactly by assigning residual units in binary lexical category order. Full-credit attribution deliberately does not preserve the sum across categories. All strategies require unique non-NULL IDs, nonnegative line amounts that reconcile to each eligible order, nonempty categories, and a bounded eligible total of at most 10^12 minor units.

## Execution and safety boundary

The output is a **conditional SQL plan**, not a verified production answer. Run its `preflight_sql` on the actual source and require every violation count to be zero before using its query. If checks error, time out, or return violations, stop. The tool does not enforce this on Databricks because it does not execute warehouse SQL.

Use the same consistent data snapshot for checks and query; otherwise source changes between them can invalidate the checks. Validate default binary collation and the supported types on the target runtime. Very large malformed inputs may cause preflight aggregates to overflow; errors are failures, not permission to continue. Warehouse cost, target-runtime execution, real permissions and real row quality have not been tested by this feature.

Generated Databricks SQL is parsed and transpiled to SQLite for local tests, then compared with independent Python allocation logic. Valid cases cover asymmetry, repeated categories, rounding, multiple regions, date boundaries, unpaid orders, another currency, empty and zero totals. Invalid cases must be blocked. These checks do not establish intended business meaning; only the owner can supply that.

Relevant column/type/description, table description, model mapping or linked term changes invalidate rules and outstanding questions on import. Identical metadata refreshes and unrelated columns preserve rules. Reverting a change does not revive an old approval. Undocumented business-policy changes are undetectable: owners must invalidate or version their definitions.

Questions expire after 24 hours. Metadata freshness defaults to 24 hours. An expired conflict receipt cannot be resolved; invalidate the active rule and request fresh review. Consumed and stale receipts cannot approve rules. Answering `none` during review retires the active rule and outstanding receipts; a subsequent request still asks for a supported definition rather than guessing a new one.

This is a trusted single-user local tool. Owner names are attribution, not authentication or RBAC. Someone with shell/database access can alter records. History is not tamper-proof. Receipts store request dates, synthetic examples, generated SQL, evidence and decisions; protect this database as sensitive metadata. MCP cannot approve a rule, but an agent separately granted shell access could invoke the CLI. Do not treat CLI-only approval as an authorization boundary against such an agent.

## What the question selector actually does

It enumerates four fixed synthetic order examples and three preconfigured meanings, maximizing distinguished interpretation pairs, then minimizing row count, then maximizing the illustrated difference. It selects a three-row example distinguishing all three choices. This is finite candidate selection—not learned question discovery, a global minimal counterexample, information gain under a probability model, or an estimate of production impact.

The story script exports the first question, approval, reused plan, unsupported-case refusal, conflict, resolution, schema invalidation and history into a new isolated run directory. Its approvals are scripted, not expert-reviewed. No expert-held-out or model-comparison benchmark has been completed. Follow the pilot protocol before claiming an advantage.
