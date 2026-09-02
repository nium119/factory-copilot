"""Eval framework test — discovered by pytest, runs all eval cases.

评估套件走完整链路（Neo4j + LLM + 路由），属于质量评估而非回归单测：
默认跳过，需完整环境时显式开启——

    FC_EVAL=1 pytest tests/test_eval.py -v            # 全部用例
    FC_EVAL=1 pytest tests/test_eval.py -v -k smoke   # 冒烟子集
"""

import os

import pytest

from tests.eval.loader import load_all_cases
from tests.eval.runner import eval_runner
from tests.eval.reporter import print_report, export_json

# 门禁：未显式开启时整模块跳过（依赖活 Neo4j + LLM，环境不全会产生大量误报）
if not os.environ.get("FC_EVAL"):
    pytest.skip("评估套件需完整环境（Neo4j + LLM），设 FC_EVAL=1 显式运行", allow_module_level=True)



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
