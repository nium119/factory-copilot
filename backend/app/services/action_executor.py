"""动作执行器 — 将本体工具名称映射为针对 Neo4j 的 Cypher 查询。

主路径：从本体动作签名生成 Cypher → Neo4j。
映射来源：OntologyService（Neo4j）。
"""

import json
import re
from dataclasses import asdict
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.logger import log


# ── 自动字段名解析 ───────────────────────────────────────

def _snake_to_camel(s: str) -> str:
    """work_order_id → workOrderId"""
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _camel_to_snake(s: str) -> str:
    """workOrderId → work_order_id"""
    result = []
    for ch in s:
        if ch.isupper() and result:
            result.append("_")
        result.append(ch.lower())
    return "".join(result)


def _build_field_map(records: list[dict], ont_names: list[str]) -> dict[str, str]:
    """通过自动名称解析构建 api_field → ont_prop 映射。

    策略（按先后顺序，每条记录的键首个命中即生效）：
      1. 精确匹配（不做更改）
      2. snake_case → camelCase
      3. camelCase → snake_case
      4. 大小写不敏感匹配
      5. 无匹配 → 键保持原样（额外字段）
    """
    if not records or not ont_names:
        return {}
    ont_set = set(ont_names)
    ont_lower = {n.lower(): n for n in ont_names}

    all_keys = set()
    for r in records:
        all_keys.update(r.keys())

    mapping = {}
    for key in all_keys:
        if key in ont_set:
            continue  # 精确匹配，无需重命名

        # snake → camel
        camel_key = _snake_to_camel(key)
        if camel_key in ont_set:
            mapping[key] = camel_key
            continue

        # camel → snake
        snake_key = _camel_to_snake(key)
        if snake_key in ont_set:
            mapping[key] = snake_key
            continue

        # 大小写不敏感
        lower = key.lower()
        if lower in ont_lower:
            mapping[key] = ont_lower[lower]

    return mapping


def apply_column_filters(
    concept: dict, user_roles: set[str], records: list[dict],
) -> list[dict]:
    """从记录中过滤列，仅保留 DataFilter visibleProperties 允许的属性。

    如果 concept 的 dataFilters 中没有 visibleProperties 规则匹配，
    则返回未修改的记录（即所有列可见）。

    始终保留 id、主键属性、name、label 以及 _ 前缀的系统字段。
    """
    if not records:
        return records

    data_filters = concept.get("dataFilters", [])
    ont_props = concept.get("properties", [])

    pk_name = next(
        (p["name"] for p in ont_props if p.get("isPrimary")),
        "id",
    )
    always_keep = {pk_name, "name", "label"}

    visible: set[str] = set()
    for df in data_filters:
        vis_props = df.get("visibleProperties", [])
        if not vis_props:
            continue
        if not df.get("roles") or (user_roles & set(df["roles"])):
            visible.update(vis_props)

    if not visible:
        return records

    visible |= always_keep

    return [
        {k: v for k, v in r.items() if k in visible or k.startswith("_")}
        for r in records
    ]


