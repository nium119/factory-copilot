"""异常分类、指数退避重试、熔断器、恢复建议"""
import asyncio
import time
import re
from enum import Enum
from dataclasses import dataclass, field

from app.core.logger import log


class ErrorClass(str, Enum):
    """错误分类"""
    NETWORK = "network"         # 网络不可达、连接拒绝
    TIMEOUT = "timeout"         # 请求超时
    AUTH = "auth"               # 认证失败（401/403）
    DATA = "data"               # 数据格式错误、字段缺失
    RATE_LIMIT = "rate_limit"   # 限流（429）
    UNKNOWN = "unknown"         # 无法分类


# 错误分类规则：(pattern, ErrorClass)
_ERROR_PATTERNS = [
    (r"(timeout|timed.?out|超时)", ErrorClass.TIMEOUT),
    (r"(connection.?refused|connection.?reset|无法连接|拒绝连接|Name or service not known|No route to host)", ErrorClass.NETWORK),
    (r"(unauthorized|401|403|认证失败|token.*无效|token.*过期|未授权)", ErrorClass.AUTH),
    (r"(JSON.*解析|parse.*error|格式错误|字段.*缺失|Required|validation)", ErrorClass.DATA),
    (r"(429|rate.?limit|too many requests|限流)", ErrorClass.RATE_LIMIT),
]


def classify_error(error: Exception, stderr: str = "") -> ErrorClass:
    """根据异常类型和 stderr 输出分类错误"""
    error_text = str(error) + " " + stderr
    error_text_lower = error_text.lower()

    # 先检查异常类型
    if isinstance(error, asyncio.TimeoutError):
        return ErrorClass.TIMEOUT

    # 再检查文本模式
    for pattern, cls in _ERROR_PATTERNS:
        if re.search(pattern, error_text_lower):
            return cls

    return ErrorClass.UNKNOWN


@dataclass
class CircuitBreaker:
    """熔断器状态机

    三种状态: CLOSED(正常) → OPEN(熔断) → HALF_OPEN(半开)
    """
    failure_threshold: int = 5        # 连续失败 N 次后熔断
    cooldown_seconds: float = 30.0    # 熔断后冷却时间
    half_open_limit: int = 1          # 半开状态下允许的试探请求数

    _failures: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _state: str = field(default="CLOSED", init=False)  # CLOSED | OPEN | HALF_OPEN
    _half_open_count: int = field(default=0, init=False)

    @property
    def state(self) -> str:
        self._transition()
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"

    def _transition(self):
        """检查并执行状态转移"""
        now = time.time()
        if self._state == "OPEN" and (now - self._last_failure_time) > self.cooldown_seconds:
            self._state = "HALF_OPEN"
            self._half_open_count = 0
            log.info("[CircuitBreaker] OPEN → HALF_OPEN")
        elif self._state == "CLOSED" and self._failures >= self.failure_threshold:
            self._state = "OPEN"
            self._last_failure_time = now
            log.warning(f"[CircuitBreaker] CLOSED → OPEN (连续 {self._failures} 次失败)")

    def record_success(self):
        """记录一次成功调用"""
        if self._state == "HALF_OPEN":
            self._half_open_count += 1
            if self._half_open_count >= self.half_open_limit:
                self._state = "CLOSED"
                self._failures = 0
                log.info("[CircuitBreaker] HALF_OPEN → CLOSED (恢复)")
        else:
            self._failures = 0

    def record_failure(self):
        """记录一次失败调用"""
        self._failures += 1
        self._last_failure_time = time.time()
        if self._state == "HALF_OPEN":
            self._state = "OPEN"
            log.warning("[CircuitBreaker] HALF_OPEN → OPEN (试探失败)")

    def allow_request(self) -> bool:
        """当前是否允许发出请求"""
        self._transition()
        return self._state != "OPEN"


# 全局熔断器实例（按服务划分）
_circuit_breakers: dict = {}


def get_circuit_breaker(service: str = "mes_api") -> CircuitBreaker:
    """获取或创建服务熔断器"""
    if service not in _circuit_breakers:
        _circuit_breakers[service] = CircuitBreaker()
    return _circuit_breakers[service]


def backoff_delay(attempt: int, base: float = 0.5, max_delay: float = 8.0) -> float:
    """指数退避延迟计算: base * 2^attempt，上限 max_delay"""
    return min(base * (2 ** attempt), max_delay)


def get_recovery_suggestion(error_class: ErrorClass) -> str:
    """根据错误类型返回人类可读的恢复建议"""
    suggestions = {
        ErrorClass.NETWORK: "MES 服务网络不可达，请检查网络连接和 MES_API_BASE 配置",
        ErrorClass.TIMEOUT: "MES 服务响应超时，已自动重试。如持续超时，请联系运维检查 MES 服务器负载",
        ErrorClass.AUTH: "MES Token 认证失败，请重新登录获取有效 Token",
        ErrorClass.DATA: "MES 返回数据格式异常，已回退到本地缓存。请联系系统管理员检查 MES API 版本兼容性",
        ErrorClass.RATE_LIMIT: "MES 服务限流，请求已自动延迟重试",
        ErrorClass.UNKNOWN: "MES 服务异常，已自动回退到本地缓存数据",
    }
    return suggestions.get(error_class, suggestions[ErrorClass.UNKNOWN])
