#!/usr/bin/env python
"""Standalone eval runner CLI — no pytest dependency.

Usage:
    python -m tests.eval.cli                  # run all cases
    python -m tests.eval.cli --tag smoke      # run smoke tests only
    python -m tests.eval.cli --json report.json  # export JSON report
"""

import argparse
import asyncio
import sys

from app.services.neo4j_service import neo4j_service
from .runner import eval_runner
from .reporter import print_report, export_json


async def main():
    parser = argparse.ArgumentParser(description="Factory Copilot Eval Runner")
    parser.add_argument("--tag", "-t", help="Filter cases by tag", default=None)
    parser.add_argument("--json", "-j", help="Export JSON report to file", default=None)
    args = parser.parse_args()

    # Ensure Neo4j connected
    if not neo4j_service.connected:
        print("Connecting to Neo4j...")
        ok = await neo4j_service.connect()
        if not ok:
            print("ERROR: Neo4j unavailable — eval requires Neo4j")
            sys.exit(2)

    print(f"Running eval cases{' [tag=' + args.tag + ']' if args.tag else ''}...")
    report = await eval_runner.run_all(tag=args.tag)
    print_report(report)

    json_path = args.json or "eval_report.json"
    export_json(report, json_path)
    print(f"JSON report: {json_path}")

    if report.failed > 0 or report.errored > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
