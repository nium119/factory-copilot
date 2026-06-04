"""Eval framework test — discovered by pytest, runs all eval cases.

Usage:
    pytest tests/test_eval.py -v              # all cases
    pytest tests/test_eval.py -v -k "smoke"   # smoke tests only
    pytest tests/test_eval.py -v -k "work_order"  # work_order cases
"""

import pytest

from tests.eval.loader import load_all_cases
from tests.eval.runner import eval_runner
from tests.eval.reporter import print_report, export_json


@pytest.mark.asyncio
async def test_eval_all():
    """Run all eval cases. Individual case failures don't stop the run."""
    cases = load_all_cases()
    if not cases:
        pytest.skip("No eval cases found")

    report = await eval_runner.run_all()
    print_report(report)
    export_json(report, "eval_report.json")

    # Assert individual results
    for result in report.results:
        if result.status.value == "error":
            pytest.fail(f"[{result.case.name}] ERROR: {result.error_message}")
        elif result.status.value == "fail":
            failures = "; ".join(result.failures)
            pytest.fail(f"[{result.case.name}] FAIL: {failures}")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", load_all_cases(), ids=lambda c: c.name)
async def test_eval_case(case):
    """Run each eval case as a separate pytest test."""
    if case.skip:
        pytest.skip(case.skip_reason)

    result = await eval_runner.run_case(case)
    if result.status.value == "error":
        pytest.fail(f"ERROR: {result.error_message}")
    elif result.status.value == "fail":
        failures = "; ".join(result.failures)
        pytest.fail(f"FAIL: {failures}")
