"""Baseline 保存 / 对比回归 — LLM-judge 分数与记录数的回归门禁。

baseline 记录每个 case 的 record_count 与 judge overall，对比时检测：
- judge overall 回退超过 threshold（默认 0.5）→ 回归
- 仅对比双方都有 judge 分数的 case（无 judge 的 case 跳过）
"""
import json
from datetime import datetime, timezone

from .models import EvalReport


def save_baseline(report: EvalReport, path: str) -> None:
    """保存当前评估结果为 baseline JSON（按 case name 索引）"""
    entries = {}
    for r in report.results:
        entries[r.case.name] = {
            "record_count": r.record_count,
            "judge_overall": r.judge.overall if r.judge else None,
        }
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def compare_baseline(report: EvalReport, path: str, threshold: float = 0.5) -> list[dict]:
    """与 baseline 对比，返回回退项列表 [{name, baseline, current}]。

    threshold 为 judge overall 允许的最大回退幅度（默认 0.5 分）。
    baseline 文件缺失 / 损坏时返回单个 error 标记项（不抛异常）。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return [{"name": "<baseline 文件>", "baseline": 0.0, "current": 0.0, "error": f"无法读取 {path}: {e}"}]

    entries = data.get("entries", {})
    regressions = []
    for r in report.results:
        base = entries.get(r.case.name)
        if base is None:
            continue
        cur = r.judge.overall if r.judge else None
        prev = base.get("judge_overall")
        if cur is None or prev is None:
            continue
        if prev - cur > threshold:
            regressions.append({"name": r.case.name, "baseline": prev, "current": cur})
    return regressions
