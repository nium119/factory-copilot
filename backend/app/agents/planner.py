"""Planning 模式 — 动态任务规划器"""
from typing import Dict, Any, Optional, List
from app.core.logger import log
from app.agents.settings import AVAILABLE_TASKS, FALLBACK_TASKS


def plan_tasks(message: str, wo_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """根据用户消息动态规划需要执行的任务"""
    matched_tasks = []

    # 检查是否触发"工单综合检查"
    wo_task = AVAILABLE_TASKS["work_order"]
    for kw in wo_task["keywords"]:
        if kw in message:
            for task_key, task_def in AVAILABLE_TASKS.items():
                if task_key != "work_order":
                    matched_tasks.append({
                        "key": task_key,
                        "name": task_def["name"],
                        "priority": task_def["priority"],
                        "wo_id": wo_id,
                    })
            matched_tasks.sort(key=lambda t: t["priority"])
            log.info(f"[Planner] 综合检查 -> 执行全部 {len(matched_tasks)} 个子任务")
            return matched_tasks

    # 根据关键词匹配需要的任务
    for task_key, task_def in AVAILABLE_TASKS.items():
        if task_key == "work_order":
            continue
        for kw in task_def["keywords"]:
            if kw in message:
                matched_tasks.append({
                    "key": task_key,
                    "name": task_def["name"],
                    "priority": task_def["priority"],
                    "wo_id": wo_id,
                })
                break

    # 无关键词但有工单号 → 默认物料+设备
    if not matched_tasks and wo_id:
        matched_tasks = [
            {"key": t["key"], "name": t["name"], "priority": t["priority"], "wo_id": wo_id}
            for t in FALLBACK_TASKS
        ]

    matched_tasks.sort(key=lambda t: t["priority"])
    log.info(f"[Planner] 匹配到 {len(matched_tasks)} 个任务: {[t['name'] for t in matched_tasks]}")
    return matched_tasks


async def execute_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个规划任务"""
    from app.agents.tools.production_prep_tools import (
        check_material_readiness, check_equipment_readiness, check_mold_readiness,
        query_quality_standard, query_sop, query_process_card,
    )

    task_key = task["key"]
    wo_id = task.get("wo_id")

    dispatch = {
        "material": lambda: check_material_readiness(wo_id),
        "equipment": lambda: check_equipment_readiness(wo_id),
        "mold": lambda: check_mold_readiness(wo_id),
        "quality": lambda: query_quality_standard(),
        "sop": lambda: query_sop(),
        "process_card": lambda: query_process_card(wo_id),
    }

    handler = dispatch.get(task_key)
    if not handler:
        log.warning(f"[Planner] 未知任务: {task_key}")
        return {"key": task_key, "status": "unknown", "data": None}

    try:
        data = await handler()
        return {"key": task_key, "name": task["name"], "status": "success", "data": data}
    except Exception as e:
        log.warning(f"[Planner] 任务 {task_key} 执行失败: {e}")
        return {"key": task_key, "name": task["name"], "status": "failed", "error": str(e)}
