"""A2A 示例 Agent — 外部能耗监测服务（FC 对接参考实现）

实现 A2A 标准协议核心子集：
- GET  /.well-known/agent-card.json   → Agent Card（能力发现）
- POST /tasks/send                     → 提交任务（JSON-RPC 2.0）
- POST /tasks/sendSubscribe            → SSE 流式进度
- GET  /tasks/get                      → 查询任务状态

启动：
    cd backend/examples
    python -m uvicorn a2a_demo_agent:app --host 0.0.0.0 --port 9100

FC 对接：
    系统配置 → 外部 Agent → 添加 {name, url=http://localhost:9100} → 连接 → 委托任务
"""
import asyncio
import json
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Energy Monitor A2A Agent")

AGENT_CARD = {
    "name": "energy-monitor-agent",
    "description": "外部能耗监测 Agent，提供产线实时能耗、碳排放数据及能耗报表",
    "url": "http://localhost:9100",
    "version": "1.0.0",
    "skills": [
        {
            "id": "query_energy",
            "name": "查询能耗",
            "description": "查询指定产线/工段的实时能耗和碳排放数据",
            "tags": ["energy", "carbon", "production"],
            "inputModes": ["text"],
            "outputModes": ["text"],
            "examples": ["查询 A3 产线今日能耗", "查询能耗最高的产线"],
        },
        {
            "id": "energy_report",
            "name": "能耗报表",
            "description": "生成能耗日报/周报/月报",
            "tags": ["report", "energy"],
            "inputModes": ["text"],
            "outputModes": ["text"],
            "examples": ["生成本周能耗周报", "本月碳排放报告"],
        },
    ],
    "endpoints": {
        "tasks/send": "/tasks/send",
        "tasks/sendSubscribe": "/tasks/sendSubscribe",
        "tasks/get": "/tasks/get",
        "tasks/cancel": "/tasks/cancel",
    },
}

# 内存任务存储（仅演示；真实 Agent 应持久化）
_tasks: dict = {}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_task(task_id: str, text: str, status: str = "completed", artifact: str = "") -> dict:
    return {
        "id": task_id,
        "sessionId": "",
        "status": status,
        "message": {"role": "user", "parts": [{"type": "text", "text": text}]},
        "artifacts": [{"type": "text", "data": artifact}] if artifact else [],
        "metadata": {"agent": AGENT_CARD["name"]},
        "createdAt": _now(),
        "updatedAt": _now(),
    }


def _handle_text(text: str) -> str:
    """模拟业务处理：按关键词返回能耗数据（真实 Agent 替换为真实查询）"""
    if any(k in text for k in ("报表", "周报", "月报", "日报", "报告")):
        return "[能耗报表] 本周总能耗 12,340 kWh，碳排放 7,890 kgCO2，较上周 ↓5.2%"
    # 简单关键词匹配产线
    import re
    line_match = re.search(r"([A-Z])\d?\s*产线", text)
    line = line_match.group(1) if line_match else "A3"
    return f"[能耗查询] {line} 产线实时功率 125 kW，今日累计 1,250 kWh，碳排放 785 kgCO2"


# ─────────────── Agent Card ───────────────

@app.get("/.well-known/agent-card.json")
async def get_agent_card():
    return AGENT_CARD


# ─────────────── 任务操作 ───────────────

@app.post("/tasks/send")
async def tasks_send(req: Request):
    body = await req.json()
    params = body.get("params", {}) or {}
    message = params.get("message", {}) or {}
    parts = message.get("parts", []) or []
    text = parts[0].get("text", "") if parts else ""
    session_id = params.get("sessionId", "")

    task_id = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    # 模拟处理延迟（真实 Agent 可改为真实异步查询）
    await asyncio.sleep(1.0)

    task = _make_task(task_id, text, status="completed", artifact=_handle_text(text))
    task["sessionId"] = session_id
    _tasks[task_id] = task
    return JSONResponse({"jsonrpc": "2.0", "id": body.get("id", 1), "result": task})


@app.post("/tasks/sendSubscribe")
async def tasks_send_subscribe(req: Request):
    body = await req.json()
    params = body.get("params", {}) or {}
    message = params.get("message", {}) or {}
    parts = message.get("parts", []) or []
    text = parts[0].get("text", "") if parts else ""
    session_id = params.get("sessionId", "")

    task_id = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

    async def event_stream():
        # 1. submitted → working
        working = _make_task(task_id, text, status="working")
        working["sessionId"] = session_id
        yield f"event: status-update\ndata: {json.dumps(working)}\n\n"
        await asyncio.sleep(0.5)

        # 2. working → completed（携带结果 artifact）
        done = _make_task(task_id, text, status="completed", artifact=_handle_text(text))
        done["sessionId"] = session_id
        _tasks[task_id] = done
        yield f"event: artifact-update\ndata: {json.dumps(done)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/tasks/get")
async def tasks_get(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        return JSONResponse({"jsonrpc": "2.0", "id": 0, "result": _make_task(task_id, "", status="failed", artifact="任务不存在")})
    return JSONResponse({"jsonrpc": "2.0", "id": 0, "result": task})


@app.post("/tasks/cancel")
async def tasks_cancel(req: Request):
    body = await req.json()
    params = body.get("params", {}) or {}
    task_id = params.get("id", "")
    existing = _tasks.get(task_id, _make_task(task_id, "", status="submitted"))
    existing["status"] = "canceled"
    existing["updatedAt"] = _now()
    _tasks[task_id] = existing
    return JSONResponse({"jsonrpc": "2.0", "id": body.get("id", 1), "result": existing})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9100)
