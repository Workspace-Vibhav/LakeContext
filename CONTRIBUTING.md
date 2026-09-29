# Contributing

Use Python 3.12+, install with `uv sync --locked`, and run the checks in the README before proposing a change. Keep fixtures synthetic and never commit credentials, local graph databases, real query text, or customer metadata.

New connectors should return a validated `Snapshot` and fail without replacing the stored graph when collection is incomplete. Add behavioral tests for successful imports and source failures. Changes to relationship inference need negative examples that prove unrelated columns are not treated as verified joins.

Keep retrieval read-only, preserve evidence, and clearly distinguish proposed and approved relationships. Authorization features must document the actual enforcement point and include denied-access tests. Avoid confidence percentages unless calibrated against an explicit evaluation set.

This repository has not yet been published. No external issue tracker or security reporting address is configured.
