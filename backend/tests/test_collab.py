# -*- coding: utf-8 -*-
"""阶段 E 单元测试：协作意图识别（多业务域协作的显式化决定）。"""
from app.agents.collab import collab_reason, is_collab_intent


class TestIsCollabIntent:
    def test_explicit_collab_keyword(self):
        assert is_collab_intent("综合分析一下当前生产情况") is True
        assert is_collab_intent("汇总所有设备的运行状况") is True

    def test_implicit_collab_keyword(self):
        assert is_collab_intent("今天生产线的运行状况怎么样") is True
        assert is_collab_intent("车间概览") is True

    def test_normal_query_not_collab(self):
        assert is_collab_intent("查询工单 WO-001") is False
        assert is_collab_intent("创建销售订单") is False

    def test_empty(self):
        assert is_collab_intent("") is False


class TestCollabReason:
    def test_explicit_reason(self):
        assert "综合分析" in collab_reason("综合分析生产情况")

    def test_implicit_reason(self):
        assert "生产线" in collab_reason("生产线当前状况")

    def test_empty_reason(self):
        assert collab_reason("查询工单") == ""
