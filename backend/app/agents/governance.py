# -*- coding: utf-8 -*-
"""六闸门治理流水线 + 审计报告。

阶段 D「治理流水线 + 反馈闭环」：把散落在 action_executor / _resolve_params /
_apply_write_gates / _inject_data_filters 的治理判定统一成一条可审计流水线。

六闸门：① 工具边界 ② 操作权限RBAC ③ 数据权限(行/列) ④ 业务规则constraint
        ⑤ 风险分级 ⑥ 审批门禁

当前实现 ① 工具边界 + ⑤ 风险分级 两个纯逻辑闸门（可单元测试）；
②③④⑥ 的接入点在此定义，判定逻辑仍由既有组件承担（逐步接线）。
"""
from dataclasses import dataclass, field
from typing import Optional

# 风险分级（单一数据源：tool_registry 也复用这里，避免两处重复）
RISK_BY_OUTPUT = {
    "query": "READ",
    "list": "READ",
    "similarity": "READ",
    "delete": "WRITE_APPROVE",
    "write": "WRITE_APPROVE",
    "create": "WRITE_APPROVE",
    "update": "WRITE_APPROVE",
    "schedule": "WRITE_APPROVE",  # 排程/插单是写操作
}

OUTCOME_PASS = "pass"
OUTCOME_BLOCK = "block"
OUTCOME_APPROVE = "approve"
OUTCOME_DELEGATE = "delegate"

# 六闸门名称（按执行顺序）
GATE_ORDER = ("tool_boundary", "rbac", "data_permission", "rule_constraint", "risk", "approval")


@dataclass
class GateResult:
    """单闸门判定结果（含审计留痕 detail）。"""

    gate: str
    outcome: str
    reason: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class GovernanceReport:
    """一次写操作的六闸门审计报告（可审计）。"""

    tool_name: str
    params: dict = field(default_factory=dict)
    results: list = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """任一闸门 block → 拦截，不可执行。"""
        return any(r.outcome == OUTCOME_BLOCK for r in self.results)

    @property
    def needs_approval(self) -> bool:
        """任一闸门 approve/delegate → 需人机确认或委托审批。"""
        return any(r.outcome in (OUTCOME_APPROVE, OUTCOME_DELEGATE) for r in self.results)

    def summary(self) -> str:
        """审计摘要（供日志/轨迹留痕）。"""
        if not self.results:
            return f"{self.tool_name}: 无闸门判定"
        parts = [f"{r.gate}={r.outcome}" + (f"({r.reason})" if r.reason else "")
                 for r in self.results]
        return f"{self.tool_name}: " + " → ".join(parts)


