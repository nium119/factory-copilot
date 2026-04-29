"""
ParallelExecutor — 通用并行执行器，从 collaborator.py 抽象而来

支持超时控制、部分结果降级、SSE 事件流
"""
import asyncio
import time
import json as _json
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, AsyncGenerator, Tuple
from loguru import logger
from app.core.resource_monitor import resource_monitor


@dataclass
class ParallelTask:
    """并行任务定义"""
    task_id: str
    agent_name: str
    display_name: str = ""
    query: str = ""
    timeout: float = 10.0              # 单任务超时（秒）


@dataclass
class ParallelResult:
    """单个任务执行结果"""
    task_id: str
    agent_name: str
    display_name: str = ""
    status: str = "pending"            # pending / running / success / timeout / error / empty
    data: Optional[str] = None
    error: Optional[str] = None
    elapsed: float = 0.0


@dataclass
class BatchResult:
    """并行批次执行结果"""
    batch_id: str = "default"
    results: List[ParallelResult] = field(default_factory=list)
    success_count: int = 0
    total_count: int = 0
    total_elapsed: float = 0.0

    @property
    def overall_status(self) -> str:
        if self.success_count == self.total_count and self.total_count > 0:
            return "complete"
        elif self.success_count > 0:
            return "partial"
        return "failed"


