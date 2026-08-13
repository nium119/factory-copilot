#!/usr/bin/env python
"""Standalone eval runner CLI — no pytest dependency.

Usage:
    python -m tests.eval.cli                                 # 全量（仅确定性断言）
    python -m tests.eval.cli --judge                         # 加 LLM-judge 质量评估
    python -m tests.eval.cli --tag smoke --judge             # 按 tag 过滤
    python -m tests.eval.cli --json report.json              # 导出 JSON 报告
    python -m tests.eval.cli --judge --save-baseline data/eval_baseline.json   # 保存基线
    python -m tests.eval.cli --judge --compare-baseline data/eval_baseline.json # 对比回归
"""

import argparse
import asyncio
import sys

from app.services.neo4j_service import neo4j_service

from .baseline import compare_baseline, save_baseline
from .reporter import export_json, print_report
from .runner import EvalRunner


async def main():
    parser = argparse.ArgumentParser(description="Factory Copilot Eval Runner")
    parser.add_argument("--tag", "-t", help="Filter cases by tag", default=None)
    parser.add_argument("--json", "-j", help="Export JSON report to file", default=None)
    parser.add_argument("--judge", action="store_true", help="启用 LLM-judge 质量评估")
    parser.add_argument("--judge-model", help="judge 使用的 LLM 模型名", default=None)
    parser.add_argument("--save-baseline", help="保存 baseline 到 JSON 文件", default=None)
    parser.add_argument("--compare-baseline", help="与 baseline 对比，检测回归", default=None)
    args = parser.parse_args()

    # Ensure Neo4j connected
    if not neo4j_service.connected:
        print("Connecting to Neo4j...")
        ok = await neo4j_service.connect()
        if not ok:
            print("ERROR: Neo4j unavailable — eval requires Neo4j")
            sys.exit(2)

    judge_note = " (LLM-judge)" if args.judge else ""
    print(f"Running eval cases{' [tag=' + args.tag + ']' if args.tag else ''}{judge_note}...")
    runner = EvalRunner(judge=args.judge, judge_model=args.judge_model)
    report = await runner.run_all(tag=args.tag)
    print_report(report)

    json_path = args.json or "eval_report.json"
    export_json(report, json_path)
    print(f"JSON report: {json_path}")

    # Baseline 保存 / 对比
    if args.save_baseline:
        save_baseline(report, args.save_baseline)
        print(f"Baseline saved: {args.save_baseline}")

    regressions = []
    if args.compare_baseline:
        regressions = compare_baseline(report, args.compare_baseline)
        if not regressions:
            print("\n[Regression] 无回退（对比 baseline）")
        else:
            print("\n[Regression] 检测到回退：")
            for rg in regressions:
                if "error" in rg:
                    print(f"  - {rg['error']}")
                else:
                    print(f"  - {rg['name']}: overall {rg['baseline']:.1f} -> {rg['current']:.1f}")

    if report.failed > 0 or report.errored > 0 or regressions:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