class ActionExecutor:
    """对 Neo4j 执行本体动作。"""

    def __init__(self):
        self._concepts: Dict[str, dict] = {}
        self._sigs: Dict[str, dict] = {}
        self._mappings: list = []

    # ── 初始化 ──────────────────────────────────────────────

    def _ensure_loaded(self):
        """从 OntologyService（Neo4j）延迟加载。"""
        from app.services.ontology_service import ontology_service
        concepts = ontology_service.get_concepts()
        if concepts:
            self._concepts = {c["name"]: c for c in concepts}
        sigs = ontology_service.get_action_signatures()
        if sigs:
            self._sigs = {s["functionName"]: s for s in sigs}
        # 加载已连接的 MCP 工具到签名表
        from app.mcp import mcp_registry
        for tool_name in mcp_registry.get_tool_names():
            if tool_name not in self._sigs:
                client, mcp_tool = mcp_registry._tool_map.get(tool_name, (None, None))
                self._sigs[tool_name] = {
                    "functionName": tool_name,
                    "conceptName": mcp_tool.name if mcp_tool else tool_name,
                    "source": "mcp",
                    "description": mcp_tool.description if mcp_tool else "",
                }
        # 为启用了向量化的概念自动合成 findSimilar Action 签名
        for concept_name, concept in self._concepts.items():
            vec_cfg = concept.get("vectorization")
            if not vec_cfg:
                continue
            if isinstance(vec_cfg, str):
                try:
                    import json as _json
                    vec_cfg = _json.loads(vec_cfg)
                except Exception:
                    continue
            if not vec_cfg.get("enabled"):
                continue
            fn_name = f"{concept_name}_findSimilar"
            if fn_name not in self._sigs:
                label = concept.get("label", concept_name)
                pk = next((p.get("name", "name") for p in concept.get("properties", []) if p.get("isPrimary")), "name")
                self._sigs[fn_name] = {
                    "functionName": fn_name,
                    "conceptName": concept_name,
                    "conceptLabel": label,
                    "actionName": "findSimilar",
                    "actionLabel": f"匹配相似{label}",
                    "description": f"图+向量混合检索，匹配与目标最相似的{label}",
                    "outputType": "similarity",
                    "source": "vectorization",
                    "requiresConfirmation": False,
                    "authorized_roles": [],
                    "review_roles": [],
                    "params": [
                        {"name": "targetKey", "label": "目标主键", "type": "string",
                         "required": False, "conceptPropertyRef": f"{concept_name}.{pk}"},
                        {"name": "topK", "label": "返回数量", "type": "int",
                         "required": False, "defaultValue": 5, "conceptPropertyRef": ""},
                        {"name": "message", "label": "用户消息", "type": "string",
                         "required": False, "conceptPropertyRef": ""},
                    ],
                }
        self._mappings = ontology_service.get_mappings()
        if self._concepts:
            log.info(
                f"[ActionExecutor] 已从本体加载："
                f"{len(self._concepts)} 个概念，{len(self._sigs)} 个动作，"
                f"{len(self._mappings)} 个映射"
            )
        else:
            log.warning("[ActionExecutor] 本体服务无数据可用")

    def invalidate_cache(self):
        """清除缓存数据，下次调用时从 ontology_service 重新加载。"""
        self._concepts = {}
        self._sigs = {}
        self._mappings = []

    # ── 公开 API ───────────────────────────────────────────

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        self._ensure_loaded()

        sig = self._sigs.get(tool_name)
        if not sig:
            return f"[未实现] 工具 {tool_name} 尚未绑定执行逻辑"

        try:
            if sig.get("outputType") == "list" or tool_name.endswith("_query"):
                return await self._execute_query(sig, arguments)
            return await self._execute_write(sig, arguments)
        except Exception as e:
            log.error(f"动作 {tool_name} 执行失败：{e}", exc_info=True)
            return f"[工具执行失败] {tool_name}: {e}"

    def list_handlers(self) -> list:
        self._ensure_loaded()
        return sorted(set(self._sigs.keys()))

    async def apply_data_filters(
        self, tool_name: str, user_id: str, arguments: Dict[str, Any],
    ) -> list[str]:
        """根据用户身份将数据过滤器注入到参数中。

        在 param_extract/tool_start SSE 事件之前调用，以便前端
        能在执行链中显示已应用的过滤器。

        返回可读的过滤器描述列表（例如 "workshop=机加车间"）。
        """
        self._ensure_loaded()
        sig = self._sigs.get(tool_name)
        if not sig:
            return []

        concept_name = sig.get("conceptName", "")
        is_query = sig.get("outputType") == "list" or tool_name.endswith("_query") or tool_name.endswith("_delete")
        if not is_query:
            return []

        concept = self._concepts.get(concept_name, {})

        from app.services.auth_service import auth_service as _auth_svc
        from app.services.ontology_service import ontology_service
        user_roles = await _auth_svc.get_effective_roles(user_id)
        applied: list[str] = []

        # Scope: 概念级图遍历范围（多工厂隔离），沿父链自动继承
        scope = ontology_service.resolve_scope(concept_name)
        if scope:
            user_val = await _auth_svc.get_user_property(
                user_id, scope["scopeMatchProperty"],
            )
            if user_val is not None:
                arguments["_scope_concept"] = scope["scopeConcept"]
                arguments["_scope_property"] = scope["scopeProperty"]
                arguments["_scope_value"] = user_val
                applied.append(f"scope: [:3]→{scope['scopeConcept']}.{scope['scopeProperty']}={user_val}")
                log.info(f"[Scope] {concept_name} →{scope['scopeConcept']}.{scope['scopeProperty']}={user_val}")

        # DataFilter 规则：按角色的属性匹配 + 列可见性
        data_filters = concept.get("dataFilters", [])
        for df in data_filters:
            roles = df.get("roles", [])
            if roles and not (user_roles & set(roles)):
                continue

            # 简单属性匹配（向后兼容）
            prop = df.get("property", "")
            if not prop:
                continue
            if prop in arguments:
                continue  # 已由用户显式输入设置
            user_val = await _auth_svc.get_user_property(
                user_id, df.get("matchProperty", ""),
            )
            if user_val is not None:
                arguments[prop] = user_val
                applied.append(f"{prop}={user_val}")
                log.info(f"[DataFilter] {concept_name} 过滤器已应用：{prop}={user_val}（用户={user_id}）")
        return applied

    async def execute_structured_async(
        self, tool_name: str, arguments: Dict[str, Any],
        user_id: str = "",
    ) -> Dict[str, Any]:
        """通过 DataBackend（Neo4j）执行。

        使用本体动作定义来构建查询，然后委托给配置的
        DataBackend 执行。

        如果提供了 user_id 且该动作有 authorized_roles，则执行前
        进行 RBAC 权限检查。
        """
        log.warning(f"[EXEC] execute_structured_async called: tool={tool_name}")
        self._ensure_loaded()
        sig = self._sigs.get(tool_name)
        if not sig:
            result_text = await self.execute(tool_name, arguments)
            return {
                "tool": tool_name,
                "arguments": arguments,
                "result": result_text,
                "rowCount": 0,
                "source": "neo4j",
            }

        # ── 权限检查 ──
        if user_id:
            required_roles = sig.get("authorized_roles", [])
            if required_roles:
                from app.services.auth_service import auth_service
                allowed = await auth_service.check(user_id, required_roles)
                if not allowed:
                    return {
                        "tool": tool_name,
                        "arguments": arguments if isinstance(arguments, dict) else {},
                        "result": f"权限不足：用户 {user_id} 无权执行此操作（需要角色: {', '.join(required_roles)}）",
                        "rowCount": 0,
                        "source": "auth_service",
                    }

        from app.services.data_backend import data_backend

        # MCP 工具：直接调 mcp_registry
        if sig.get("source") == "mcp":
            try:
                from app.mcp import mcp_registry
                # 检查 MCP 工具是否有必填参数未提供
                mcp_params = sig.get("params", [])
                required_params = [p for p in mcp_params if p.get("required")]
                _msg = (arguments or {}).get('_message', '')
                missing = [p["name"] for p in required_params if not arguments.get(p["name"]) and p["name"] != '_message']
                if missing and _msg:
                    # 参数不全：把消息附上让 MCP Server 自己解析
                    arguments = dict(arguments or {})
                    arguments['message'] = _msg
                elif missing and not _msg:
                    # 无消息无参数：提示用户
                    hints = [f"{p['name']}（{p.get('label', p['name'])}）" for p in required_params if p['name'] in missing]
                    return {
                        "tool": tool_name,
                        "arguments": {},
                        "result": f"请提供以下参数：{'；'.join(hints)}",
                        "rowCount": 0,
                        "source": "mcp",
                        "sourceLabel": "MCP 工具",
                    }
                # P0 统一写操作治理：MCP 工具按声明的 risk 分级
                from app.agents.settings import TOOL_SAFETY as _MCP_TS
                _mcp_risk = (_MCP_TS.get(tool_name) or {}).get("risk", "READ")
                if _mcp_risk in ("WRITE_APPROVE", "CRITICAL"):
                    # 审批拦截：声明为写操作的工具必须走人机确认
                    from app.agents.approval import ApprovalManager
                    from app.agents.guardrails import _summarize_params as _summ
                    _approval = ApprovalManager.create_approval_request(
                        action=tool_name,
                        description=f"MCP 写操作: {tool_name}: {_summ(arguments)}",
                        details=dict(arguments or {}),
                    )
                    if _approval:
                        return {
                            "tool": tool_name,
                            "arguments": arguments if isinstance(arguments, dict) else {},
                            "result": f"MCP 写操作需要审批确认 (ID: {_approval['approval_id']})",
                            "needs_approval": True,
                            "approval_id": _approval["approval_id"],
                            "rowCount": 0,
                            "source": "mcp",
                            "sourceLabel": "MCP 工具",
                        }
                result_text = await mcp_registry.call_tool(tool_name, arguments) or ""
                # WRITE_AUDIT：执行后审计（READ 不审计，写操作留痕）
                if _mcp_risk == "WRITE_AUDIT":
                    from app.agents.guardrails import AuditLogger
                    AuditLogger.log(
                        tool_name=tool_name, action_name=tool_name, risk="WRITE_AUDIT",
                        agent="mcp", params=dict(arguments or {}),
                        result_preview=str(result_text)[:200], success=True,
                    )
                return {
                    "tool": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": result_text or "MCP 工具执行完成",
                    "rowCount": 0,
                    "source": "mcp",
                    "sourceLabel": "MCP 工具",
                }
            except Exception as e:
                return {
                    "tool": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": f"MCP 工具执行失败: {e}",
                    "rowCount": 0,
                    "source": "mcp",
                }

        concept_name = sig.get("conceptName", "")
        backend_name = "neo4j"
        inferences = []
        trigger_alerts = []

        # 操作类型判定：actionName / 函数名后缀优先（更可靠），outputType 仅作补充
        # 修复：此前 outputType 配置错误（如 delete 配成 write）会覆盖正确的类型推断，
        #       导致删除走写入分支、删除不生效。
        _output_type = "write"
        if sig.get("actionName") == "delete" or (sig.get("functionName") or "").endswith("_delete"):
            _output_type = "delete"
        elif sig.get("actionName") == "query" or (sig.get("functionName") or "").endswith("_query"):
            _output_type = "query"
        elif sig.get("actionName") == "findSimilar" or (sig.get("functionName") or "").endswith("_findSimilar"):
            _output_type = "similarity"
        else:
            _output_type = sig.get("outputType") or "write"
        # ── GraphRAG 混合检索 ──
        if _output_type == "similarity":
            from app.services.vector_search_engine import vector_search_engine as _vse
            target_key = (arguments or {}).get("targetMaterialCode") or (arguments or {}).get("targetKey") or ""
            result_text, records = await _vse.find_similar(
                concept_name, target_key, topK=(arguments or {}).get("topK", 5),
                arguments=arguments,
            )
            return {
                "tool": tool_name,
                "arguments": arguments if isinstance(arguments, dict) else {},
                "result": result_text,
                "rowCount": len(records),
                "source": "neo4j",
                "sourceLabel": "GraphRAG混合检索",
                "actionType": "query",
            }
        if _output_type in ("list", "query"):
            # 查询路径：DataBackend.query(concept, filters)
            if user_id:
                await self.apply_data_filters(tool_name, user_id, arguments)
            result_text, row_count, backend_name, records = await self._query_via_backend(
                concept_name, sig, arguments, data_backend, user_id=user_id,
            )
            # 对查询到的实体触发规则评估
            if records:
                from app.services.rule_engine import rule_engine
                trigger_alerts = rule_engine.evaluate_triggers(concept_name, records)
                if trigger_alerts:
                    for a in trigger_alerts:
                        a.concept_name = concept_name
                        a.agents = ["production_execution", "production_management", "quality_equipment", "analysis_monitor"]
                    result_text += "\n\n触发器预警：\n" + "\n".join(
                        f"  • {a.rule_label}：{a.description}"
                        f"（{a.entity_id}：{a.trigger_condition}）"
                        for a in trigger_alerts
                    )
        elif _output_type == "delete":
            # 删除路径：scope → 规则校验 → DataBackend.delete
            from app.services.rule_engine import rule_engine

            # Scope 过滤：注入用户所属工厂等数据权限
            if user_id:
                await self.apply_data_filters(tool_name, user_id, arguments)

            # 规则校验（与写入路径一致）
            violations, inferences, approvals = rule_engine.evaluate_all(
                concept_name, dict(arguments), tool_name,
            )
            if arguments.pop('_skip_approval', None):
                approvals = []
            if approvals:
                return {
                    "source": "rule_engine",
                    "result": "需要审批",
                    "needs_approval": True,
                    "approvals": [asdict(a) for a in approvals],
                }
            if violations:
                msg = "规则校验失败，无法删除：\n" + "\n".join(
                    f"  • {v.message}" for v in violations
                )
                log.warning(f"[ActionExecutor] 删除规则违规：{violations}")
                return {
                    "tool": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": msg,
                    "rowCount": 0,
                    "source": "rule_engine",
                }

            result_text, row_count, backend_name = await self._execute_delete_via_backend(
                concept_name, sig, arguments, data_backend,
            )
        else:
            # 写入路径：DataBackend.create 之前先校验规则
            from app.services.rule_engine import rule_engine

            # 用来自数据库的实体当前状态丰富参数，以便链式推理规则
            # 能引用数据库中的字段（如 rework_count）。
            concept = self._concepts.get(concept_name, {})
            pk_prop = next(
                (p["name"] for p in concept.get("properties", []) if p.get("isPrimary")),
                "id",
            )
            entity_id = arguments.get(pk_prop)
            if entity_id:
                existing = await data_backend.resolve_entity(concept_name, str(entity_id))
                if existing:
                    enriched = dict(existing)
                    enriched.update(arguments)
                    arguments = enriched
                    log.info(f"[ActionExecutor] 已用数据库状态丰富参数：{concept_name}/{entity_id}")

            violations, inferences, approvals = rule_engine.evaluate_all(
                concept_name, dict(arguments), tool_name,
            )
            if arguments.pop('_skip_approval', None):
                approvals = []  # 审批已通过，不再触发门禁
            if approvals:
                return {
                    "source": "rule_engine",
                    "result": "需要审批",
                    "needs_approval": True,
                    "approvals": [asdict(a) for a in approvals],
                }
            if violations:
                msg = "规则校验失败：\n" + "\n".join(
                    f"  • {v.message}" for v in violations
                )
                log.warning(f"[ActionExecutor] 规则违规：{violations}")
                return {
                    "tool": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": msg,
                    "rowCount": 0,
                    "source": "rule_engine",
                }

            # 检查是否有推理需要用户确认（阶段 1：预览）
            skip_inferences = arguments.pop('_skip_inferences', None)
            if skip_inferences:
                log.info(f"[ActionExecutor] 用户已跳过推理")
                inferences = []

            unconfirmed = [inf for inf in inferences if inf.requires_confirmation]
            if unconfirmed and not arguments.pop('_confirmed_inferences', None):
                log.info(f"[ActionExecutor] {len(unconfirmed)}/{len(inferences)} 条推理需要确认")
                return {
                    "tool": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": "",
                    "rowCount": 0,
                    "source": "inference_preview",
                    "needs_inference_confirmation": True,
                    "inferences": [
                        self._inference_to_dict(inf)
                        for inf in inferences
                    ],
                }

            # 在 create 之前，将同概念的旧版推理值合并到参数中
            extra_props = {}
            same_concept_actions = []
            for inf in inferences:
                if inf.target_concept == concept_name:
                    if inf.target_action:
                        same_concept_actions.append(inf)
                    else:
                        extra_props[inf.target_property] = inf.target_value
            if extra_props:
                log.info(f"[ActionExecutor] 自动应用推理：{extra_props}")
                arguments.update(extra_props)

            result_text, row_count, backend_name, created_entity_id = await self._create_via_backend(
                concept_name, sig, arguments, data_backend,
            )

            # 对已创建的实体执行同概念基于动作的推理
            for inf in same_concept_actions:
                try:
                    ok, msg = await self._apply_inferred_action(inf, created_entity_id)
                    inf._applied = ok
                    inf._applied_msg = msg
                    log.info(
                        f"[ActionExecutor] 同概念推理动作："
                        f"{inf.target_concept}.{inf.target_action} 作用于 {created_entity_id} → {msg or 'OK'}"
                    )
                except Exception as e:
                    log.warning(f"[ActionExecutor] 同概念推理失败：{e}")
                    inf._applied = False

            # 执行跨概念推理结果
            for inf in inferences:
                if inf.target_concept == concept_name:
                    continue  # 已在上面处理
                target_entity_id = self._resolve_target_entity_id(
                    sig, arguments, inf.target_concept,
                )
                if not target_entity_id and inf.target_action:
                    # 对于基于动作的推理，实体 ID 来自参数
                    target_entity_id = (
                        inf.target_params.get("id")
                        or inf.target_params.get("workOrderId")
                        or inf.target_params.get("productId")
                        or ""
                    )
                if target_entity_id or inf.target_action:
                    try:
                        if inf.target_action:
                            ok, msg = await self._apply_inferred_action(
                                inf, target_entity_id,
                            )
                            inf._applied = ok
                            inf._applied_msg = msg
                            log.info(
                                f"[ActionExecutor] 推理动作："
                                f"{inf.target_concept}.{inf.target_action} → {msg or 'OK'}"
                            )
                        else:
                            # 旧版：直接写入属性
                            await self._apply_inference_write(
                                inf.target_concept, target_entity_id,
                                inf.target_property, inf.target_value,
                            )
                            log.info(
                                f"[ActionExecutor] 推理已应用："
                                f"{inf.target_concept}({target_entity_id}).{inf.target_property} = {inf.target_value}"
                            )
                            inf._applied = True
                    except Exception as e:
                        log.warning(f"[ActionExecutor] 推理写入失败：{e}")
                        inf._applied = False

            if inferences:
                applied = [inf for inf in inferences if getattr(inf, '_applied', False) or inf.target_concept == concept_name]
                suggested = [inf for inf in inferences if inf not in applied]
                if applied:
                    lines = []
                    for inf in applied:
                        if inf.target_action:
                            lines.append(
                                f"  • {inf.rule_label}：{inf.description}"
                                f"（已调用 {inf.target_concept}.{inf.target_action}）"
                            )
                        else:
                            lines.append(
                                f"  • {inf.rule_label}：{inf.description}"
                                f"（已设置 {inf.target_concept}.{inf.target_property} = {inf.target_value}）"
                            )
                    result_text += "\n\n推理已应用：\n" + "\n".join(lines)
                if suggested:
                    lines = []
                    for inf in suggested:
                        if inf.target_action:
                            lines.append(
                                f"  • {inf.rule_label}：{inf.description}"
                                f"（建议调用 {inf.target_concept}.{inf.target_action}）"
                            )
                        else:
                            lines.append(
                                f"  • {inf.rule_label}：{inf.description}"
                                f"（建议设置 {inf.target_concept}.{inf.target_property} = {inf.target_value}）"
                            )
                    result_text += "\n\n推理建议：\n" + "\n".join(lines)

        # 构建数据源说明
        source_label = {"api": "业务系统实时查询", "neo4j": "图数据库", "db": "数据库直连"}.get(backend_name, backend_name)

        # 操作类型：由分支决定，消费方直接读取
        if tool_name.endswith("_delete") or sig.get("actionName") == "delete":
            action_type = "delete"
        elif tool_name.endswith("_query") or sig.get("outputType") == "list":
            action_type = "query"
        else:
            action_type = "write"

        return {
            "tool": tool_name,
            "arguments": arguments if isinstance(arguments, dict) else {},
            "result": result_text,
            "rowCount": row_count,
            "source": backend_name,
            "sourceLabel": source_label,
            "actionType": action_type,
            "inferences": [
                self._inference_to_dict(inf, concept_name)
                for inf in inferences
            ] if inferences else [],
            "alerts": [
                {
                    "rule_name": a.rule_name,
                    "rule_label": a.rule_label,
                    "description": a.description,
                    "concept_name": a.concept_name,
                    "entity_id": a.entity_id,
                    "trigger_condition": a.trigger_condition,
                    "severity": a.severity,
                    "agents": a.agents or [],
                }
                for a in trigger_alerts
            ] if trigger_alerts else [],
        }

    def _format_records_table(
        self, concept_name: str, records: list[dict],
        title: str = "",
    ) -> str:
        """将查询记录格式化为 markdown 表格（中文列头 + null→- + bool→✅❌）。"""
        if not records:
            return ""
        concept = self._concepts.get(concept_name, {})
        ont_props = concept.get("properties", [])
        ont_labels = {p["name"]: p.get("label", p["name"]) for p in ont_props}
        ordered_ont_names = [p["name"] for p in ont_props]

        import json as _json
        enum_map = {}
        for p in ont_props:
            ev = p.get("enumValues")
            if ev:
                if isinstance(ev, str):
                    try: ev = _json.loads(ev)
                    except: ev = {}
                if isinstance(ev, dict):
                    enum_map[p["name"]] = {str(k): str(v) for k, v in ev.items()}

        all_keys = set()
        for r in records:
            all_keys.update(k for k, v in r.items() if v is not None)

        ordered_keys = list(ordered_ont_names)
        # 内部字段（与 dynamic._strip_internal_ids 的 _drop_headers 保持一致）不展示
        _drop = {'embedding', 'namespace', '_namespace'}
        extra_keys = [k for k in all_keys if k not in ordered_ont_names
                      and not k.startswith("_") and not k.endswith("Display")
                      and k != "id" and k not in _drop]
        ordered_keys.extend(extra_keys)
        header_parts = [ont_labels.get(k, k) for k in ordered_keys]

        bool_props = {p["name"] for p in ont_props if p.get("type") == "bool"}
        for r in records:
            for bp in bool_props:
                if bp not in r:
                    r[bp] = "❌"
            for k, v in list(r.items()):
                if k in enum_map and str(v) in enum_map[k]:
                    r[k] = enum_map[k][str(v)]
                elif isinstance(v, bool):
                    r[k] = "✅" if v else "❌"

        lines = [title or f"找到 {len(records)} 条记录：", ""]
        lines.append("| " + " | ".join(header_parts) + " |")
        lines.append("|" + "|".join(["---" for _ in header_parts]) + "|")
        for r in records:
            parts = [str(r.get(k, "")) if r.get(k) is not None else "-" for k in ordered_keys]
            lines.append("| " + " | ".join(parts) + " |")
        return "\n".join(lines)

    async def _query_via_backend(
        self, concept_name: str, sig: dict, args: dict, backend,
        user_id: str = "",
    ) -> tuple[str, int, str, list]:
        """从动作参数构建过滤器，并通过 DataBackend 查询。

        返回 (result_text, row_count, backend_name, raw_records)。
        """
        filters = {}

        # 模糊搜索（企业级多字段 OR + 命中分级）
        # args 里由 intent_router 产出的 _fuzzy / _fuzzy_op，
        # 用 action 参数 schema（sig["params"]）确定可搜索字段。
        _fuzzy_val = args.get('_fuzzy') or args.get('fuzzy')
        if _fuzzy_val:
            _fuzzy_op = args.get('_fuzzy_op') or args.get('fuzzy_op') or 'contains'
            # 可搜索字段：action schema 中 string/ref 类型参数（可查询字段）
            _fuzzy_fields = []
            for _p in sig.get("params", []):
                _t = (_p.get("type") or _p.get("paramType") or "").lower()
                if _t in ('string', 'ref', 'text'):
                    _fn = _p.get("conceptPropertyRef", "")
                    # 跨概念 ref（Material.materialCode）取目标属性名 materialCode
                    if _fn and "." in _fn:
                        _fn = _fn.split(".", 1)[1]
                    if _fn and _fn not in _fuzzy_fields:
                        _fuzzy_fields.append(_fn)
            # 确保主键也在可搜索字段中
            if not any(_f == 'code' for _f in _fuzzy_fields):
                _fuzzy_fields.append('code')
            if _fuzzy_fields:
                filters['_fuzzy'] = _fuzzy_val
                filters['_fuzzy_op'] = _fuzzy_op
                filters['_fuzzy_fields'] = _fuzzy_fields
                log.info(f"[ActionExecutor] 模糊搜索: {concept_name} kw={_fuzzy_val} op={_fuzzy_op} fields={_fuzzy_fields}")

        for p_name, p_value in args.items():
            if p_value is None or p_value == "":
                continue
            # 来自概念级实体解析的合成参数
            if p_name == '_concept_entity':
                filters['id'] = p_value
                continue
            if p_name == '_concept_name':
                continue
            param_def = next(
                (p for p in sig.get("params", []) if p["name"] == p_name), None,
            )
            if param_def:
                prop_ref = param_def.get("conceptPropertyRef", "")
                if prop_ref and "." in prop_ref:
                    ref_concept, prop_name = prop_ref.split(".", 1)
                    if ref_concept != concept_name:
                        # 跨概念参数：解析 ref 值 → 用目标实体的关联键属性做过滤
                        entity = await backend.resolve_entity(ref_concept, p_value)
                        if entity:
                            ref_concept_def = self._concepts.get(ref_concept, {})
                            # 从目标概念的 relations 中找 joinOn 确定关联键
                            join_key = None
                            for rel in ref_concept_def.get("relations", []):
                                if rel.get("target") == concept_name and rel.get("joinOn"):
                                    for part in rel["joinOn"].split("="):
                                        part = part.strip()
                                        if part.startswith(ref_concept + "."):
                                            join_key = part.split(".")[1].strip()
                                            break
                                if join_key:
                                    break
                            # 也从本概念的 relations 中找
                            if not join_key:
                                concept_def = self._concepts.get(concept_name, {})
                                for rel in concept_def.get("relations", []):
                                    if rel.get("target") == ref_concept and rel.get("joinOn"):
                                        for part in rel["joinOn"].split("="):
                                            part = part.strip()
                                            if part.startswith(ref_concept + "."):
                                                join_key = part.split(".")[1].strip()
                                                break
                                    if join_key:
                                        break
                            if join_key and entity.get(join_key):
                                filters[p_name] = entity[join_key]
                            else:
                                filters[p_name] = p_value
                        else:
                            filters[p_name] = p_value
                    else:
                        filters[prop_name] = p_value
                else:
                    filters[p_name] = p_value
            else:
                filters[p_name] = p_value

        # 数据授权：从请求上下文（ContextVar）获取当前用户，注入 RBAC DataFilter。
        # 防止动态分析/查询绕过权限读取未授权数据；单 action 路径已在执行前注入（幂等）。
        try:
            from app.services.multi_system_backend import _request_user_id
            _uid = user_id or _request_user_id.get() or ""
            if _uid:
                await self.apply_data_filters(f"{concept_name}_query", _uid, filters)
        except Exception as ex:
            log.warning(f"[ActionExecutor] DataFilter 注入失败 ({concept_name}): {ex}")

        records = await backend.query(concept_name, filters)
        if not records:
            from app.services.data_backend import FallbackDataBackend
            _bn = "api" if (isinstance(backend, FallbackDataBackend) and backend._has_api_config(concept_name)) else "neo4j"
            return "未找到匹配的记录。", 0, _bn, []

        concept = self._concepts.get(concept_name, {})
        ont_props = concept.get("properties", [])
        ont_names = [p["name"] for p in ont_props]

        # 构建自动解析映射：api_field_name → 本体属性名
        auto_map = _build_field_map(records, ont_names)

        # 使用组合映射重命名字段
        if auto_map:
            records = [
                {auto_map.get(k, k): v for k, v in r.items()}
                for r in records
            ]

        # 列级数据过滤：根据用户角色限制可见属性
        if user_id and records:
            from app.services.auth_service import auth_service as _auth_svc
            user_roles = await _auth_svc.get_effective_roles(user_id)
            if user_roles:
                concept = self._concepts.get(concept_name, {})
                records = apply_column_filters(concept, user_roles, records)

        result_text = self._format_records_table(concept_name, records)

        from app.services.data_backend import FallbackDataBackend
        if isinstance(backend, FallbackDataBackend) and backend._has_api_config(concept_name):
            backend_name = "api"
        else:
            health = await backend.health()
            backend_name = health.get("primary", "unknown")

        # 更新请求级数据源状态，避免前端误标为"模拟数据"
        try:
            from app.agents.tools.mes_cli_runner import set_data_source
            set_data_source(backend_name)
        except Exception:
            pass

        return result_text, len(records), backend_name, records

    async def _create_via_backend(
        self, concept_name: str, sig: dict, args: dict, backend,
    ) -> tuple[str, int, str, str]:
        """通过 DataBackend 创建实体。返回 (text, row_count, backend_name, entity_id)。"""
        # 校验跨概念引用：确保引用实体在目标概念中存在
        for param in sig.get("params", []):
            ref = param.get("conceptPropertyRef", "")
            if not ref or "." not in ref:
                continue
            ref_concept, ref_prop = ref.split(".", 1)
            if ref_concept == concept_name:
                continue  # 同概念引用跳过
            param_value = args.get(param["name"])
            if not param_value:
                if param.get("required"):
                    return (
                        f"参数 '{param.get('label', param['name'])}' 是必填的，但未提供值",
                        0, "validation", "",
                    )
                continue
            entity = await backend.resolve_entity(ref_concept, param_value)
            if not entity:
                return (
                    f"引用的 {ref_concept} 实体 '{param_value}' 不存在，请检查输入",
                    0, "validation", "",
                )
            # ref实体属性通过前缀映射到目标概念：materialCode→materialName/materialSpec等
            target_concept = self._concepts.get(concept_name, {})
            target_props = {p["name"]: p for p in target_concept.get("properties", [])}
            param_prefix = param["name"].rstrip("Code")  # materialCode → material
            for ep_name, ep_val in entity.items():
                if ep_name.startswith("_") or ep_name in ("code", "id", "_namespace"):
                    continue
                mapped_name = param_prefix + ep_name[0].upper() + ep_name[1:]
                if mapped_name in target_props and mapped_name not in args:
                    args[mapped_name] = ep_val

        # 用 ref 属性查找已有实体，找到就取 pk 做 MERGE
        concept_def = self._concepts.get(concept_name, {})
        pk_name = "id"
        for pp in concept_def.get("properties", []):
            if pp.get("isPrimary"):
                pk_name = pp["name"]
                break
        # 只用主键参数查找已有实体（存在则 MERGE 更新，否则新建）。
        # 注意：不能用 ref 业务参数（materialCode/routingCode 等）做查找——
        # 那样创建会误匹配已有工单并更新它，而不是新增记录。
        _lookup = {k: v for k, v in args.items() if v and k == pk_name}
        if _lookup:
            existing = await backend.query(concept_name, _lookup)
            if existing:
                pk_val = existing[0].get(pk_name)
                if pk_val:
                    args[pk_name] = str(pk_val)
            elif args.get(pk_name):
                return f"未找到 {concept_name}（{', '.join(f'{k}={v}' for k,v in _lookup.items())}），请确认条件是否正确", 0, "validation", ""

        # 参数重映射：targetProperty 指向概念属性，写入时把参数值赋给目标属性
        for p in sig.get("params", []):
            target = p.get("targetProperty", "")
            if target and p["name"] in args:
                args[target] = args[p["name"]]

        # 过滤掉非概念属性的参数（如 oldMaterialCode、reason 等仅用于 Action 上下文）
        _prop_names = {p["name"] for p in concept_def.get("properties", [])}
        _clean_args = {k: v for k, v in args.items() if k in _prop_names or k.startswith("_")}
        result = await backend.create(concept_name, _clean_args)
        if "error" in result:
            # 回退到同步执行
            result_text = await self.execute(sig["functionName"], args)
            return result_text, 0, "neo4j", ""

        result_id = result.get("id", "")
        from app.services.data_backend import FallbackDataBackend
        is_api = isinstance(backend, FallbackDataBackend) and backend._has_api_config(concept_name)
        if is_api:
            backend_name = "api"
            # 双写 Neo4j: 从系统API配置读取 dualWriteNeo4j 开关
            try:
                api_cfg = backend._get_api_config(concept_name)
                if api_cfg and api_cfg.get("dualWriteNeo4j"):
                    from app.services.data_backend import data_backend as _db
                    neo4j = _db._backends.get("neo4j")
                    if neo4j and neo4j._available:
                        await neo4j.create(concept_name, dict(args))
            except Exception:
                pass
        else:
            health = await backend.health()
            backend_name = health.get("primary", "unknown")

        # 更新请求级数据源状态
        try:
            from app.agents.tools.mes_cli_runner import set_data_source
            set_data_source(backend_name)
        except Exception:
            pass

        # 格式化写入结果（与查询一致的表格展示）
        # 创建/更新后回读完整节点，避免只展示传入参数（造成"只有工单号"的错觉）
        display_record = result
        try:
            pk_value = result.get(pk_name)
            if pk_value:
                refreshed = await backend.query(concept_name, {pk_name: pk_value})
                if refreshed:
                    display_record = refreshed[0]
                    result_id = str(refreshed[0].get("id") or refreshed[0].get(pk_name) or "")
        except Exception:
            pass
        result_table = self._format_records_table(concept_name, [display_record])
        result_text = f"操作完成\n\n{result_table}"
        return result_text, 1, backend_name, result_id

    def _resolve_target_entity_id(
        self, sig: dict, arguments: dict, target_concept: str,
    ) -> Optional[str]:
        """查找跨概念推理目标的实体 ID。

        遍历动作参数，找到 conceptPropertyRef 与目标概念匹配
        的那个，然后从 arguments 中取其值。
        """
        for p in sig.get("params", []):
            ref = p.get("conceptPropertyRef", "")
            if ref.startswith(target_concept + "."):
                val = arguments.get(p["name"])
                if val:
                    return str(val)
        # 回退：尝试常见的 ID 模式
        for key in ("id", f"{target_concept[0].lower()}{target_concept[1:]}Id"):
            val = arguments.get(key)
            if val:
                return str(val)
        return None

    async def _apply_inference_write(
        self, concept: str, entity_id: str, property_name: str, value: str,
    ) -> None:
        """通过 Neo4j MERGE 更新实体属性（旧版格式）。"""
        from app.services.neo4j_service import neo4j_service

        ns = settings.NEO4J_NAMESPACE
        ns_clause = f" ON CREATE SET n._namespace = $ns" if ns else ""
        cypher = (
            f"MERGE (n:{concept} {{id: $id}}){ns_clause} "
            f"SET n.{property_name} = $value "
            f"RETURN n"
        )
        params = {"id": entity_id, "value": value}
        if ns:
            params["ns"] = ns
        await neo4j_service.execute_write(cypher, params)

    async def _apply_inferred_action(
        self, inference, target_entity_id: str,
    ) -> tuple[bool, str]:
        """对目标概念执行推理出的动作。

        校验约束并通过 Neo4j 写入，走完整的规则管线。
        返回 (success, message)。
        """
        from app.services.neo4j_service import neo4j_service
        from app.services.rule_engine import rule_engine

        concept_name = inference.target_concept
        action_params = dict(inference.target_params)

        # 从本体动作定义中解析原子动作的效果
        ontology = ontology_service.get_concept(concept_name)
        action_def = None
        for a in (ontology.get("actions") or []):
            if a.get("name") == inference.target_action:
                action_def = a
                break

        # 将动作参数与 outputMapping（原子动作的固定效果）合并
        set_pairs = dict(inference.target_params or {})
        if action_def:
            for k, v in (action_def.get("outputMapping") or {}).items():
                if k not in set_pairs:
                    set_pairs[k] = v

        # 构建 MERGE + SET
        for k, v in action_params.items():
            if k not in ("id", "name") and v:
                set_pairs[k] = v

        # 判断是否为"创建新实体"动作（名称以 "create" 开头）
        is_create_action = (
            inference.target_action
            and inference.target_action.startswith("create")
            and target_entity_id
        )

        # 写入前校验约束
        violations, _, _ = rule_engine.evaluate_all(concept_name, set_pairs)
        if violations:
            msgs = [v.message for v in violations]
            log.warning(
                f"[Inference] 约束阻止了 {concept_name}.{inference.target_action}：{msgs}"
            )
            return False, "; ".join(msgs)

        if is_create_action:
            # 创建新实体，将 target_entity_id 作为来源引用
            import uuid
            pk_name_s = "id"
            from app.services.ontology_service import ontology_service as _onto
            c = _onto.get_concept(concept_name)
            if c:
                for pp in c.get("properties", []):
                    if pp.get("isPrimary"): pk_name_s = pp["name"]; break
            new_id = f"{concept_name}-{uuid.uuid4().hex[:8].upper()}"
            set_pairs[pk_name_s] = new_id
            # 设置来源追溯：查找目标概念上的 source*Id 属性
            concept_props = ontology.get("properties") or []
            for p in concept_props:
                pname = p.get("name", "")
                if pname.startswith("source") and pname.endswith("Id"):
                    set_pairs[pname] = target_entity_id
                    break

            ns = settings.NEO4J_NAMESPACE
            if ns:
                set_pairs["_namespace"] = ns

            set_clauses = ", ".join(f"n.{k} = ${k}" for k in set_pairs)
            params = {k: v for k, v in set_pairs.items()}
            cypher = (
                f"CREATE (n:{concept_name} {{{pk_name_s}: ${pk_name_s}}}) "
                f"SET {set_clauses} "
                f"RETURN n"
            )
            await neo4j_service.execute_write(cypher, params)
            return True, f"{concept_name}.{inference.target_action} 已创建 {new_id}"

        if not set_pairs:
            # 无参数原子动作，outputMapping 为空 — 仅确保节点存在
            ns = settings.NEO4J_NAMESPACE
            ns_clause = f" ON CREATE SET n._namespace = $ns" if ns else ""
            cypher = f"MERGE (n:{concept_name} {{{pk_name_s}: ${pk_name_s}}}){ns_clause} RETURN n"
            params = {pk_name_s: target_entity_id}
            if ns:
                params["ns"] = ns
            await neo4j_service.execute_write(cypher, params)
            return True, f"{concept_name}.{inference.target_action} 已执行"

        ns = settings.NEO4J_NAMESPACE
        if ns:
            set_pairs["_namespace"] = ns
        set_clauses = ", ".join(f"n.{k} = ${k}" for k in set_pairs)
        params = {"id": target_entity_id, **set_pairs}
        cypher = (
            f"MERGE (n:{concept_name} {{id: $id}}) "
            f"SET {set_clauses} "
            f"RETURN n"
        )
        await neo4j_service.execute_write(cypher, params)

        return True, f"{concept_name}.{inference.target_action} 已执行"

    @staticmethod
    def _inference_to_dict(inf, concept_name: str = "") -> dict:
        """将 InferredAction 序列化为响应输出格式。"""
        return {
            "rule_name": inf.rule_name,
            "rule_label": inf.rule_label,
            "description": inf.description,
            "target_concept": inf.target_concept,
            "target_property": inf.target_property,
            "target_value": inf.target_value,
            "target_action": inf.target_action,
            "target_params": inf.target_params,
            "requires_confirmation": inf.requires_confirmation,
            "applied": getattr(inf, '_applied', inf.target_concept == concept_name),
        }

    # ── 查询生成（Cypher）───────────────────────────────

    async def _execute_delete_via_backend(
        self, concept_name: str, sig: dict, args: dict, backend,
    ) -> tuple[str, int, str]:
        """通过 DataBackend 删除实体。用参数条件匹配并删除。"""
        from app.services.ontology_service import ontology_service

        # 过滤参数：去掉内部标记（保留 _scope_* 范围过滤），保留业务参数
        scope_keys = {'_scope_concept', '_scope_property', '_scope_value'}
        filters = {k: v for k, v in args.items() if v and (not k.startswith("_") or k in scope_keys)}
        if not any(k for k in filters if not k.startswith("_")):
            return "无法删除：未提供查询条件", 0, "validation"

        # 1. 查询确认存在（含范围过滤）
        records = await backend.query(concept_name, filters)
        if not records:
            return f"未找到匹配的 {concept_name} 记录，无需删除", 0, "neo4j"

        # 2. 逐条删除
        concept = ontology_service.get_concept(concept_name)
        ont_props = concept.get("properties", []) if concept else []
        pk_name = "id"
        for pp in ont_props:
            if pp.get("isPrimary"):
                pk_name = pp["name"]
                break

        deleted, failed = 0, 0
        for r in records:
            pk_value = r.get(pk_name)
            if pk_value and await backend.delete(concept_name, pk_name, str(pk_value)):
                deleted += 1
            else:
                failed += 1

        if deleted and not failed:
            return f"✅ 已删除 {concept_name}：{deleted} 条记录", deleted, "neo4j"
        elif deleted:
            return f"⚠️ 部分删除：成功 {deleted} 条，失败 {failed} 条", deleted, "neo4j"
        else:
            return f"❌ 删除失败：{failed} 条记录未能删除", 0, "neo4j"

    async def _execute_query(self, sig: dict, args: dict) -> str:
        """生成并执行针对 Neo4j 的 Cypher 查询。

        跨概念参数（conceptPropertyRef 指向另一个概念）仅通过
        本体关系遍历来解析。
        """
        from app.services.neo4j_service import neo4j_service
        from app.services.ontology_service import ontology_service
        from app.services.cypher_validator import execute_with_retry

        concept_name = sig["conceptName"]

        if not neo4j_service.connected:
            # Neo4j 未连接时尝试 API 直查
            try:
                from app.services.multi_system_backend import multi_system_backend
                if concept_name in multi_system_backend._concept_system:
                    result = await multi_system_backend.query(concept_name, args)
                    if result and "未找到" not in result:
                        return result
            except Exception:
                pass
            return "未找到匹配的记录。"

        # API 数据源优先: 编译器标记为 API 的概念走实时接口
        try:
            from app.services.multi_system_backend import multi_system_backend
            if concept_name in multi_system_backend._concept_system:
                result = await multi_system_backend.query(concept_name, args)
                if result and "未找到" not in result:
                    from app.agents.tools.mes_cli_runner import set_data_source
                    set_data_source("api")
                    return result
                # API 返回空 — 检查是否允许降级
                sys_name = multi_system_backend._concept_system.get(concept_name)
                sys_cfg = multi_system_backend._systems.get(sys_name) if sys_name else None
                if sys_cfg and not sys_cfg.fallback_on_error:
                    from app.agents.tools.mes_cli_runner import set_data_source as _sds
                    _sds("api")  # API 调了但无数据，来源仍是 API
                    return f"业务系统查询无结果，已禁用降级，请检查接口配置。"
                from app.agents.tools.mes_cli_runner import set_data_source as _set_ds2
                _set_ds2("neo4j")
        except Exception as e:
            # API 异常 — 检查是否允许降级
            from app.services.multi_system_backend import multi_system_backend as _msb
            sys_name = _msb._concept_system.get(concept_name)
            sys_cfg = _msb._systems.get(sys_name) if sys_name else None
            if sys_cfg and not sys_cfg.fallback_on_error:
                from app.agents.tools.mes_cli_runner import set_data_source as _sds
                _sds("api")
                return f"业务系统接口异常（{e}），已禁用降级，请检查接口配置。"
            from app.agents.tools.mes_cli_runner import set_data_source as _set_ds3
            _set_ds3("neo4j")

        concept = ontology_service.get_concept(concept_name)

        # 解析 ref 类型参数：将字符串值转为目标实体关联键的实际值
        from app.services.data_backend import data_backend
        for p_name, p_value in list(args.items()):
            if not p_value or p_name.startswith("_"):
                continue
            param_def = next((p for p in sig.get("params", []) if p["name"] == p_name), None)
            if not param_def:
                continue
            prop_ref = param_def.get("conceptPropertyRef", "")
            if not prop_ref or "." not in prop_ref:
                continue
            ref_concept, ref_prop = prop_ref.split(".", 1)
            if ref_concept == concept_name:
                continue
            entity = await data_backend.resolve_entity(ref_concept, p_value)
            if entity:
                ref_concept_def = self._concepts.get(ref_concept, {})
                join_key = None
                for rel in ref_concept_def.get("relations", []):
                    if rel.get("target") == concept_name and rel.get("joinOn"):
                        for part in rel["joinOn"].split("="):
                            part = part.strip()
                            if part.startswith(ref_concept + "."):
                                join_key = part.split(".")[1].strip()
                                break
                    if join_key:
                        break
                if not join_key:
                    for rel in (concept.get("relations") or []):
                        if rel.get("target") == ref_concept and rel.get("joinOn"):
                            for part in rel["joinOn"].split("="):
                                part = part.strip()
                                if part.startswith(ref_concept + "."):
                                    join_key = part.split(".")[1].strip()
                                    break
                        if join_key:
                            break
                if join_key and entity.get(join_key):
                    args[p_name] = entity[join_key]

        label = concept_name
        where_clauses: list[str] = []
        params: dict[str, Any] = {}
        cross_refs: list[dict] = []
        idx = 0

        for p_name, p_value in args.items():
            if p_value is None or p_value == "":
                continue
            if p_name.startswith('_'):
                continue

            param_def = next(
                (p for p in sig.get("params", []) if p["name"] == p_name), None,
            )
            if param_def:
                param_type = param_def.get("type", "string")
            elif isinstance(p_value, bool):
                param_type = "bool"
            elif isinstance(p_value, (int, float)):
                param_type = "number"
            else:
                param_type = "string"
            prop_ref = param_def.get("conceptPropertyRef", "") if param_def else ""

            if prop_ref and "." in prop_ref:
                target_concept, target_prop = prop_ref.split(".", 1)
                if target_concept == concept_name:
                    pname = f"p{idx}"
                    where_clauses.append(self._cypher_where(
                        "n", target_prop, pname, param_type,
                    ))
                    params[pname] = p_value
                    idx += 1
                else:
                    cross_refs.append({
                        "target_concept": target_concept,
                        "target_prop": target_prop,
                        "p_value": p_value,
                        "param_type": param_type,
                    })
            else:
                pname = f"p{idx}"
                where_clauses.append(self._cypher_where(
                    "n", p_name, pname, param_type,
                ))
                params[pname] = p_value
                idx += 1

        # 构建 MATCH 模式 — 跨概念参数通过本体关系遍历
        match_tail = ""
        for i, ref in enumerate(cross_refs):
            target_concept = ref["target_concept"]
            target_prop = ref["target_prop"]
            p_value = ref["p_value"]
            param_type = ref["param_type"]

            rel_label = None
            if concept:
                for rel in concept.get("relations", []):
                    if rel["target"] == target_concept:
                        rel_label = rel.get("label", "")
                        break

            if rel_label:
                t_alias = f"t{i}"
                pname = f"p{idx}"
                match_tail += f"-[:{rel_label}]->({t_alias}:{target_concept})"
                where_clauses.append(self._cypher_where(
                    t_alias, target_prop, pname, param_type,
                ))
                params[pname] = p_value
                idx += 1
            # 如果没有定义关系，则静默跳过 — 本体是权威来源

        # namespace 过滤 — 优先取概念的 namespace，没有则用全局配置兜底
        ns = (concept or {}).get("namespace") or settings.NEO4J_NAMESPACE
        log.debug(f"[Query] {concept_name} namespace={ns} concept_ns={(concept or {}).get('namespace', 'N/A')} global_ns={settings.NEO4J_NAMESPACE}")
        if ns:
            where_clauses.append("n._namespace = $ns")
            params["ns"] = ns

        # 过滤空数据 — 排除 id 和主键同时为空的占位记录
        pk_prop = next((p["name"] for p in (concept.get("properties", []) if concept else []) if p.get("isPrimary")), "code")
        # 真实记录至少 id 不为空（空记录的 id 为 NULL 或缺失）
        has_id_prop = any(p["name"] == "id" for p in (concept.get("properties", []) if concept else []))
        if has_id_prop:
            where_clauses.append("n.id IS NOT NULL AND n.id <> ''")
        else:
            where_clauses.append(f"n.{pk_prop} IS NOT NULL AND n.{pk_prop} <> ''")

        # 构建 RETURN 列：优先使用 Display 属性，计算字段加 OPTIONAL MATCH
        computed_rules = [
            r for r in (concept.get("rules", []) or [])
            if r.get("ruleType") == "computed" and r.get("expression")
        ] if concept else []
        log.warning(f"[Cypher] {concept_name} concept={concept is not None} rules_total={len(concept.get('rules', [])) if concept else 0} computed={len(computed_rules)}")
        computed_targets = {r.get("targetProperty", "") for r in computed_rules}

        base_cypher = f"MATCH (n:{label}){match_tail}"
        if where_clauses:
            base_cypher += " WHERE " + " AND ".join(where_clauses)
        if computed_rules:
            for i, cr in enumerate(computed_rules):
                # 替换表达式中的别名使其唯一并与主 MATCH 对齐
                # (a) → (n), (b:Type) → (b{i+1}:Type), b. → b{i+1}.
                expr = cr['expression']
                t_alias = f"b{i+1}"
                import re
                expr = re.sub(r'\(a\)', '(n)', expr)
                expr = re.sub(r'\(b:', f'({t_alias}:', expr)
                expr = re.sub(r'\(b\)', f'({t_alias})', expr)
                expr = re.sub(r'\bb\.', f'{t_alias}.', expr)
                # CALL 子查询取单值：避免多个 computed 规则 OPTIONAL MATCH 相乘产生笛卡尔积
                base_cypher += f"\nCALL (n) {{ OPTIONAL MATCH {expr} RETURN {t_alias} LIMIT 1 }}"

        # 构建 RETURN：主键 + Display 优先 + 计算字段
        props = concept.get("properties", []) if concept else []
        ret_parts = []
        def _as(label_text: str) -> str:
            return f"`{label_text}`"
        for p in props:
            if p.get("isPrimary"):
                ret_parts.append(f"n.{p['name']} AS {_as(p.get('label', p['name']))}")
        for p in props:
            if p.get("isPrimary"):
                continue
            if p["name"] in computed_targets:
                continue
            if p["name"].endswith("Display"):
                continue  # Display 是内部存储字段，ref 类型已在下方合并展示
            if any(pp["name"] == p["name"] + "Display" for pp in props):
                continue
            if p.get("type") == "ref":
                ret_parts.append(f"(CASE WHEN n.`{p['name']}Display` IS NOT NULL AND n.`{p['name']}Display` <> toString(n.{p['name']}) THEN n.`{p['name']}Display` + ' - ' + toString(n.{p['name']}) ELSE COALESCE(n.`{p['name']}Display`, toString(n.{p['name']})) END) AS {_as(p.get('label', p['name']))}")
            elif p.get("type") == "datetime":
                ret_parts.append(f"substring(toString(n.{p['name']}), 0, 19) AS {_as(p.get('label', p['name']))}")
            else:
                ret_parts.append(f"n.{p['name']} AS {_as(p.get('label', p['name']))}")
        # 计算字段用 targetProperty 的 label 作为列名
        prop_label_map = {p["name"]: p.get("label", p["name"]) for p in props}
        for i, cr in enumerate(computed_rules):
            alias = f"b{i+1}"
            target = cr.get('targetProperty', '')
            col_label = prop_label_map.get(target, target)
            ret_parts.append(f"{alias} IS NOT NULL AS {_as(col_label)}")

        if not ret_parts:
            ret_parts = ["n.id AS id"]
        # 去重: 计算列(IS NOT NULL)优先, 同名列的普通属性/Display列跳过
        seen = set()
        unique_parts = []
        # 先加计算列, 再加非计算列 (同别名自动跳过)
        computed = [rp for rp in ret_parts if "IS NOT NULL AS" in rp]
        others = [rp for rp in ret_parts if "IS NOT NULL AS" not in rp]
        for rp in computed + others:
            alias = rp.split(" AS ")[-1].strip() if " AS " in rp else rp
            if alias not in seen:
                seen.add(alias)
                unique_parts.append(rp)
        base_cypher += " RETURN " + ",\n  ".join(unique_parts) + " LIMIT 50"
        log.warning(f"[Cypher] {concept_name} ret_parts={ret_parts} cypher={base_cypher}")

        records = await neo4j_service.execute_read(base_cypher, params)
        if records is None:
            records = []
        if records:
            try:
                from app.agents.tools.mes_cli_runner import set_data_source
                set_data_source("neo4j")
            except Exception:
                pass
            lines = [f"找到 {len(records)} 条记录：", ""]
            keys = list(records[0].keys()) if records else []
            lines.append("| " + " | ".join(keys) + " |")
            lines.append("|" + "|".join(["---" for _ in keys]) + "|")
            for r in records:
                parts = []
                for k in keys:
                    v = r.get(k)
                    if v is None:
                        parts.append("-")
                    elif isinstance(v, bool):
                        parts.append("✅" if v else "❌")
                    else:
                        parts.append(str(v))
                lines.append("| " + " | ".join(parts) + " |")
            return "\n".join(lines)

        return "未找到匹配的记录。"

    @staticmethod
    def _cypher_where(alias: str, prop: str, pname: str, param_type: str) -> str:
        """构建 Cypher WHERE 子句片段。"""
        if param_type == "string":
            return f"{alias}.{prop} CONTAINS ${pname}"
        return f"{alias}.{prop} = ${pname}"

    async def _execute_write(self, sig: dict, args: dict) -> str:
        """通过 Neo4j 执行写入型动作，带唯一性保护。"""
        from app.services.neo4j_service import neo4j_service

        concept_name = sig["conceptName"]
        label = concept_name

        # ID 生成的原子序列——用概念主键名
        from app.services.ontology_service import ontology_service as onto_svc
        pk_name = "id"
        concept = onto_svc.get_concept(concept_name)
        if concept:
            for pp in concept.get("properties", []):
                if pp.get("isPrimary"):
                    pk_name = pp["name"]
                    break

        # 确保主键唯一性约束
        await neo4j_service.ensure_unique_constraint(label, pk_name)

        seq = await neo4j_service.next_sequence(label)
        prefix = await self._infer_id_prefix(concept_name)
        new_id = f"{prefix}-{seq:03d}"

        props = {k: v for k, v in args.items()
                 if v is not None and v != "" and not k.startswith('_')}
        props[pk_name] = new_id

        # 主键是 code 时，id 属性同步填充；主键是 id 时，code 属性同步填充
        concept_prop_names = {p["name"] for p in (concept.get("properties", []) if concept else [])}
        if pk_name == "code" and "id" in concept_prop_names and "id" not in props:
            props["id"] = new_id
        elif pk_name == "id" and "code" in concept_prop_names and "code" not in props:
            props["code"] = new_id

        ns = settings.NEO4J_NAMESPACE
        if ns:
            props["_namespace"] = ns

        set_clauses = ", ".join(f"n.{k} = ${k}" for k in props)
        params = {k: v for k, v in props.items()}
        cypher = (
            f"MERGE (n:{label} {{{pk_name}: ${pk_name}}}) "
            f"ON CREATE SET {set_clauses} "
            f"RETURN n"
        )
        await neo4j_service.execute_write(cypher, params)

        # 从 Action 参数定义中获取中文标签，构建详细摘要
        param_labels = {}
        for p in sig.get("params", []):
            param_labels[p.get("name", "")] = p.get("label", "") or p.get("name", "")

        summary_parts = []
        for k, v in args.items():
            if v is None or v == "" or k.startswith('_'):
                continue
            label = param_labels.get(k, k)
            summary_parts.append(f"{label}: {v}")

        detail = "，".join(summary_parts)
        return f"创建成功 — {sig['conceptLabel']}: {new_id}\n{detail}"

    # ── 本体辅助方法 ─────────────────────────────────────────────

    @staticmethod
    async def _infer_id_prefix(concept_name: str) -> str:
        """从现有 Neo4j 节点中提取 ID 前缀（例如 'QC-20250521-001' → 'QC'）。"""
        from app.services.neo4j_service import neo4j_service
        if neo4j_service.connected:
            records = await neo4j_service.execute_read(
                f"MATCH (n:{concept_name}) RETURN n.id AS id LIMIT 1"
            )
            if records and records[0].get("id"):
                return str(records[0]["id"]).split("-")[0]
        return concept_name[:4].upper()


action_executor = ActionExecutor()
