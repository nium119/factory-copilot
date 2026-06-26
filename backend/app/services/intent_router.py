"""意图路由器 — 基于本体的确定性工具路由。

所有路由规则均从本体的 actionSignatures 自动生成。
不硬编码关键词或正则表达式。当本体发生变化时，会调用 rebuild()
自动重新生成索引。

架构：
  L2: LLM 语义分类（仅限已知的动作名称）
  L3: 向用户列出可用动作（不使用 LLM，不做猜测）

L1 关键词匹配已移除 — 它在 Agent 合并/重组时需要持续维护，
过于脆弱。
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.logger import log


# ── 数据结构 ──

@dataclass
class ActionIndexEntry:
    """为单个本体动作预构建的路由索引。"""
    tool_name: str
    core_keywords: frozenset   # 高权重：actionLabel、conceptLabel、enumValues、完整描述
    ngram_keywords: frozenset  # 低权重：从标签/描述中提取的 2-3 字符 ngram
    concept_name: str
    concept_label: str
    action_label: str
    description: str
    requires_confirmation: bool
    param_schema: list = field(default_factory=list)  # 原始动作参数 [{name,label,type,required},...]
    # param_name → [(extractor_type, config), ...]
    param_extractors: Dict[str, List[tuple]] = field(default_factory=dict)


@dataclass
class RoutingResult:
    """将用户消息路由到工具的结果。"""
    tool_name: str = ""
    params: dict = field(default_factory=dict)
    confidence: float = 0.0
    method: str = ""             # "keyword" | "llm_classify" | "l3"
    requires_confirmation: bool = False
    concept_label: str = ""
    action_label: str = ""
    available_actions: list = field(default_factory=list)  # 用于 L3
    no_match_reason: str = ""


# ── 中文文本分词辅助函数 ──

def _tokenize_keywords(text: str) -> set:
    """从中文文本中提取有意义的关键词片段。"""
    if not text:
        return set()
    kw = set()
    # 全文作为关键词
    kw.add(text.strip())
    # 按常见分隔符拆分
    for part in re.split(r'[，。、；：（）\s]+', text):
        part = part.strip()
        if not part:
            continue
        kw.add(part)
        # 中文 2-4 字符 ngram
        if len(part) >= 2:
            for i in range(len(part) - 1):
                kw.add(part[i:i + 2])
            if len(part) >= 3:
                for i in range(len(part) - 2):
                    kw.add(part[i:i + 3])
    # 移除单字符（歧义太大）
    return {k for k in kw if len(k) >= 2}


# ── 参数提取 ──

def _extract_enum(message: str, values: list) -> Optional[str]:
    """在消息中查找枚举值。"""
    if not values:
        return None
    # 按长度降序排列，确保先检查"不合格"再检查"合格"
    for v in sorted(values, key=len, reverse=True):
        if v and v in message:
            return v
    return None


def _extract_context(message: str, context_words: list) -> Optional[str]:
    """在上下文关键词（如'产品名称'或'状态'）之后查找值。"""
    if not context_words:
        return None
    for cw in context_words:
        if cw not in message:
            continue
        # 在关键词之后查找位置
        idx = message.find(cw)
        after = message[idx + len(cw):].strip()
        # 取第一个词（中文字符、字母数字、连字符）
        m = re.search(r'[一-鿿\w\-]+', after)
        if m and m.group() not in context_words:
            return m.group()
    return None


def _extract_code(message: str, pattern: str) -> Optional[str]:
    """从消息中提取类似 WO-001 的编码模式。"""
    m = re.search(pattern, message)
    return m.group() if m else None


def _extract_number(message: str) -> Optional[int]:
    """从消息中提取数字。"""
    m = re.search(r'(\d+)', message)
    return int(m.group(1)) if m else None



# 常见中文动词前缀，不应被包含在名词提取结果中
_NOUN_LEADING_VERBS = [
    '生产制造',  # 必须在 '生产' 之前
    '生产', '制造', '创建', '加工', '组装', '处理', '记录', '查询', '检查', '检验',
]


def _extract_noun_before_number(message: str) -> Optional[str]:
    """提取紧邻数字之前的中文名词短语。

    使用有界正则 {2,6}，使得后面位置的较短子串先于位置 0 的较长子串被尝试，
    自然偏向离数字最近的词。然后去除常见的动词前缀。
    """
    m = re.search(r'([一-鿿a-zA-Z0-9]{2,6})(\d+)', message)
    if not m:
        return None
    result = m.group(1).strip()
    for v in _NOUN_LEADING_VERBS:
        if result.startswith(v) and len(result) - len(v) >= 2:
            result = result[len(v):]
            break
    return result if len(result) >= 2 else None


def _extract_date(message: str) -> Optional[str]:
    """从消息中提取日期。"""
    m = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', message)
    if m:
        return m.group(1)
    m = re.search(r'(\d{1,2})月(\d{1,2})[日号]', message)
    if m:
        import datetime
        year = datetime.date.today().year
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


# ── 路由器 ──

class IntentRouter:
    """基于本体的意图路由器。所有规则均来自 actionSignatures。"""

    def __init__(self):
        self._onto = None    # 由 rebuild 设置
        self._executor = None
        self._index: Dict[str, ActionIndexEntry] = {}

    def rebuild(self, ontology_service, action_executor):
        """从当前本体状态重建路由索引。

        在本体（重新）加载后调用此方法，使路由器与最新本体数据保持同步。
        """
        self._onto = ontology_service
        self._executor = action_executor
        self._index.clear()

        sigs = self._onto.get_action_signatures()
        concepts = {c['name']: c for c in self._onto.get_concepts()}
        handlers = set(self._executor.list_handlers())

        for sig in sigs:
            fn_name = sig['functionName']
            if fn_name not in handlers:
                continue  # 仅路由到已实现的处理器

            concept = concepts.get(sig['conceptName'], {})

            # ── 自动生成关键词（区分核心与 ngram）──
            core_keywords = set()
            ngram_keywords = set()

            # 核心：动作标签 + 概念标签（完整文本，高权重）
            core_keywords.add(sig['actionLabel'])
            core_keywords.add(sig['conceptLabel'])
            # 核心：动作描述
            action_desc = sig.get('description', '')
            if action_desc:
                core_keywords.add(action_desc)
            # 核心：概念描述（通常包含面向用户的同义词，
            # 如 QualityCheck 概念描述中包含"质量事件" → "质量"能匹配
            # 用户关于"产品质量"的查询）
            concept_desc = concept.get('description', '')
            if concept_desc:
                core_keywords.add(concept_desc)
            # 核心：枚举值
            for prop in concept.get('properties', []):
                enum_vals = prop.get('enumValues') or []
                for ev in enum_vals:
                    if ev:
                        core_keywords.add(str(ev))

            # Ngram：从标签和描述中分词得到的片段
            ngram_keywords |= _tokenize_keywords(sig['actionLabel'])
            ngram_keywords |= _tokenize_keywords(sig['conceptLabel'])
            ngram_keywords |= _tokenize_keywords(action_desc)
            ngram_keywords |= _tokenize_keywords(concept_desc)
            # 移除已存在于核心关键词中的 ngram
            ngram_keywords -= core_keywords

            # ── 自动生成参数提取器 ──
            param_extractors: Dict[str, List[tuple]] = {}
            concept_props = {p['name']: p for p in concept.get('properties', [])}

            for param in sig['params']:
                extractors = []
                param_name = param['name']
                param_label = param.get('label', '')
                param_type = param.get('type', 'string')

                # 从标签生成上下文词 — 仅使用完整标签。
                # 短 ngram 片段（如从"生产数量"中提取的"生产"）会在片段出现
                # 于消息中不相关的部分时产生错误匹配。
                context_words = [param_label] if param_label else []

                # 查找匹配的概念属性以获取枚举值
                enum_vals = param.get('enumValues') or []
                if not enum_vals:
                    prop = concept_props.get(param_name, {})
                    enum_vals = prop.get('enumValues') or []
                if not enum_vals:
                    # 也尝试按标签匹配
                    for p in concept.get('properties', []):
                        if p.get('label') == param_label and (p.get('enumValues') or []):
                            enum_vals = p['enumValues'] or []
                            break
                if not enum_vals:
                    # conceptPropertyRef 指向裸概念名（如 QualityDisposition）—
                    # 解析该 Dictionary 概念的 individuals
                    ref = param.get('conceptPropertyRef', '')
                    if ref and '.' not in ref:
                        ref_concept = concepts.get(ref, {})
                        individuals = ref_concept.get('individuals', [])
                        if individuals:
                            enum_vals = [ind.get('name', '') for ind in individuals if ind.get('name')]

                if enum_vals:
                    extractors.append(('enum', enum_vals))

                # 日期字段优先用 date 提取器（返回 YYYY-MM-DD），
                # context 提取器在后作为回退（返回原始中文如 "6月15日"，格式不兼容 input[type=date]）
                if 'date' in param_name.lower() or '日期' in param_label:
                    extractors.append(('date', None))

                if context_words:
                    extractors.append(('context', context_words))

                # ID/引用字段的编码模式 — 通用标识符模式。
                # 不推断概念名称（名称是动态的，不遵循可预测的格式）。
                if prop.get('isPrimary') or 'Id' in param_name or 'ID' in param_name:
                    extractors.append(('code', r'[A-Z]{2,}-\d+(?:-\d+)*'))

                if param_type == 'int' or '数量' in param_label:
                    extractors.append(('number', None))

                # 回退：提取数字前的中文名词（如"工业阀门100件" → "工业阀门"）
                # 对日期字段、ID 字段和有枚举值的字段跳过
                is_date_field = 'date' in param_name.lower() or '日期' in param_label
                is_id_field = 'id' in param_name.lower() or 'Id' in param_name or 'ID' in param_name
                if param_type == 'string' and not enum_vals and not is_date_field and not is_id_field:
                    extractors.append(('noun_before_number', None))

                # 实体查找：跨概念参数需要通过数据库进行实体解析。
                # 存储 (ref_concept, ref_prop)，以便 resolve_entities() 可以查询 DataBackend。
                prop_ref = param.get('conceptPropertyRef', '')
                if prop_ref and '.' in prop_ref:
                    ref_concept, ref_prop = prop_ref.split('.', 1)
                    if ref_concept != sig['conceptName']:
                        extractors.append(('entity_lookup', (ref_concept, ref_prop)))

                param_extractors[param_name] = extractors

            # 构建用于确认表单的参数 schema（name、label、type、required、defaultValue、enumValues、conceptPropertyRef）
            param_schema = []
            for param in sig['params']:
                ps = {
                    'name': param['name'],
                    'label': param.get('label', ''),
                    'type': param.get('type', 'string'),
                    'required': param.get('required', False),
                    'defaultValue': param.get('defaultValue', ''),
                }
                # 从参数自身读取枚举值（优先）
                ev = param.get('enumValues') or []
                if not ev:
                    # 从概念属性中查找枚举值
                    prop = concept_props.get(param['name'], {})
                    ev = prop.get('enumValues') or []
                if not ev:
                    # conceptPropertyRef 指向裸概念名（如 QualityDisposition、DefectLevel）时，
                    # 解析该 Dictionary 概念的 individuals 作为枚举值
                    ref = param.get('conceptPropertyRef', '')
                    if ref and '.' not in ref:
                        ref_concept = concepts.get(ref, {})
                        individuals = ref_concept.get('individuals', [])
                        if individuals:
                            ev = [ind.get('name', '') for ind in individuals if ind.get('name')]
                if ev:
                    ps['enumValues'] = ev
                ref = param.get('conceptPropertyRef', '')
                if ref:
                    ps['conceptPropertyRef'] = ref
                param_schema.append(ps)

            self._index[fn_name] = ActionIndexEntry(
                tool_name=fn_name,
                core_keywords=frozenset(core_keywords),
                ngram_keywords=frozenset(ngram_keywords),
                concept_name=sig['conceptName'],
                concept_label=sig['conceptLabel'],
                action_label=sig['actionLabel'],
                description=sig['description'],
                requires_confirmation=sig.get('requiresConfirmation', False),
                param_schema=param_schema,
                param_extractors=param_extractors,
            )

        # ── 将独有的 ngram 提升为核心关键词 ──
        # 仅在单个概念中出现的 ngram 具有语义独特性
        # （如"质量"仅出现在 QualityCheck 中，"设备"仅出现在 Equipment 中）。
        # 同一概念上的多个动作共享 ngram，因此唯一性
        # 按概念级别计算，而非每个动作。
        concept_ngrams: Dict[str, set] = {}  # concept_name → 所有 ngram 的并集
        for entry in self._index.values():
            cn = entry.concept_name
            if cn not in concept_ngrams:
                concept_ngrams[cn] = set()
            concept_ngrams[cn] |= set(entry.ngram_keywords)

        # 统计每个 ngram 在多少个概念中使用
        ngram_concept_count: Dict[str, int] = {}
        for ngrams in concept_ngrams.values():
            for kw in ngrams:
                ngram_concept_count[kw] = ngram_concept_count.get(kw, 0) + 1
        # 仅提升长度 ≥3 字符的 ngram — 2 字符片段歧义太大
        # （如描述中的"中的"会误匹配"生产中的工单"）
        distinctive = {kw for kw, count in ngram_concept_count.items()
                       if count == 1 and len(kw) >= 3}

        updated = 0
        for fn_name, entry in list(self._index.items()):
            overlap = entry.ngram_keywords & distinctive
            if overlap:
                new_core = set(entry.core_keywords) | overlap
                new_ngrams = set(entry.ngram_keywords) - overlap
                self._index[fn_name] = ActionIndexEntry(
                    tool_name=entry.tool_name,
                    core_keywords=frozenset(new_core),
                    ngram_keywords=frozenset(new_ngrams),
                    concept_name=entry.concept_name,
                    concept_label=entry.concept_label,
                    action_label=entry.action_label,
                    description=entry.description,
                    requires_confirmation=entry.requires_confirmation,
                    param_schema=entry.param_schema,
                    param_extractors=entry.param_extractors,
                )
                updated += len(overlap)

        total_kw = sum(len(e.core_keywords) + len(e.ngram_keywords) for e in self._index.values())
        log.info(f"IntentRouter 已重建：{len(self._index)} 个动作已索引 "
                 f"（共 {total_kw} 个关键词，{updated} 个 ngram 提升为核心关键词）")

    @property
    def ready(self) -> bool:
        return len(self._index) > 0

    # ── 公开 API ──

    def get_candidates(self, agent_name: str) -> dict:
        """返回某个 Agent 的工具 {fn_name: ActionIndexEntry}。用于 L2 分类。"""
        if not self.ready:
            return {}
        agent_fn_names = set()
        try:
            agent_tools = self._onto.get_tools_for_agent(agent_name)
            agent_fn_names = {t['function']['name'] for t in agent_tools}
        except Exception:
            pass
        return {k: v for k, v in self._index.items() if k in agent_fn_names}

    def route_explicit(self, fn_name: str, message: str) -> RoutingResult:
        """为显式选择的动作（来自 L2 LLM 分类）构建 RoutingResult。"""
        entry = self._index.get(fn_name)
        if not entry:
            return RoutingResult(no_match_reason=f"未知动作: {fn_name}")
        params = self.extract_params(message, fn_name)
        log.info(f"[IntentRouter] L2 匹配: {fn_name} params={params}")
        return RoutingResult(
            tool_name=fn_name,
            params=params,
            confidence=0.75,
            method="llm_classify",
            requires_confirmation=entry.requires_confirmation,
            concept_label=entry.concept_label,
            action_label=entry.action_label,
        )

    def extract_params(self, message: str, tool_name: str) -> dict:
        """使用本体驱动的规则从用户消息中提取参数。"""
        entry = self._index.get(tool_name)
        if not entry:
            return {}

        params = {}
        for param_name, extractors in entry.param_extractors.items():
            for ext_type, ext_config in extractors:
                if ext_type == 'entity_lookup':
                    continue  # 异步处理，参见 resolve_entities()
                elif ext_type == 'enum':
                    val = _extract_enum(message, ext_config)
                    if val:
                        params[param_name] = val
                        break
                elif ext_type == 'context':
                    val = _extract_context(message, ext_config)
                    if val:
                        params[param_name] = val
                        break
                elif ext_type == 'code':
                    val = _extract_code(message, ext_config)
                    if val:
                        params[param_name] = val
                        break
                elif ext_type == 'date':
                    val = _extract_date(message)
                    if val:
                        params[param_name] = val
                        break
                elif ext_type == 'number':
                    val = _extract_number(message)
                    if val is not None:
                        params[param_name] = val
                        break
                elif ext_type == 'noun_before_number':
                    val = _extract_noun_before_number(message)
                    if val:
                        params[param_name] = val
                        break
        return params

    # ── 异步实体解析（基于 DataBackend）──────────────────

    async def resolve_entities(
        self, message: str, tool_name: str, params: dict,
    ) -> dict:
        """针对跨概念参数，通过 DataBackend 解析实体引用。

        在同步的 extract_params() 之后由 _standard_process() 调用。对匹配动作
        上的每个 entity_lookup 提取器，尝试在实际数据库（Neo4j / SQLite / API）
        中查找匹配的实体。

        返回包含已解析实体 ID 的增强参数字典。
        """
        entry = self._index.get(tool_name)
        if not entry:
            return params

        from app.services.data_backend import data_backend

        enriched = dict(params)
        for param_name, extractors in entry.param_extractors.items():
            for ext_type, ext_config in extractors:
                if ext_type != 'entity_lookup':
                    continue
                ref_concept, ref_prop = ext_config
                candidate = self._find_entity_candidate(message, ref_concept)
                if not candidate:
                    continue
                entity = await data_backend.resolve_entity(ref_concept, candidate)
                if entity:
                    # 返回 ID 属性作为解析后的值
                    enriched[param_name] = entity.get('id') or entity.get(ext_config[1])
                    log.info(
                        f"[IntentRouter] 实体已解析: {candidate} → "
                        f"{ref_concept}.id={enriched[param_name]}"
                    )
                break

        # ── 概念级实体解析 ──
        # 对于查询动作，始终尝试从消息中解析动作所属概念的实体名称。
        # 这处理了类似"查询设备CNC加工中心的状态"的情况，其中实体名称
        # 嵌入在自然语言中，而非跨概念参数引用。
        if not enriched.get('id') and not enriched.get('_concept_entity'):
            concept_name = entry.concept_name
            candidate = self._find_entity_candidate(message, concept_name)
            if candidate:
                entity = await data_backend.resolve_entity(concept_name, candidate)
                if entity:
                    entity_id = entity.get('id')
                    if entity_id:
                        enriched['_concept_entity'] = entity_id
                        enriched['_concept_name'] = entity.get('name', candidate)
                        log.info(
                            f"[IntentRouter] 概念实体已解析: {candidate} → "
                            f"{concept_name}.id={entity_id}"
                        )

        # ── 跨概念实体解析 ──
        # 当消息提及的实体属于不同于动作目标的概念时
        # （例如，"设备EQUIP-001的生产质量"路由到
        # QualityCheck_query），解析跨概念实体并存储，
        # 以便 Neo4j 后端可以进行多跳图遍历。
        if not enriched.get('_cross_entity') and not enriched.get('_concept_entity'):
            # 尝试所有已知概念以查找实体引用
            all_concepts = set()
            if hasattr(self, '_onto') and self._onto:
                for c in self._onto.get_concepts():
                    all_concepts.add(c.get('name', ''))
            for other_concept in all_concepts:
                if other_concept == entry.concept_name:
                    continue
                candidate = self._find_entity_candidate(message, other_concept)
                if not candidate:
                    continue
                entity = await data_backend.resolve_entity(other_concept, candidate)
                if entity and entity.get('id'):
                    enriched['_cross_entity'] = entity['id']
                    enriched['_cross_concept'] = other_concept
                    enriched['_cross_entity_name'] = entity.get('name', candidate)
                    log.info(
                        f"[IntentRouter] 跨概念实体已解析: "
                        f"{candidate} → {other_concept}.id={entity['id']} "
                        f"（目标概念: {entry.concept_name}）"
                    )
                    # 清除错误匹配的参数：用户提到了一个实体 ID（如 EQUIP-001），
                    # 它被分配给了常规参数（如 workOrderId），但实际上属于另一个
                    # 不同的概念。移除这些参数，以免 Neo4j 多跳遍历
                    # 被不存在属性的 WHERE 子句阻塞。
                    # 但保护：保留 conceptPropertyRef 与跨概念匹配的参数
                    # （由上面的 entity_lookup 正确设置）。
                    cross_entity_id = entity['id']
                    protected_params = {
                        pname for pname, extractors in (entry.param_extractors or {}).items()
                        for etype, econf in extractors
                        if etype == 'entity_lookup' and econf[0] == other_concept
                    }
                    for key in list(enriched.keys()):
                        if key.startswith('_') or key in protected_params:
                            continue
                        val = enriched[key]
                        if isinstance(val, str) and val == cross_entity_id:
                            log.info(
                                f"[IntentRouter] 清除不匹配的参数 "
                                f"'{key}'={val}（已解析为 {other_concept}.id）"
                            )
                            del enriched[key]
                    break

        return enriched

    def _find_entity_candidate(self, message: str, concept_name: str) -> Optional[str]:
        """尝试在用户消息中查找候选实体引用。

        优先级：编码模式（EQUIP-001）→ 数字前的名词（工业阀门100件）
        → 引号字符串 → 中文人名（张工, 李主管）
        → 剩余文本提取。
        """
        # 1) 编码模式: [A-Z]{2,}-\d+(?:-\d+)*（如 WO-001, WO-20250521-001）
        m = re.search(r'[A-Z]{2,}-\d+(?:-\d+)*', message)
        if m:
            return m.group()

        # 2) 数字前的中文名词（如"工业阀门100件"）
        val = _extract_noun_before_number(message)
        if val:
            return val

        # 3) 引号字符串
        m = re.search(r'[""]([^""]{1,20})[""]', message)
        if m:
            return m.group(1)

        # 4) 带职称的中文人名:
        #    如 张工, 李主管, 王质检, 赵师傅, 钱经理, 孙主任
        #    锚定在字符串开头或常见句式助词之后，
        #    避免在复合词中间匹配（如"加工"不应匹配为"X工"）。
        m = re.search(
            r'(?:^|(?<=[\s,，。、的为是查询查看关于]))'
            r'[一-鿿](?:工|主管|质检|师傅|经理|主任)',
            message,
        )
        if m:
            return m.group()

        # 5) 剩余文本提取：从消息中去除已知的概念/动作标签（来自
        #    本体元数据）和句式模式。剩余部分很可能是实体名称。
        stripped = message
        # 从本体中收集所有已知标签：概念名称、概念
        # 标签、动作标签。采用最长优先策略以避免部分匹配。
        known_labels = set()
        if hasattr(self, '_onto') and self._onto:
            for c in self._onto.get_concepts():
                for k in ('name', 'label'):
                    v = c.get(k, '')
                    if v:
                        known_labels.add(v)
            for sig in self._onto.get_action_signatures():
                for k in ('actionLabel', 'conceptLabel'):
                    v = sig.get(k, '')
                    if v:
                        known_labels.add(v)
        # 按长度降序去除，以确保"工艺路线"在"工序"之前被移除
        for lbl in sorted(known_labels, key=len, reverse=True):
            stripped = stripped.replace(lbl, ' ')
        # 去除末尾的"的X"模式
        stripped = re.sub(r'的[一-鿿A-Za-z0-9]{1,4}$', ' ', stripped)
        # 提取最长的剩余片段
        parts = re.findall(r'[一-鿿A-Za-z0-9-]{2,30}', stripped.strip())
        for part in parts:
            part = part.strip()
            if part and part not in known_labels:
                return part

        return None

    def get_action_info(self, tool_name: str) -> Optional[ActionIndexEntry]:
        """获取某个工具的索引条目。"""
        return self._index.get(tool_name)

    async def get_param_schema(self, tool_name: str) -> list:
        """获取用于确认表单渲染的参数 schema。

        对于跨概念参数（conceptPropertyRef 指向另一个概念），
        标记 entitySearch 让前端进行服务端动态搜索，同时预加载少量初始选项。
        """
        entry = self._index.get(tool_name)
        if not entry:
            return []
        schema = list(entry.param_schema)  # 浅拷贝
        # 为跨概念参数补充实体选项
        for ps in schema:
            ref = ps.get('conceptPropertyRef', '')
            if not ref:
                continue
            if '.' not in ref:
                # 裸概念名（如 QualityDisposition）— 从已构建的 enumValues 取，无需查询
                continue
            ref_concept, _ = ref.split('.', 1)
            if ref_concept == entry.concept_name:
                continue
            try:
                from app.services.data_backend import data_backend
                # ref 类型统一启用服务端搜索（前端输入时实时查询），避免大表全量加载超时
                ps['entitySearch'] = ref_concept
                # 小数据量 (<20) 预载初始选项供快速选择
                try:
                    records = await data_backend.query(ref_concept, {}, [])
                    if records and len(records) <= 20:
                        ps['entityOptions'] = [
                            {'value': r.get('id', r.get('name', '')), 'label': r.get('name', r.get('id', ''))}
                            for r in records
                        ]
                except Exception:
                    pass  # 大表可能超时，仅用 entitySearch 即可
            except Exception as e:
                log.debug(f"[IntentRouter] 实体查找失败 ({ref}): {e}")
        return schema

    async def enrich_params(self, tool_name: str, params: dict) -> dict:
        """遍历本体关系以自动填充参数并构建上下文。

        L3 图遍历：当参数引用了一个关联概念（如 workOrderId → WorkOrder），
        查找该实体并沿其关系追踪以提供验证上下文。

        使用 DataBackend 抽象 — 兼容 Neo4j、SQLite 或 API。

        返回: {'params': {...}, 'context': {...}}
            每个 context 条目为 {"entity": {...}, "label": "中文关系标签"}。
        """
        entry = self._index.get(tool_name)
        if not entry or not self._onto:
            return {'params': dict(params), 'context': {}}

        concept = self._onto.get_concept(entry.concept_name)
        if not concept:
            return {'params': dict(params), 'context': {}}

        from app.services.data_backend import data_backend

        enriched = dict(params)
        context = {}
        relations = {r.get('target', ''): r for r in (concept.get('relations') or [])}

        # 阶段 1：对每个已填充的参数，检查它是否引用了关联概念
        for param_name, param_value in list(enriched.items()):
            if not param_value:
                continue
            for target_name, rel in relations.items():
                if target_name.lower() in param_name.lower():
                    entity = await data_backend.resolve_entity(target_name, str(param_value))
                    if entity:
                        ck = target_name[0].lower() + target_name[1:]
                        rel_label = rel.get('label', '') or target_name
                        context[ck] = {"entity": entity, "label": rel_label}
                        # 阶段 2：遍历目标概念自身的关系（扇出）
                        target_concept = self._onto.get_concept(target_name)
                        if target_concept:
                            for sub_rel in (target_concept.get('relations') or []):
                                sub_target = sub_rel.get('target', '')
                                sub_entity = await self._resolve_related_entity(
                                    target_name, entity, sub_target, data_backend,
                                )
                                if sub_entity:
                                    sk = sub_target[0].lower() + sub_target[1:]
                                    sub_label = sub_rel.get('label', '') or sub_target
                                    context[sk] = {"entity": sub_entity, "label": sub_label}
                    break

        return {'params': enriched, 'context': context}

    @staticmethod
    async def _resolve_related_entity(
        source_concept: str, source_entity: dict, target_concept: str, backend,
    ) -> Optional[dict]:
        """通过外键推断解析关联实体，与后端无关。"""
        import re

        # 尝试外键列名推断：Product → product_id / productId
        fk_snake = re.sub(r"(?<!^)(?=[A-Z])", "_", target_concept).lower() + "_id"
        fk_camel = target_concept[0].lower() + target_concept[1:] + "Id"

        for fk_key in (fk_snake, fk_camel, "id"):
            fk_val = source_entity.get(fk_key)
            if fk_val:
                entity = await backend.resolve_entity(target_concept, str(fk_val))
                if entity:
                    return entity

        # 回退：扫描所有实体值，查找与目标概念前缀匹配的 ID
        from app.services.action_executor import action_executor
        prefix = await action_executor._infer_id_prefix(target_concept)
        for val in source_entity.values():
            if isinstance(val, str) and val.startswith(prefix):
                entity = await backend.resolve_entity(target_concept, val)
                if entity:
                    return entity

        return None


# 单例
intent_router = IntentRouter()
