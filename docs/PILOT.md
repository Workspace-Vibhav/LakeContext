# Paid pilot: trusted revenue definitions for analytics teams

## What we would sell

**Help your analytics team resolve a disputed revenue definition once and apply its approved meaning consistently to supported AI-generated queries.**

The buyer hypothesis is an analytics lead responsible for trusted reporting. The user is an analyst reviewing queries; the approver is the business owner of the metric. The pain to validate is recurring review and reconciliation work—not lack of a knowledge graph.

This is a proposed offer, not evidence of demand. The current software is a local, single-user prototype for one explicit accounting basis. It is not ready to promise broad production governance or autonomous execution.

## Qualify before onboarding

Ask an accessible analytics/data engineering team to walk through its most recent disagreement about a revenue report. Obtain a concrete example, the people involved, time spent, recurrence, impact and how the team currently resolves it. Do not introduce hypothetical AI failures as if they happened.

Proceed only if there is a recurring issue, a named metric owner, permission to use the case, a budget owner, and a decision date. Ask whether an existing dbt/Semantic Layer, Databricks metric view, documented SQL view or saved query would solve it more cheaply. If it would, do not manufacture a need for this product.

## A bounded two-week offer

Propose a fixed-fee, assisted evaluation for one team and one supported revenue metric. Agree scope and price in writing before onboarding; a paid invoice or signed budget commitment is stronger evidence than enthusiasm about a free demo. No customer has made that commitment yet. Do not sell unlimited metrics or production guarantees.

Include:

- Mapping and explicit approval of the metric's accounting basis and allocation policy.
- One explanation with contrasting synthetic results and a recorded owner decision.
- Supported follow-up query plans reusing that decision, with conditional SQL and preflight checks.
- Review of conflicting definitions and schema-change behavior.
- A measured comparison with the team's existing workflow and a handoff of the decisions and evidence.

Exclude automatic warehouse execution, production rollout, shared approval workflows, SSO/RBAC, arbitrary business-meaning discovery and general financial correctness. Real-data access or model calls need separate explicit permission, cost limits and handling terms. A customer can begin with sanitized schemas and synthetic fixtures; this limits what the pilot proves.

## Agree the evaluation before seeing results

Collect permissioned cases and have a qualified business owner record intended answers independently of tool outputs. Separate development cases from held-out cases; lock the split, labels, schema versions and scoring before final evaluation. Do not tune on the held-out set. The repository's generated tests are development regressions, not expert labels or market evidence.

Compare the team's normal process and a well-equipped coding assistant with the same schema, available context and opportunity to ask questions. Where the customer already has a configured semantic layer, include it and count its setup effort. Track both cold-start setup and later reuse; do not give only LakeContext the approved definition while calling the comparison fair.

Record for every case:

- Whether the result matches the independently confirmed meaning.
- Incorrect answers presented as safe; refusals and coverage separately.
- Number of clarification questions and repeated questions.
- Analyst review minutes, owner minutes, setup/maintenance minutes and time to resolution.
- Whether the chosen meaning transfers correctly to a follow-up query.
- Whether a conflict or relevant change blocks inappropriate reuse.

Use human review and a permitted target-runtime execution to check results where available. A successful synthetic replay alone is insufficient. Report raw counts, sample size and failures, not just a success percentage. A small pilot provides preliminary evidence, not a general benchmark claim.

Set buyer-agreed success thresholds before starting. For example, the buyer might require a meaningful reduction in median review time, no observed unsafe answers in the agreed cases, acceptable coverage and setup effort, and correctly blocked conflicts. The exact thresholds must reflect their workflow; zero observed errors in a small sample is not a zero-error guarantee.

## Decide whether there is a business

Estimate monthly value using observed cases per month × review minutes saved × the buyer's loaded cost per minute, less setup and maintenance. Keep speculative avoided-loss estimates separate. Ask the budget owner for a paid continuation at an explicit price; do not treat usage or compliments as equivalent.

Continue if the measured value, buyer commitment and supported scope justify the cost. Narrow or stop if disputes rarely recur, definitions are already solved by existing tools, setup costs outweigh savings, or nobody owns a budget for this problem.

Keep the MIT core usable. Potential future paid services include assisted onboarding and support; managed team approvals and authenticated controls are possible later products, **not shipped features**. Build them only after customers demonstrate demand. The defensibility hypothesis is trusted evaluations, integrations and adoption—not an unverified claim that nobody else can implement the algorithm.

## Suggested outreach

> We're testing a tool that helps analysts clarify ambiguous revenue definitions and reuse the approved meaning in AI-generated SQL. Do you have a recent report where two valid queries produced different business answers? I'd like to understand the review effort, then see whether a small paid pilot on one metric would be worthwhile. It currently supports one narrow revenue-allocation case, not arbitrary analytics.
