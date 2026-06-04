"""Eval test case models."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EvalStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class FailReason(str, Enum):
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_ERROR = "tool_error"
    NO_RECORDS = "no_records"
    FIELD_MISSING = "field_missing"
    FIELD_VALUE_MISMATCH = "field_value_mismatch"
    RECORD_COUNT_MISMATCH = "record_count_mismatch"
    CONTENT_MISSING = "content_missing"
    UNEXPECTED_ERROR = "unexpected_error"


@dataclass
class FieldAssertion:
    field: str
    operator: str = "equals"  # equals | contains | regex | exists
    value: Any = None


@dataclass
class EvalExpectation:
    min_records: int = 0
    max_records: int = -1  # -1 = unlimited
    required_fields: list[str] = field(default_factory=list)
    field_values: dict[str, Any] = field(default_factory=dict)
    content_contains: list[str] = field(default_factory=list)
    content_not_contains: list[str] = field(default_factory=list)


@dataclass
class EvalCase:
    name: str
    description: str = ""
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    expect: EvalExpectation = field(default_factory=EvalExpectation)
    skip: bool = False
    skip_reason: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case: EvalCase
    status: EvalStatus = EvalStatus.SKIP
    duration_ms: float = 0.0
    record_count: int = 0
    response: str = ""
    records: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    error_message: str = ""


@dataclass
class EvalReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errored: int = 0
    duration_ms: float = 0.0
    results: list[EvalResult] = field(default_factory=list)
