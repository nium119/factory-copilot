"""Cypher 校验、清洗与重试逻辑。

所有生成的 Cypher 查询的安全执行包装器。
"""

import re
import time
from typing import Any

from app.core.logger import log


def validate_and_sanitize(cypher: str, params: dict) -> tuple[str, dict, list[str]]:
    """校验生成的 Cypher 查询，返回 (清洗后语句, 参数, 告警列表)。"""
    warnings: list[str] = []

    # 1. 读查询强制 LIMIT
    upper = cypher.upper().strip()
    if upper.startswith("MATCH") and "RETURN" in upper:
        if "LIMIT" not in upper:
            cypher = cypher.rstrip() + " LIMIT 50"
            warnings.append("已添加缺省的 LIMIT 50")

        # 限制过大的 LIMIT
        limit_m = re.search(r"LIMIT\s+(\d+)", upper)
        if limit_m:
            limit_val = int(limit_m.group(1))
            if limit_val > 200:
                cypher = re.sub(r"LIMIT\s+\d+", "LIMIT 200", cypher, flags=re.IGNORECASE)
                warnings.append(f"LIMIT {limit_val} 已限制为 200")

    # 2. 检测无界变长路径
    if re.search(r"\[.*\*\d*\.\.\d*\]", cypher):
        m = re.search(r"\[.*(\*\d*\.\.)\d*\]", cypher)
        if m and ".." not in m.group(0):
            warnings.append("检测到无界变长路径模式")

    # 3. 检查参数引用完整性
    param_refs = set(re.findall(r"\$(\w+)", cypher))
    params_set = set(params.keys())
    for ref in param_refs - params_set:
        warnings.append(f"参数 '{ref}' 在查询中被引用但未提供")
    for p in params_set - param_refs:
        warnings.append(f"参数 '{p}' 已提供但查询中未使用")

    # 4. 拦截危险子句
    for keyword in ("DELETE", "DETACH DELETE", "REMOVE", "DROP"):
        if re.search(rf"\b{keyword}\b", upper):
            raise ValueError(f"检测到读查询中包含危险子句 '{keyword}'")

    return cypher, params, warnings


async def execute_with_retry(
    neo4j_service,
    cypher: str,
    params: dict,
    max_retries: int = 3,
) -> list[dict]:
    """执行 Cypher 查询，带校验和常见错误自动重试。"""
    cypher, params, warnings = validate_and_sanitize(cypher, params)
    for w in warnings:
        log.warning(f"[Cypher] {w}: {cypher[:120]}")

    log.info(f"[Cypher] 执行: {cypher[:200]} | 参数: {list(params.keys())}")
    t0 = time.perf_counter()

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            result = await neo4j_service.execute_read(cypher, params)
            elapsed = (time.perf_counter() - t0) * 1000
            log.info(f"[Cypher] 完成，耗时 {elapsed:.0f}ms，{len(result) if result else 0} 行")
            return result or []
        except Exception as e:
            last_error = e
            category = categorize_error(e)
            log.warning(f"[Cypher] 第 {attempt + 1}/{max_retries + 1} 次尝试失败 ({category}): {e}")

            if attempt >= max_retries:
                break

            fixed = _try_fix(cypher, params, e, category)
            if fixed:
                cypher, params = fixed
            elif 'locked' in str(e).lower():
                import asyncio
                delay = 0.5 * (attempt + 1)
                log.info(f"[Cypher] 锁冲突，等待 {delay}s 后重试...")
                await asyncio.sleep(delay)
                continue
            else:
                break  # 无法修复，不重试

    elapsed = (time.perf_counter() - t0) * 1000
    log.error(f"[Cypher] 所有尝试均失败，耗时 {elapsed:.0f}ms: {last_error}")
    raise last_error  # type: ignore[misc]


def _try_fix(cypher: str, params: dict, error: Exception, category: str) -> tuple[str, dict] | None:
    """尝试自动修复常见的 Cypher 错误。返回 (新语句, 新参数) 或 None。"""
    msg = str(error).lower()

    if category == "property_not_found":
        # 尝试从 Neo4j 错误消息中提取属性名
        m = re.search(r"'(n\.\w+)'", msg) or re.search(r"`(\w+)`", msg)
        if m:
            bad_prop = m.group(1).replace("n.", "")
            # 尝试小写
            fixed = cypher.replace(f"n.{bad_prop}", f"n.{bad_prop.lower()}")
            if fixed != cypher:
                log.info(f"[Cypher] 自动修复: 属性 '{bad_prop}' 改为小写")
                return fixed, params

    if category == "label_not_found":
        # 移除标签约束，按属性匹配查找所有节点
        m = re.search(r"MATCH \(n:(\w+)\)", cypher, re.IGNORECASE)
        if m:
            label = m.group(1)
            fixed = cypher.replace(f"MATCH (n:{label})", "MATCH (n)")
            log.info(f"[Cypher] 自动修复: 移除未知标签 '{label}'")
            return fixed, params

    return None


def categorize_error(error: Exception) -> str:
    """将 Neo4j/连接 错误归类。"""
    msg = str(error).lower()

    if "property" in msg and ("not found" in msg or "does not exist" in msg or "unknown" in msg):
        return "property_not_found"
    if "label" in msg and ("not found" in msg or "does not exist" in msg):
        return "label_not_found"
    if "syntax" in msg or "invalid" in msg or "unexpected" in msg:
        return "syntax"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "connection" in msg or "unavailable" in msg or "refused" in msg:
        return "connection"
    return "unknown"
