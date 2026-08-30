# -*- coding: utf-8 -*-
"""反馈器：验证（确定性）+ 回滚（快照恢复）。

阶段 D「治理流水线 + 反馈闭环」的反馈层：写操作执行后确定性验证是否成功，
失败标记 needs_review（人工复核，不自动回滚——回滚是高风险破坏性操作，
由前端轨迹回滚 restore-entity 人工触发）；回滚逻辑抽成 rollback_entity 供复用。

原则：验证归确定性（程序判定写操作成功/失败），回滚默认人工触发（责任分离）。
"""
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import log

# 写操作类型（与 action_executor 的 actionType 对齐）
_WRITE_TYPES = ("create", "delete", "update", "write")


@dataclass
class VerifyResult:
    """写操作验证结果（确定性判定，留痕可审计）。"""

    ok: bool
    reason: str = ""
    needs_review: bool = False
    detail: dict = field(default_factory=dict)


class Reflector:
    """反馈器：验证写操作结果 + 轨迹回滚（快照恢复）。"""

    @staticmethod
    def verify_write_result(tool_result: dict) -> VerifyResult:
        """确定性验证写操作是否成功。

        - 规则拦截（source=rule_engine）→ 失败，需复核
        - 写操作影响 0 行 → 失败，需复核（可能参数不对 / 数据不存在）
        - 否则 → 通过
        """
        tool_result = tool_result or {}
        source = tool_result.get("source", "")
        action_type = tool_result.get("actionType", "")
        row_count = tool_result.get("rowCount", 0) or 0

        if source == "rule_engine":
            return VerifyResult(False, "规则拦截", True,
                                detail={"source": source, "rowCount": row_count})
        if action_type in _WRITE_TYPES and row_count <= 0:
            return VerifyResult(False, "写操作影响 0 行", True,
                                detail={"actionType": action_type, "rowCount": row_count})
        return VerifyResult(True, "验证通过",
                            detail={"actionType": action_type, "rowCount": row_count})

    @staticmethod
    async def rollback_entity(concept: str, is_create: bool, records: list,
                              created_entity_id: str = "", pk_name: str = "code") -> dict:
        """轨迹回滚（快照恢复，复用 restore-entity 的确定性逻辑）。

        - is_create=True  → 回滚 = 删除新建实体（按主键 / created_entity_id）
        - is_create=False → 回滚 = 恢复被删实体（用 before_snapshot 重新 create）
        返回 {ok, ...}。
        """
        from app.services.data_backend import data_backend

        records = records or []
        if is_create:
            deleted = 0
            for record in records:
                pk_val = record.get(pk_name) if isinstance(record, dict) else None
                if not pk_val and created_entity_id:
                    pk_val = created_entity_id
                if pk_val and await data_backend.delete(concept, pk_name, str(pk_val)):
                    deleted += 1
            log.info(f"[Reflector] 回滚删除新建实体 {concept}: {deleted}/{len(records)}")
            return {"ok": True, "concept": concept, "deleted": deleted, "total": len(records)}

        restored, failed = 0, 0
        for record in records:
            try:
                result = await data_backend.create(concept, record)
                if result and not result.get("error"):
                    restored += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        log.info(f"[Reflector] 回滚恢复被删实体 {concept}: 成功 {restored} 失败 {failed}")
        return {"ok": True, "concept": concept, "restored": restored,
                "failed": failed, "total": len(records)}


# 全局单例
reflector = Reflector()