class GovernancePipeline:
    """六闸门治理流水线（确定性）。"""

    def __init__(self, ontology_service=None):
        self._os = ontology_service

    # ── ① 工具边界 ──

    def gate_tool_boundary(self, tool_name: str, sigs: Optional[dict]) -> GateResult:
        """工具边界：工具名非空 + 已注册。"""
        if not tool_name:
            return GateResult("tool_boundary", OUTCOME_BLOCK, "工具名为空")
        if sigs is not None and tool_name not in sigs:
            return GateResult("tool_boundary", OUTCOME_BLOCK, f"工具 {tool_name} 未注册")
        return GateResult("tool_boundary", OUTCOME_PASS, detail={"tool": tool_name})

    # ── ⑤ 风险分级 ──

    def gate_risk(self, output_type: str) -> GateResult:
        """风险分级：READ 通过；写/删/排程 → 需审批。"""
        risk = RISK_BY_OUTPUT.get(output_type, "WRITE_APPROVE")
        if risk == "READ":
            return GateResult("risk", OUTCOME_PASS, "只读", detail={"risk": risk})
        return GateResult("risk", OUTCOME_APPROVE, f"写操作({output_type})需审批",
                          detail={"risk": risk})

    # ── ② RBAC ──

    async def gate_rbac(self, user_id: str, authorized_roles: list) -> GateResult:
        """操作权限：用户具备 authorized_roles 中任一角色则通过，否则委托审批。"""
        if not authorized_roles:
            return GateResult("rbac", OUTCOME_PASS, "无角色限制")
        if not user_id:
            return GateResult("rbac", OUTCOME_DELEGATE, "未提供用户身份，需委托",
                              detail={"required": list(authorized_roles)})
        from app.services.auth_service import auth_service
        user_roles = await auth_service.get_effective_roles(user_id)
        if user_roles & set(authorized_roles):
            return GateResult("rbac", OUTCOME_PASS,
                              detail={"roles": sorted(user_roles)})
        return GateResult("rbac", OUTCOME_DELEGATE,
                          f"需要角色 {list(authorized_roles)}",
                          detail={"required": list(authorized_roles)})

    # ── ③ 数据权限 ──

    async def gate_data_permission(self, tool_name: str, user_id: str,
                                   params: dict) -> GateResult:
        """数据权限（行/列）：注入 DataFilter（过滤不拦截），返回过滤器描述留痕。"""
        from app.services.action_executor import action_executor
        try:
            filters = await action_executor.apply_data_filters(tool_name, user_id, params)
        except Exception:
            filters = []
        return GateResult("data_permission", OUTCOME_PASS,
                          detail={"filters": filters})

    # ── ④ 业务规则 constraint ──

    async def gate_rule_constraint(self, concept_name: str, params: dict,
                                   action_name: str) -> GateResult:
        """业务规则：constraint 违规 → block（不可执行）。"""
        if not concept_name:
            return GateResult("rule_constraint", OUTCOME_PASS, "无概念规则")
        from app.services.rule_engine import rule_engine
        try:
            violations, _inf, _appr = rule_engine.evaluate_all(
                concept_name, params or {}, action_name)
        except Exception:
            return GateResult("rule_constraint", OUTCOME_PASS, "规则评估异常，放行")
        if violations:
            msgs = [getattr(v, "message", str(v)) for v in violations]
            return GateResult("rule_constraint", OUTCOME_BLOCK, "; ".join(msgs),
                              detail={"violations": msgs})
        return GateResult("rule_constraint", OUTCOME_PASS)

    # ── ⑥ 审批门禁 ──

    def gate_approval(self, requires_confirmation: bool,
                      needs_delegation: bool) -> GateResult:
        """审批门禁：RBAC 委托 → delegate；写操作需确认 → approve。"""
        if needs_delegation:
            return GateResult("approval", OUTCOME_DELEGATE, "需委托审批")
        if requires_confirmation:
            return GateResult("approval", OUTCOME_APPROVE, "写操作需人机确认")
        return GateResult("approval", OUTCOME_PASS, "无需审批")

    # ── 流水线编排 ──

    async def evaluate(self, tool_name: str, output_type: str,
                       sigs: Optional[dict] = None, *,
                       user_id: str = "", authorized_roles: list = None,
                       concept_name: str = "", params: dict = None,
                       requires_confirmation: bool = False,
                       needs_delegation: bool = False) -> GovernanceReport:
        """依次跑六闸门，返回完整审计报告。

        ① 工具边界 → ② RBAC → ③ 数据权限 → ④ 业务规则 → ⑤ 风险分级 → ⑥ 审批门禁。
        任一 block 即短路；判定逻辑委托既有组件（auth_service/action_executor/rule_engine），
        本流水线只负责统一编排 + 审计留痕。
        """
        report = GovernanceReport(tool_name=tool_name)
        # ① 工具边界
        report.results.append(self.gate_tool_boundary(tool_name, sigs))
        if report.blocked:
            return report
        # ② RBAC
        report.results.append(await self.gate_rbac(user_id, authorized_roles or []))
        # ③ 数据权限
        report.results.append(await self.gate_data_permission(tool_name, user_id, params or {}))
        # ④ 业务规则
        report.results.append(await self.gate_rule_constraint(concept_name, params or {}, tool_name))
        if report.blocked:
            return report
        # ⑤ 风险分级
        report.results.append(self.gate_risk(output_type))
        # ⑥ 审批门禁
        report.results.append(self.gate_approval(requires_confirmation, needs_delegation))
        return report

    # ── 统一 pre-execute 门禁（对齐 DSH tools/pre-execute）──

    async def pre_execute(
        self, *, tool_name: str, output_type: str, sigs: Optional[dict] = None,
        user_id: str = "", authorized_roles: list = None, concept_name: str = "",
        params: dict = None, requires_confirmation: bool = False,
        needs_delegation: bool = False, skip_rbac: bool = False,
        skip_data_permission: bool = False,
    ) -> dict:
        """执行前六闸门统一判定。返回：
        {
            blocked: bool,          # 任一 block → 拦截
            reason: str,            # 拦截原因（blocked 时）
            violations: list,       # 业务规则违规（block 依据）
            inferences: list,       # 推理结论（供写路径后续应用）
            approvals: list,        # 审批门禁（规则审批/RBAC委托/风险），供上层挂起确认
            risk: str,              # 风险分级
            report: GovernanceReport,
        }
        审批(approve/delegate)不在本层挂起——它只把 approvals 返回，由上层
        （_apply_write_gates）统一做 confirm_required / confirm_delegated（对齐 DSH：
        pre-execute 的 ask 由 approval seam 处理，execute 只做确定性动作）。
        skip_rbac / skip_data_permission：写/删路径因入口已做 RBAC、路径已做
        apply_data_filters 而跳过（避免重复评估/重复注入），后续逐步收敛到统一入口。
        """
        params = params or {}
        report = GovernanceReport(tool_name=tool_name, params=dict(params))

        # ① 工具边界
        report.results.append(self.gate_tool_boundary(tool_name, sigs))
        if report.blocked:
            return self._pack(report)

        # ② RBAC（入口已做时跳过，避免重复判定）
        _needs_delegation = needs_delegation
        if skip_rbac:
            report.results.append(GateResult("rbac", OUTCOME_PASS, "入口已做 RBAC，跳过"))
        else:
            _rbac = await self.gate_rbac(user_id, authorized_roles or [])
            report.results.append(_rbac)
            _needs_delegation = _needs_delegation or _rbac.outcome == OUTCOME_DELEGATE

        # ③ 数据权限（路径已注入时跳过，避免重复注入 _scope_*）
        if skip_data_permission:
            report.results.append(GateResult("data_permission", OUTCOME_PASS, "路径已注入，跳过"))
        else:
            report.results.append(await self.gate_data_permission(tool_name, user_id, params))

        # ④ 业务规则：写/删概念才评估；一次 evaluate_all 同时取 violations/inferences/approvals，
        #    避免执行路径重复评估规则。
        violations, inferences, approvals = [], [], []
        if concept_name and output_type in ("write", "create", "update", "delete"):
            from app.services.rule_engine import rule_engine
            try:
                violations, inferences, approvals = rule_engine.evaluate_all(
                    concept_name, dict(params), tool_name)
            except Exception as e:  # 规则评估异常放行（确定性降级，不阻断业务）
                report.results.append(GateResult("rule_constraint", OUTCOME_PASS,
                                                 f"规则评估异常放行: {e}"))
            else:
                if violations:
                    _msgs = [getattr(v, "message", str(v)) for v in violations]
                    report.results.append(GateResult(
                        "rule_constraint", OUTCOME_BLOCK, "; ".join(_msgs),
                        detail={"violations": _msgs}))
                elif approvals:
                    report.results.append(GateResult(
                        "rule_constraint", OUTCOME_APPROVE,
                        f"规则「{'; '.join(getattr(a, 'rule_label', '') for a in approvals)}」触发审批",
                        detail={"approval_rules": len(approvals)}))
                else:
                    report.results.append(GateResult("rule_constraint", OUTCOME_PASS))

        # ⑤ 风险分级
        _risk = self.gate_risk(output_type)
        report.results.append(_risk)
        risk = (_risk.detail or {}).get("risk", "READ")

        # ⑥ 审批门禁（规则审批 / RBAC 委托 / 风险写操作 / 需确认，统一在此汇总）
        if report.blocked:
            report.results.append(GateResult("approval", OUTCOME_BLOCK, "前序闸门已拦截"))
        elif _needs_delegation:
            report.results.append(GateResult("approval", OUTCOME_DELEGATE, "需委托审批"))
        elif approvals or requires_confirmation or risk != "READ":
            report.results.append(GateResult("approval", OUTCOME_APPROVE, "需人机确认"))
        else:
            report.results.append(GateResult("approval", OUTCOME_PASS, "无需审批"))

        return self._pack(report, violations=violations, inferences=inferences,
                          approvals=approvals, risk=risk)

    @staticmethod
    def _pack(report: "GovernanceReport", violations: list = None,
              inferences: list = None, approvals: list = None, risk: str = "") -> dict:
        _blocked = report.blocked
        _reason = ""
        if _blocked:
            _blk = next((r for r in report.results if r.outcome == OUTCOME_BLOCK), None)
            _reason = _blk.reason if _blk else "治理闸门拦截"
        return {
            "blocked": _blocked,
            "reason": _reason,
            "violations": violations or [],
            "inferences": inferences or [],
            "approvals": approvals or [],
            "risk": risk,
            "report": report,
        }


# 全局单例
governance_pipeline = GovernancePipeline()
