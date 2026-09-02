# -*- coding: utf-8 -*-
"""活体对话框回归冒烟测试（打本机 9004 的 FC）。

作为统一循环重构前的「行为基线」：验证核心对话场景在重构后仍能
——查询直达工具、模糊写意图触发反问、排程触发写操作确认、复合分析正常出结果。

不要求精确匹配工具名（L2 语义路由有方差），断言「不崩溃 + 出现预期事件类别」。
服务不可用时跳过（`pytest -m live` 单独跑，或默认自动跳过）。
"""
import json
import time
import urllib.error
import urllib.request

import jwt
import pytest

BASE = "http://127.0.0.1:9004"
_WATCH_TYPES = {
    "tool_result", "route_match", "execution_done", "tool_start",
    "param_extract", "agent_info", "error", "data_source",
    "confirm_required", "done", "route_l2",
}


def _token():
    from app.core.config import settings
    return jwt.encode({"EmpCode": "admin", "exp": int(time.time()) + 3600},
                      settings.JWT_SECRET, algorithm="HS256")


def _server_up() -> bool:
    try:
        urllib.request.urlopen(BASE + "/health", timeout=3)
        return True
    except Exception:
        return False


def _chat(content, conv_id="regr-baseline"):
    """发消息并解析 SSE，返回 (events, content_text)。"""
    req = urllib.request.Request(
        BASE + "/api/messages/stream", method="POST",
        data=json.dumps({"conversation_id": conv_id, "content": content,
                         "enable_memory": False}).encode(),
        headers={"Authorization": f"Bearer {_token()}",
                 "Content-Type": "application/json"},
    )
    events = []
    parts = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            t = obj.get("type") or obj.get("__type")
            if t in _WATCH_TYPES:
                events.append((t, obj))
            if t == "content":
                parts.append(obj.get("content", ""))
            # 写操作确认 / 流程结束即停：confirm_required 后流会挂起等审批，
            # done 后无更多有效内容；避免读到连接超时。
            if t in ("confirm_required", "done"):
                break
    return events, "".join(parts)


@pytest.fixture(scope="module")
def live():
    if not _server_up():
        pytest.skip("FC 服务未启动（9004），跳过活体对话框回归")
    return _chat


@pytest.mark.live
class TestDialogRegression:
    def test_query_workorder_direct_tool(self, live):
        events, answer = live("查询工单 WO-001 的信息")
        types = {t for t, _ in events}
        assert "error" not in types, f"出现 error 事件: {events}"
        # 查询类应至少命中工具或产出内容
        assert ("tool_result" in types or "route_match" in types or answer.strip()), \
            f"查询未命中工具也无内容: {types}"

    def test_vague_create_intent_asks_back(self, live):
        events, answer = live("帮我创建一张单据")
        types = {t for t, _ in events}
        assert "error" not in types, f"出现 error 事件: {events}"
        done = [o for t, o in events if t == "done"]
        # 能力发现：反问 + 推荐清单，或 done 事件携带 unsupported 能力标记
        asked_back = "创建" in answer or "单据" in answer or any(
            o.get("unsupported") is True or o.get("capabilities") for o in done
        )
        assert asked_back, f"模糊写意图未触发反问/能力清单: answer={answer!r} types={types}"

    def test_auto_schedule_triggers_write_flow(self, live):
        events, answer = live("运行自动排程")
        types = {t for t, _ in events}
        assert "error" not in types, f"出现 error 事件: {events}"
        # 排程是写操作：应出现确认/工具执行，或回答里提到排程结果
        write_hit = ("confirm_required" in types or "tool_result" in types
                     or "tool_start" in types)
        mentions = any(k in answer for k in ("排程", "排产", "makespan", "工单"))
        assert write_hit or mentions, f"排程未触发写流程/结果: types={types} answer={answer!r}"

    def test_compound_analysis_produces_answer(self, live):
        events, answer = live("综合分析一下当前生产情况")
        types = {t for t, _ in events}
        assert "error" not in types, f"出现 error 事件: {events}"
        assert answer.strip(), "复合分析未产出内容"
