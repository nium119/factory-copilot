"""Eval report generator — console output + JSON export."""

import json
from datetime import datetime, timezone

from .models import EvalReport, EvalResult, EvalStatus


def format_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms / 60000:.1f}m"


def _status_icon(status: EvalStatus) -> str:
    return {
        EvalStatus.PASS: "PASS",
        EvalStatus.FAIL: "FAIL",
        EvalStatus.SKIP: "SKIP",
        EvalStatus.ERROR: "ERR ",
    }.get(status, "????")


def _status_color(status: EvalStatus) -> str:
    return {
        EvalStatus.PASS: "\033[32m",
        EvalStatus.FAIL: "\033[31m",
        EvalStatus.SKIP: "\033[33m",
        EvalStatus.ERROR: "\033[35m",
    }.get(status, "")


_RESET = "\033[0m"


def print_result(result: EvalResult) -> None:
    icon = _status_icon(result.status)
    color = _status_color(result.status)
    dur = format_duration(result.duration_ms)
    print(f"  {color}{icon}{_RESET} {result.case.name:<50s} {dur:>6s}  ({result.record_count} records)")

    if result.failures:
        for f in result.failures:
            print(f"       \033[31m-> {f}\033[0m")
    if result.error_message:
        print(f"       \033[35m-> {result.error_message}\033[0m")
    if result.case.skip and result.case.skip_reason:
        print(f"       \033[33m-> {result.case.skip_reason}\033[0m")


def print_report(report: EvalReport) -> None:
    print()
    print("=" * 70)
    print("  Factory Copilot Eval Report")
    print("=" * 70)
    print(f"  Total: {report.total}  |  "
          f"\033[32mPASS: {report.passed}\033[0m  "
          f"\033[31mFAIL: {report.failed}\033[0m  "
          f"\033[33mSKIP: {report.skipped}\033[0m  "
          f"\033[35mERR: {report.errored}\033[0m  |  "
          f"Duration: {format_duration(report.duration_ms)}")
    print("-" * 70)

    # Group by tag
    by_tag: dict[str, list[EvalResult]] = {}
    for r in report.results:
        tags = r.case.tags or ["general"]
        for tag in tags:
            by_tag.setdefault(tag, []).append(r)

    for tag, results in sorted(by_tag.items()):
        print(f"\n  [{tag}]")
        for r in results:
            print_result(r)

    print()
    print("-" * 70)
    if report.failed == 0 and report.errored == 0:
        print("  Status: \033[32mALL PASS\033[0m")
    else:
        pct = report.passed / max(report.total - report.skipped, 1) * 100
        print(f"  Pass rate: {pct:.0f}% ({report.passed}/{report.total - report.skipped})")
    print()


def export_json(report: EvalReport, filepath: str) -> None:
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "errored": report.errored,
            "duration_ms": round(report.duration_ms, 1),
        },
        "results": [],
    }
    for r in report.results:
        data["results"].append({
            "name": r.case.name,
            "description": r.case.description,
            "tool": r.case.tool,
            "tags": r.case.tags,
            "status": r.status.value,
            "duration_ms": round(r.duration_ms, 1),
            "record_count": r.record_count,
            "failures": r.failures,
            "error_message": r.error_message,
            "skip_reason": r.case.skip_reason if r.case.skip else "",
        })

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
