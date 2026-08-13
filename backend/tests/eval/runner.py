"""Eval runner — execute test cases against ActionExecutor and assert results."""

import re
import time
from typing import Optional

from app.core.logger import log
from app.services.action_executor import ActionExecutor

from .judge import judge_response
from .loader import load_all_cases, load_cases_by_tag
from .models import (
    EvalCase,
    EvalExpectation,
    EvalReport,
    EvalResult,
    EvalStatus,
    FailReason,
)


class EvalRunner:
    def __init__(
        self,
        executor: Optional[ActionExecutor] = None,
        judge: bool = False,
        judge_model: Optional[str] = None,
    ):
        self._executor = executor or ActionExecutor()
        self._judge = judge
        self._judge_model = judge_model

    async def run_case(self, case: EvalCase) -> EvalResult:
        result = EvalResult(case=case)

        if case.skip:
            result.status = EvalStatus.SKIP
            return result

        start = time.perf_counter()

        try:
            result.response = await self._executor.execute(case.tool, case.arguments)
        except Exception as e:
            result.status = EvalStatus.ERROR
            result.error_message = str(e)
            result.failures.append(f"{FailReason.UNEXPECTED_ERROR.value}: {e}")
            result.duration_ms = (time.perf_counter() - start) * 1000
            return result

        result.duration_ms = (time.perf_counter() - start) * 1000

        # Parse structured result if available
        if case.tool.endswith("_query") or "找到" in result.response:
            result.records = self._parse_records(result.response)

        result.record_count = self._parse_record_count(result.response)
        self._assert_expectations(case.expect, result)
        await self._run_judge(case, result)
        return result

    def _parse_record_count(self, text: str) -> int:
        m = re.search(r"找到\s*(\d+)\s*(?:条|台|位|项|笔|个)", text)
        if m:
            return int(m.group(1))
        m = re.search(r"已创建|已记录|已更新", text)
        if m:
            return 1
        # Fallback: count pipe-separated data lines
        return 0

    def _parse_records(self, text: str) -> list[dict]:
        """Parse piped records from response text into list of dicts."""
        records = []
        in_header = False
        header_keys: list[str] = []

        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_header = True
                header_keys = [h.strip() for h in stripped[1:-1].split("|")]
                continue
            if in_header and "|" in stripped and not stripped.startswith("["):
                parts = [p.strip() for p in stripped.split("|")]
                if len(parts) == len(header_keys):
                    records.append(dict(zip(header_keys, parts)))

        return records

    def _assert_expectations(self, expect: EvalExpectation, result: EvalResult) -> None:
        failures: list[str] = []

        # Check record count
        if expect.min_records > 0 and result.record_count < expect.min_records:
            failures.append(
                f"{FailReason.RECORD_COUNT_MISMATCH.value}: "
                f"expected >= {expect.min_records}, got {result.record_count}"
            )
        if expect.max_records >= 0 and result.record_count > expect.max_records:
            failures.append(
                f"{FailReason.RECORD_COUNT_MISMATCH.value}: "
                f"expected <= {expect.max_records}, got {result.record_count}"
            )

        # Check required fields in parsed records
        for field in expect.required_fields:
            for i, rec in enumerate(result.records):
                if field not in rec:
                    failures.append(
                        f"{FailReason.FIELD_MISSING.value}: "
                        f"field '{field}' missing in record {i}"
                    )

        # Check field values
        for field, expected_val in expect.field_values.items():
            found = False
            for rec in result.records:
                actual = rec.get(field, "")
                if str(actual) == str(expected_val):
                    found = True
                    break
            if not found:
                # Also check raw response text
                if str(expected_val) not in result.response:
                    failures.append(
                        f"{FailReason.FIELD_VALUE_MISMATCH.value}: "
                        f"field '{field}'={expected_val!r} not found"
                    )

        # Check content contains
        for snippet in expect.content_contains:
            if snippet not in result.response:
                failures.append(
                    f"{FailReason.CONTENT_MISSING.value}: "
                    f"'{snippet}' not in response"
                )

        # Check content not contains
        for snippet in expect.content_not_contains:
            if snippet in result.response:
                failures.append(
                    f"{FailReason.CONTENT_MISSING.value}: "
                    f"'{snippet}' unexpectedly in response"
                )

        result.failures = failures
        if not failures:
            result.status = EvalStatus.PASS
        else:
            result.status = EvalStatus.FAIL

    async def _run_judge(self, case: EvalCase, result: EvalResult) -> None:
        """LLM-judge：评估响应质量，低于及格线判 fail（仅 judge 启用且配置了 judge 时）。

        与确定性断言互补：确定性断言保证「结构对」，judge 保证「语义质量达标」。
        judge 失败仅当 overall < min_score 时判 FAIL，不覆盖确定性断言的 FAIL。
        """
        if not (self._judge and case.expect.judge):
            return
        question = case.description or case.tool
        try:
            result.judge = await judge_response(
                question=question,
                response=result.response,
                config=case.expect.judge,
                model=self._judge_model,
            )
        except Exception as e:
            result.failures.append(f"judge 调用异常: {e}")
            if result.status == EvalStatus.PASS:
                result.status = EvalStatus.FAIL
            return
        overall = result.judge.overall
        if overall > 0 and overall < case.expect.judge.min_score:
            result.failures.append(
                f"{FailReason.JUDGE_SCORE_LOW.value}: "
                f"judge 总分 {overall:.1f} < 及格线 {case.expect.judge.min_score}"
            )
            if result.status == EvalStatus.PASS:
                result.status = EvalStatus.FAIL

    async def run_all(
        self, tag: Optional[str] = None, cases_dir: Optional[str] = None,
    ) -> EvalReport:
        from pathlib import Path

        d = Path(cases_dir) if cases_dir else None
        cases = load_cases_by_tag(tag, d) if tag else load_all_cases(d)

        if not cases:
            log.warning("[EvalRunner] no test cases found")

        # Ensure ontology is loaded from Neo4j
        try:
            from app.services.neo4j_service import neo4j_service
            from app.services.ontology_service import ontology_service
            if not neo4j_service.connected:
                await neo4j_service.connect()
            if not ontology_service.loaded:
                await ontology_service.load()
            self._executor.invalidate_cache()
        except Exception as e:
            log.warning(f"[EvalRunner] ontology init failed (fallback handlers will be used): {e}")

        report = EvalReport(total=len(cases))
        start = time.perf_counter()

        for case in cases:
            log.info(f"  [{case.tool}] {case.name}")
            result = await self.run_case(case)
            report.results.append(result)
            if result.status == EvalStatus.PASS:
                report.passed += 1
            elif result.status == EvalStatus.FAIL:
                report.failed += 1
            elif result.status == EvalStatus.SKIP:
                report.skipped += 1
            elif result.status == EvalStatus.ERROR:
                report.errored += 1

        report.duration_ms = (time.perf_counter() - start) * 1000
        return report

    def invalidate_cache(self):
        self._executor.invalidate_cache()


# Singleton
eval_runner = EvalRunner()