class ParallelExecutor:
    """通用并行执行器：并发执行多个 Agent 工具调用，支持超时和降级"""

    def __init__(self, default_timeout: float = 10.0, degrade_on_timeout: bool = True):
        self.default_timeout = default_timeout
        self.degrade_on_timeout = degrade_on_timeout
        self._agent_resolver: Optional[Callable] = None

    def set_agent_resolver(self, resolver: Callable):
        """设置 Agent 解析器，用于按名称获取 Agent 实例"""
        self._agent_resolver = resolver

    async def execute(
        self,
        tasks: List[ParallelTask],
        agent_resolver: Optional[Callable] = None,
    ) -> BatchResult:
        """并发执行任务列表，返回 BatchResult"""
        batch = BatchResult(total_count=len(tasks), batch_id=f"batch_{int(time.time()*1000)}")
        t0 = time.time()

        if not agent_resolver:
            logger.error("[ParallelExecutor] agent_resolver not set")
            return batch

        async def run_one(task: ParallelTask) -> ParallelResult:
            result = ParallelResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                display_name=task.display_name or task.agent_name,
                status="running",
            )
            start = time.time()
            try:
                agent = agent_resolver(task.agent_name)
                if agent is None:
                    result.status = "error"
                    result.error = f"Agent not found: {task.agent_name}"
                    return result

                raw = await asyncio.wait_for(
                    agent.call_tools(task.query),
                    timeout=task.timeout or self.default_timeout,
                )
                data = raw[0] if isinstance(raw, tuple) else raw
                result.elapsed = time.time() - start
                if data:
                    result.status = "success"
                    result.data = data
                else:
                    result.status = "empty"
                    result.data = None
            except asyncio.TimeoutError:
                result.status = "timeout"
                result.elapsed = time.time() - start
                result.error = f"超时 (>{task.timeout or self.default_timeout}s)"
                logger.warning(f"[ParallelExecutor] {task.agent_name} 超时")
            except Exception as e:
                result.status = "error"
                result.elapsed = time.time() - start
                result.error = str(e)
                logger.warning(f"[ParallelExecutor] {task.agent_name} 异常: {e}")

            return result

        max_conc = resource_monitor.get_max_concurrency()
        semaphore = asyncio.Semaphore(max(max_conc, 1))

        async def run_one_bounded(task: ParallelTask) -> ParallelResult:
            async with semaphore:
                return await run_one(task)

        coros = [run_one_bounded(t) for t in tasks]
        gathered = await asyncio.gather(*coros, return_exceptions=True)

        for item in gathered:
            if isinstance(item, Exception):
                logger.warning(f"[ParallelExecutor] gather exception: {item}")
                continue
            batch.results.append(item)
            if item.status == "success":
                batch.success_count += 1

        batch.total_elapsed = time.time() - t0
        logger.info(
            f"[ParallelExecutor] 批次完成: {batch.success_count}/{batch.total_count} "
            f"成功, 状态={batch.overall_status}, 耗时={batch.total_elapsed:.2f}s"
        )
        return batch

    async def execute_with_events(
        self,
        tasks: List[ParallelTask],
        agent_resolver: Optional[Callable] = None,
        batch_id: str = "",
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """
        并发执行并产出 SSE 事件流

        Yields:
            (event_type, json_content) — parallel_start / parallel_task / parallel_done
        """
        if not batch_id:
            batch_id = f"batch_{int(time.time()*1000)}"

        task_summary = [
            {"task_id": t.task_id, "agent_name": t.agent_name, "display_name": t.display_name or t.agent_name}
            for t in tasks
        ]
        yield ("parallel_start", _json.dumps({
            "batch_id": batch_id,
            "total": len(tasks),
            "tasks": task_summary,
        }, ensure_ascii=False))
        logger.info(f"[ParallelExecutor] parallel_start: {batch_id}, {len(tasks)} tasks")

        if not agent_resolver:
            yield ("error", "ParallelExecutor: agent_resolver not set")
            return

        async def run_one_with_event(task: ParallelTask) -> ParallelResult:
            result = ParallelResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                display_name=task.display_name or task.agent_name,
                status="running",
            )
            start = time.time()
            try:
                agent = agent_resolver(task.agent_name)
                if agent is None:
                    result.status = "error"
                    result.error = f"Agent not found: {task.agent_name}"
                    return result

                raw = await asyncio.wait_for(
                    agent.call_tools(task.query),
                    timeout=task.timeout or self.default_timeout,
                )
                data = raw[0] if isinstance(raw, tuple) else raw
                result.elapsed = time.time() - start
                if data:
                    result.status = "success"
                    result.data = data
                else:
                    result.status = "empty"
            except asyncio.TimeoutError:
                result.status = "timeout"
                result.elapsed = time.time() - start
                result.error = f"超时 (>{task.timeout or self.default_timeout}s)"
            except Exception as e:
                result.status = "error"
                result.elapsed = time.time() - start
                result.error = str(e)
            return result

        # 使用 as_completed 以逐个产出事件（前端可逐步更新）
        max_conc = resource_monitor.get_max_concurrency()
        semaphore = asyncio.Semaphore(max(max_conc, 1))

        async def run_one_bounded_event(task: ParallelTask) -> ParallelResult:
            async with semaphore:
                return await run_one_with_event(task)

        coros = [run_one_bounded_event(t) for t in tasks]
        completed = 0
        success_count = 0
        max_preview = 800

        for coro in asyncio.as_completed(coros):
            result = await coro
            completed += 1
            if result.status == "success":
                success_count += 1
                preview = result.data[:max_preview] + "..." if len(result.data or "") > max_preview else result.data
            else:
                preview = None

            event_data = {
                "batch_id": batch_id,
                "task_id": result.task_id,
                "agent_name": result.agent_name,
                "display_name": result.display_name,
                "status": result.status,
                "data": preview,
                "error": result.error,
                "elapsed": round(result.elapsed, 3),
                "completed": completed,
                "total": len(tasks),
            }
            yield ("parallel_task", _json.dumps(event_data, ensure_ascii=False))
            logger.info(
                f"[ParallelExecutor] parallel_task: {result.agent_name} "
                f"({result.status}) [{completed}/{len(tasks)}]"
            )

        yield ("parallel_done", _json.dumps({
            "batch_id": batch_id,
            "success": success_count,
            "total": len(tasks),
        }, ensure_ascii=False))
        logger.info(f"[ParallelExecutor] parallel_done: {batch_id}, {success_count}/{len(tasks)}")


# 全局单例
parallel_executor = ParallelExecutor()
