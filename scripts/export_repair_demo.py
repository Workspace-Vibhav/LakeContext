"""Export corrected Databricks SQL and an independently replayable synthetic check."""

import argparse
import json
from pathlib import Path

from lakecontext.repair import INTENTS
from lakecontext.service import ContextService
from lakecontext.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=".lakecontext/bakehouse.db")
    parser.add_argument("--sql", type=Path, default=Path("examples/bakehouse-risk.sql"))
    parser.add_argument("--intent", required=True, choices=list(INTENTS))
    parser.add_argument("--output", type=Path, default=Path(".lakecontext/repair-demo"))
    args = parser.parse_args()
    result = ContextService(Store(args.db)).repair_sql(args.sql.read_text(), args.intent)["repair"]
    if result["status"] != "repaired":
        raise ValueError("No validated repair available")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "corrected.sql").write_text(result["corrected_sql"] + "\n")
    (args.output / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    cases = result["validation"]["cases"]
    example = next((c for c in cases if c["original_sum"] != c["repaired_sum"]), cases[0])
    (args.output / "replay.sql").write_text(
        "-- Synthetic data only. Execute in an empty SQLite database.\n"
        + example["fixture_sql"]
        + f"\n-- Original joined total: {example['original_sum']}\n"
        + example["original_sqlite_sql"]
        + f"\n-- Corrected total for {args.intent}: {example['repaired_sum']}\n"
        + example["repaired_sqlite_sql"]
        + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "intent": args.intent,
                "validation_cases": len(cases),
                "example_before": example["original_sum"],
                "example_after": example["repaired_sum"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
