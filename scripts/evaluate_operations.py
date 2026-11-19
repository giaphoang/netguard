"""Evaluate P3 without actuators; optionally persist results in the dashboard journal."""

import argparse
import json
from pathlib import Path
from api.evaluation import assess_suite

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new result file")
    result = assess_suite(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))
    print(
        "Other checks:",
        sum(r["passed"] for r in result["results"] if "passed" in r),
        "/",
        sum("passed" in r for r in result["results"]),
    )
