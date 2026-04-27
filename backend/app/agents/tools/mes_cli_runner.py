"""MES CLI 调用封装 — 通过 subprocess 调用 mes-cli 获取 MES 真实数据

Token 传递优先级:
  1. set_token() 动态设置（来自前端请求 Authorization header）
  2. 环境变量 MES_TOKEN（测试/调试用）
  3. mes-cli 自身 appsettings.json 中的 Token 配置
"""
import subprocess
import json
import os
from contextvars import ContextVar
from typing import Any, Dict, Optional
from loguru import logger

_MES_CLI_PATH = os.getenv("MES_CLI_PATH", "mes-cli")

# 每个请求独立的 token（contextvars 保证 async 安全）
_mes_token: ContextVar[Optional[str]] = ContextVar("mes_token", default=None)

# 跟踪当前请求的数据来源（contextvars 保证 async 安全）
_data_source_status: ContextVar[str] = ContextVar("data_source", default="mock")

# 错误分类状态（contextvars 保证 async 安全）
_last_error_class: ContextVar[Optional[str]] = ContextVar("last_error_class", default=None)


def set_token(token: Optional[str]):
    """设置当前请求的 MES token（在 API 层调用，从 Authorization header 提取）"""
    _mes_token.set(token)


def set_data_source(source: str):
    """设置当前请求的数据来源状态"""
    _data_source_status.set(source)


def get_data_source() -> str:
    """获取当前请求的数据来源状态 — 'mes' 或 'mock'"""
    return _data_source_status.get()


def _build_args(command: list) -> list:
    """构建 dotnet CLI 参数列表"""
    cli_path = _MES_CLI_PATH
    if cli_path.endswith(".exe") or cli_path.endswith(".dll"):
        return ["dotnet", cli_path] + command
    return [cli_path] + command


def _get_token() -> Optional[str]:
    """获取当前有效的 MES token"""
    token = _mes_token.get()
    if token:
        return token
    return os.getenv("MES_TOKEN")


def run_cli(command: list, timeout: int = 15) -> Dict[str, Any]:
    """调用 MES CLI 并返回解析结果

    Args:
        command: CLI 命令段，如 ["schedule", "query", "--line", "SMT-01"]
        timeout: 超时秒数（默认 15s）

    Returns:
        {"success": True, "data": ...} 或 {"success": False, "error": "..."}
    """
    from app.agents.error_handler import classify_error, get_circuit_breaker, ErrorClass

    cb = get_circuit_breaker("mes_cli")
    if not cb.allow_request():
        return {"success": False, "error": "MES CLI 已熔断，请稍后重试", "circuit_open": True}

    try:
        args = _build_args(command)
        logger.debug(f"[MES CLI] 执行: {' '.join(args)}")

        env = os.environ.copy()
        token = _get_token()
        if token:
            env["MES_TOKEN"] = token

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        if result.returncode != 0:
            error_text = result.stderr.strip() or f"退出码: {result.returncode}"
            error_class = classify_error(IOError(error_text), result.stderr)
            _last_error_class.set(error_class.value)
            cb.record_failure()
            logger.error(f"[MES CLI] 命令失败 [{error_class.value}]: {error_text}")
            return {"success": False, "error": error_text, "error_class": error_class.value}

        output = result.stdout.strip()
        if not output:
            cb.record_failure()
            return {"success": False, "error": "CLI 无输出", "error_class": ErrorClass.DATA.value}

        parsed = json.loads(output)
        cb.record_success()
        return parsed

    except subprocess.TimeoutExpired:
        cb.record_failure()
        _last_error_class.set(ErrorClass.TIMEOUT.value)
        logger.error(f"[MES CLI] 超时 ({timeout}s): {' '.join(command)}")
        return {"success": False, "error": f"CLI 调用超时 ({timeout}s)", "error_class": ErrorClass.TIMEOUT.value}
    except FileNotFoundError:
        cb.record_failure()
        _last_error_class.set(ErrorClass.NETWORK.value)
        logger.error(f"[MES CLI] 可执行文件未找到: {_MES_CLI_PATH}")
        return {"success": False, "error": f"CLI 未找到: {_MES_CLI_PATH}", "error_class": ErrorClass.NETWORK.value}
    except json.JSONDecodeError as e:
        cb.record_failure()
        _last_error_class.set(ErrorClass.DATA.value)
        logger.error(f"[MES CLI] JSON 解析失败: {e}")
        return {"success": False, "error": f"CLI 输出解析失败: {e}", "error_class": ErrorClass.DATA.value}
    except Exception as e:
        cb.record_failure()
        cls = classify_error(e)
        _last_error_class.set(cls.value)
        logger.error(f"[MES CLI] 异常 [{cls.value}]: {e}")
        return {"success": False, "error": str(e), "error_class": cls.value}


def _unwrap_mes_response(data: Any) -> Any:
    """解包 MES API 标准响应格式，提取内层数据

    MES API 两种常见格式：
      1. {code: 0, msg: "success", count: N, data: [...]}
      2. {IsSuccess: true, Data: ...}
    """
    if isinstance(data, dict):
        if "code" in data and "data" in data:
            return data["data"]
        if "IsSuccess" in data and "Data" in data:
            return data["Data"]
    return data


def cli_or_mock(command: list, mock_value: Any, enabled: bool = False) -> Any:
    """CLI 或 mock 数据切换

    Args:
        command: CLI 命令（仅在 enabled=True 时调用）
        mock_value: mock 数据（enabled=False 时返回）
        enabled: 是否启用 CLI

    Returns:
        CLI 返回的 data 字段，或 mock 数据
    """
    from app.agents.error_handler import get_recovery_suggestion, ErrorClass

    if not enabled:
        _data_source_status.set("mock")
        return mock_value

    result = run_cli(command)
    if result.get("success"):
        _data_source_status.set("mes")
        raw_data = result.get("data", mock_value)
        return _unwrap_mes_response(raw_data)
    else:
        _data_source_status.set("mock_fallback")
        error_cls = result.get("error_class", "")
        error_text = result.get("error", "")

        if result.get("circuit_open"):
            logger.warning(f"[MES CLI] 熔断器开启，跳过 CLI 调用: {' '.join(command)}")
        else:
            hint = get_recovery_suggestion(ErrorClass(error_cls) if error_cls else ErrorClass.UNKNOWN)
            logger.warning(
                f"[MES CLI] 调用失败 [{error_cls}]，回退本地缓存: {' '.join(command)} — {error_text}\n"
                f"  恢复建议: {hint}"
            )
        return mock_value


def get_last_error_class() -> Optional[str]:
    """获取当前请求的最后一个错误分类"""
    return _last_error_class.get()


def get_error_recovery_hint() -> Optional[str]:
    """获取当前请求的错误恢复建议"""
    from app.agents.error_handler import get_recovery_suggestion, ErrorClass
    cls = _last_error_class.get()
    if cls:
        return get_recovery_suggestion(ErrorClass(cls))
    return None
