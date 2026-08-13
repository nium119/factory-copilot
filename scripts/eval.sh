#!/usr/bin/env bash
# 评估脚本 — 运行 eval 用例（默认启用 LLM-judge 质量评估）
#
# 用法：
#   scripts/eval.sh                                  # 全量 + LLM-judge
#   scripts/eval.sh --tag smoke                      # 按 tag 过滤
#   scripts/eval.sh --judge --save-baseline data/eval_baseline.json   # 保存基线
#   scripts/eval.sh --judge --compare-baseline data/eval_baseline.json # 对比回归
#
# 仅跑确定性断言（不加 judge）：直接 `python -m tests.eval.cli`。
set -euo pipefail

cd "$(dirname "$0")/../backend"

python -m tests.eval.cli --judge "$@"
