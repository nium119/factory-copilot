"""Load eval test cases from YAML files."""

import os
from pathlib import Path
from typing import Optional

import yaml

from .models import EvalCase, EvalExpectation, JudgeConfig

_CASES_DIR = Path(__file__).parent / "cases"


def _dict_to_judge(d: Optional[dict]) -> Optional[JudgeConfig]:
    """解析 expect.judge 配置（camelCase + snake_case 兼容）"""
    if not d:
        return None
    return JudgeConfig(
        criteria=d.get("criteria", ["准确性", "完整性", "相关性", "可读性"]),
        reference=d.get("reference", ""),
        min_score=d.get("minScore", d.get("min_score", 3.0)),
        prompt=d.get("prompt", ""),
    )


def _dict_to_expectation(d: Optional[dict]) -> EvalExpectation:
    if not d:
        return EvalExpectation()
    return EvalExpectation(
        min_records=d.get("minRecords", d.get("min_records", 0)),
        max_records=d.get("maxRecords", d.get("max_records", -1)),
        required_fields=d.get("requiredFields", d.get("required_fields", [])),
        field_values=d.get("fieldValues", d.get("field_values", {})),
        content_contains=d.get("contentContains", d.get("content_contains", [])),
        content_not_contains=d.get("contentNotContains", d.get("content_not_contains", [])),
        judge=_dict_to_judge(d.get("judge")),
    )


def load_case_from_dict(data: dict) -> EvalCase:
    expect_raw = data.get("expect", {})
    return EvalCase(
        name=data.get("name", "unnamed"),
        description=data.get("description", ""),
        tool=data.get("tool", ""),
        arguments=data.get("arguments", {}),
        expect=_dict_to_expectation(expect_raw),
        skip=data.get("skip", False),
        skip_reason=data.get("skipReason", data.get("skip_reason", "")),
        tags=data.get("tags", []),
    )


def load_cases_from_file(path: Path) -> list[EvalCase]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return []
    if isinstance(data, list):
        return [load_case_from_dict(item) for item in data]
    if isinstance(data, dict) and "cases" in data:
        return [load_case_from_dict(item) for item in data["cases"]]
    return []


def load_all_cases(cases_dir: Optional[Path] = None) -> list[EvalCase]:
    d = cases_dir or _CASES_DIR
    if not d.exists():
        return []

    cases: list[EvalCase] = []
    for fname in sorted(os.listdir(d)):
        if fname.endswith((".yaml", ".yml")):
            cases.extend(load_cases_from_file(d / fname))
    return cases


def load_cases_by_tag(tag: str, cases_dir: Optional[Path] = None) -> list[EvalCase]:
    return [c for c in load_all_cases(cases_dir) if tag in c.tags]
