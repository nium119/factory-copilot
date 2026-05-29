"""Intent Router — ontology-driven deterministic tool routing.

All routing rules are auto-generated from ontology actionSignatures.
No hardcoded keywords or regex. When the ontology changes, rebuild() is
called to regenerate the index automatically.

Architecture:
  L1: Keyword match (auto-generated from action labels + enum values)
  L2: LLM classification (constrained to known action names only)
  L3: List available actions to user (no LLM, no guessing)
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.logger import log


# ── Data structures ──

@dataclass
class ActionIndexEntry:
    """Pre-built routing index for one ontology action."""
    tool_name: str
    core_keywords: frozenset   # high-weight: actionLabel, conceptLabel, enumValues, full description
    ngram_keywords: frozenset  # low-weight: 2-3 char ngrams from labels/descriptions
    concept_name: str
    concept_label: str
    action_label: str
    description: str
    requires_confirmation: bool
    param_schema: list = field(default_factory=list)  # raw action params [{name,label,type,required},...]
    # param_name → [(extractor_type, config), ...]
    param_extractors: Dict[str, List[tuple]] = field(default_factory=dict)


@dataclass
class RoutingResult:
    """Result of routing a user message to tools."""
    tool_name: str = ""
    params: dict = field(default_factory=dict)
    confidence: float = 0.0
    method: str = ""             # "keyword" | "llm_classify" | "l3"
    requires_confirmation: bool = False
    concept_label: str = ""
    action_label: str = ""
    available_actions: list = field(default_factory=list)  # for L3
    no_match_reason: str = ""


# ── Chinese text tokenization helpers ──

def _tokenize_keywords(text: str) -> set:
    """Extract meaningful keyword fragments from Chinese text."""
    if not text:
        return set()
    kw = set()
    # Full text as keyword
    kw.add(text.strip())
    # Split on common separators
    for part in re.split(r'[，。、；：（）\s]+', text):
        part = part.strip()
        if not part:
            continue
        kw.add(part)
        # 2-4 char ngrams for Chinese
        if len(part) >= 2:
            for i in range(len(part) - 1):
                kw.add(part[i:i + 2])
            if len(part) >= 3:
                for i in range(len(part) - 2):
                    kw.add(part[i:i + 3])
    # Remove single chars (too ambiguous)
    return {k for k in kw if len(k) >= 2}


# ── Parameter extraction ──

def _extract_enum(message: str, values: list) -> Optional[str]:
    """Find an enum value in the message."""
    if not values:
        return None
    for v in values:
        if v and v in message:
            return v
    return None


def _extract_context(message: str, context_words: list) -> Optional[str]:
    """Find a value after context keywords like '产品名称' or '状态'."""
    if not context_words:
        return None
    for cw in context_words:
        if cw not in message:
            continue
        # Find position after the keyword
        idx = message.find(cw)
        after = message[idx + len(cw):].strip()
        # Take first word (Chinese chars, alphanumeric, hyphens)
        m = re.search(r'[一-鿿\w\-]+', after)
        if m and m.group() not in context_words:
            return m.group()
    return None


def _extract_code(message: str, pattern: str) -> Optional[str]:
    """Extract a code pattern like WO-001 from the message."""
    m = re.search(pattern, message)
    return m.group() if m else None


def _extract_number(message: str) -> Optional[int]:
    """Extract a number from the message."""
    m = re.search(r'(\d+)', message)
    return int(m.group(1)) if m else None



# Common Chinese verb prefixes that should not be part of a noun extraction
_NOUN_LEADING_VERBS = [
    '生产制造',  # must be before '生产'
    '生产', '制造', '创建', '加工', '组装', '处理', '记录', '查询', '检查', '检验',
]


def _extract_noun_before_number(message: str) -> Optional[str]:
    """Extract Chinese noun phrase immediately before a number.

    Uses bounded regex {2,6} so shorter substrings at later positions
    are tried before longer substrings at position 0, naturally preferring
    the word closest to the number. Then strips common verb prefixes.
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
    """Extract a date from the message."""
    m = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', message)
    if m:
        return m.group(1)
    m = re.search(r'(\d{1,2})月(\d{1,2})[日号]', message)
    if m:
        return f"2025-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


# ── The Router ──

class IntentRouter:
    """Ontology-driven intent router. All rules from actionSignatures."""

    def __init__(self):
        self._onto = None    # set by rebuild
        self._executor = None
        self._index: Dict[str, ActionIndexEntry] = {}

    def rebuild(self, ontology_service, action_executor):
        """Rebuild routing index from current ontology state.

        Call this after ontology (re)load so the router stays in sync
        with the latest agent-bundle.
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
                continue  # only route to implemented handlers

            concept = concepts.get(sig['conceptName'], {})

            # ── Auto-generate keywords (split core vs ngram) ──
            core_keywords = set()
            ngram_keywords = set()

            # Core: action label + concept label (full text, high weight)
            core_keywords.add(sig['actionLabel'])
            core_keywords.add(sig['conceptLabel'])
            # Core: action description
            action_desc = sig.get('description', '')
            if action_desc:
                core_keywords.add(action_desc)
            # Core: concept description (often contains user-facing synonyms,
            # e.g. QualityCheck concept desc has "质量事件" → "质量" matches
            # user queries about "产品质量")
            concept_desc = concept.get('description', '')
            if concept_desc:
                core_keywords.add(concept_desc)
            # Core: enum values
            for prop in concept.get('properties', []):
                enum_vals = prop.get('enumValues') or []
                for ev in enum_vals:
                    if ev:
                        core_keywords.add(str(ev))

            # Ngram: tokenized fragments from labels and descriptions
            ngram_keywords |= _tokenize_keywords(sig['actionLabel'])
            ngram_keywords |= _tokenize_keywords(sig['conceptLabel'])
            ngram_keywords |= _tokenize_keywords(action_desc)
            ngram_keywords |= _tokenize_keywords(concept_desc)
            # Remove any ngrams that are already in core
            ngram_keywords -= core_keywords

            # ── Auto-generate parameter extractors ──
            param_extractors: Dict[str, List[tuple]] = {}
            concept_props = {p['name']: p for p in concept.get('properties', [])}

            for param in sig['params']:
                extractors = []
                param_name = param['name']
                param_label = param.get('label', '')
                param_type = param.get('type', 'string')

                # Generate context words from label — use the full label only.
                # Short ngram fragments (e.g., "生产" from "生产数量") cause false
                # matches when the fragment appears in unrelated parts of the message.
                context_words = [param_label] if param_label else []

                # Find matching concept property for enum values
                prop = concept_props.get(param_name, {})
                enum_vals = prop.get('enumValues') or []
                if not enum_vals:
                    # Also try matching by label
                    for p in concept.get('properties', []):
                        if p.get('label') == param_label and (p.get('enumValues') or []):
                            enum_vals = p['enumValues'] or []
                            break

                if enum_vals:
                    extractors.append(('enum', enum_vals))

                if context_words:
                    extractors.append(('context', context_words))

                # Code pattern for ID/reference fields — generic identifier pattern.
                # No concept-name inference (names are dynamic and don't follow
                # a predictable format derived from concept names).
                if prop.get('isPrimary') or 'Id' in param_name or 'ID' in param_name:
                    extractors.append(('code', r'[A-Z]{2,}-\d+'))

                if 'date' in param_name.lower() or '日期' in param_label:
                    extractors.append(('date', None))

                if param_type == 'int' or '数量' in param_label:
                    extractors.append(('number', None))

                # Fallback: extract Chinese noun before a number (e.g., "工业阀门100件" → "工业阀门")
                # Skip for date fields, ID fields, and fields with enum values
                is_date_field = 'date' in param_name.lower() or '日期' in param_label
                is_id_field = 'id' in param_name.lower() or 'Id' in param_name or 'ID' in param_name
                if param_type == 'string' and not enum_vals and not is_date_field and not is_id_field:
                    extractors.append(('noun_before_number', None))

                # Entity lookup: cross-concept params need DB-backed entity resolution.
                # Store (ref_concept, ref_prop) so resolve_entities() can query DataBackend.
                prop_ref = param.get('conceptPropertyRef', '')
                if prop_ref and '.' in prop_ref:
                    ref_concept, ref_prop = prop_ref.split('.', 1)
                    if ref_concept != sig['conceptName']:
                        extractors.append(('entity_lookup', (ref_concept, ref_prop)))

                param_extractors[param_name] = extractors

            # Build param schema for confirmation form (name, label, type, required, enumValues)
            param_schema = []
            for param in sig['params']:
                ps = {
                    'name': param['name'],
                    'label': param.get('label', ''),
                    'type': param.get('type', 'string'),
                    'required': param.get('required', False),
                }
                # Look up enum values from concept properties
                prop = concept_props.get(param['name'], {})
                ev = prop.get('enumValues') or []
                if ev:
                    ps['enumValues'] = ev
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

        # ── Promote distinctive ngrams to core ──
        # Ngrams that appear in only ONE concept are semantically distinctive
        # (e.g. "质量" only in QualityCheck, "设备" only in Equipment).
        # Multiple actions on the same concept share ngrams, so uniqueness is
        # computed at the concept level, not per-action.
        concept_ngrams: Dict[str, set] = {}  # concept_name → union of all ngrams
        for entry in self._index.values():
            cn = entry.concept_name
            if cn not in concept_ngrams:
                concept_ngrams[cn] = set()
            concept_ngrams[cn] |= set(entry.ngram_keywords)

        # Count how many concepts use each ngram
        ngram_concept_count: Dict[str, int] = {}
        for ngrams in concept_ngrams.values():
            for kw in ngrams:
                ngram_concept_count[kw] = ngram_concept_count.get(kw, 0) + 1
        distinctive = {kw for kw, count in ngram_concept_count.items() if count == 1}

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
        log.info(f"IntentRouter rebuilt: {len(self._index)} actions indexed "
                 f"({total_kw} total keywords, {updated} ngrams promoted to core)")

    @property
    def ready(self) -> bool:
        return len(self._index) > 0

    # ── Public API ──

    def route(self, message: str, agent_name: str) -> RoutingResult:
        """Route a user message to ontology actions.

        Returns RoutingResult with tool_name + params if matched,
        or available_actions list if L3.
        """
        if not self.ready:
            return RoutingResult(no_match_reason="router not initialized")

        # Domain filter: which tools belong to this agent?
        agent_fn_names = set()
        try:
            agent_tools = self._onto.get_tools_for_agent(agent_name)
            agent_fn_names = {t['function']['name'] for t in agent_tools}
        except Exception:
            pass

        candidates = {k: v for k, v in self._index.items()
                      if k in agent_fn_names}

        if not candidates:
            return RoutingResult(no_match_reason=f"no tools for agent '{agent_name}'")

        # ── L1: Weighted keyword matching ──
        # Core keywords (actionLabel, conceptLabel, enumValues) weight=3
        # Ngram keywords (2-3 char tokens) weight=1
        # Code patterns (regex like WO-\d+) are tested separately
        # Require at least one core match (or code match) for L1 to trigger
        scores = {}
        for fn_name, entry in candidates.items():
            core_score = 0
            for kw in entry.core_keywords:
                if kw in message:
                    core_score += 3
                elif '\\d' in kw:
                    # Regex pattern — test with re.search
                    try:
                        if re.search(kw, message):
                            core_score += 3
                    except re.error:
                        pass
            ngram_score = sum(1 for kw in entry.ngram_keywords if kw in message)
            total = core_score + ngram_score
            if core_score >= 3:  # must match at least one core keyword
                scores[fn_name] = total

        if scores:
            best = self._pick_best(scores, candidates)
            best_score = scores[best]
            # Require meaningful signal: at least 2 core keyword matches
            # (score ≥ 6) or strong ngram support (score ≥ 5). Weak matches
            # where only one label matched are too ambiguous for L1.
            L1_MIN_SCORE = 4

            # Concept diversity check: if the best two candidates are from
            # different concepts and scores are close (margin < 2), the query
            # is cross-concept-ambiguous → fall through to L2 LLM.
            top_scores = sorted(scores.items(), key=lambda x: -x[1])
            if len(top_scores) >= 2 and best_score >= L1_MIN_SCORE:
                second_name, second_score = top_scores[1]
                best_concept = candidates[best].concept_name
                second_concept = candidates[second_name].concept_name
                if best_concept != second_concept and (best_score - second_score) < 2:
                    log.info(
                        f"[IntentRouter] L1 ambiguous: {best}({best_concept}, score={best_score})"
                        f" vs {second_name}({second_concept}, score={second_score})"
                        f" — falling back to L2"
                    )
                    scores = {}  # clear scores to trigger L2

            if best_score >= L1_MIN_SCORE and scores:
                entry = candidates[best]

                # Question penalty: if the message is a question (contains
                # interrogative particles) and the best match is a write action,
                # the user is likely asking about data, not requesting a write.
                # Fall through to L2 for semantic classification.
                _QUESTION_WORDS = ('怎么样', '吗', '什么', '如何', '哪些', '怎么', '为什么', '哪')
                if entry.requires_confirmation and any(w in message for w in _QUESTION_WORDS):
                    log.info(
                        f"[IntentRouter] L1 question-penalty: {best} is a write action"
                        f" but message contains question words — falling back to L2"
                    )
                    scores = {}

            if best_score >= L1_MIN_SCORE and scores:
                entry = candidates[best]
                params = self.extract_params(message, best)
                confidence = min(0.95, 0.5 + best_score * 0.05)
                log.info(f"[IntentRouter] L1 match: {best} score={best_score} "
                         f"params={params}")
                return RoutingResult(
                    tool_name=best,
                    params=params,
                    confidence=confidence,
                    method="keyword",
                    requires_confirmation=entry.requires_confirmation,
                    concept_label=entry.concept_label,
                    action_label=entry.action_label,
                )
            else:
                log.info(f"[IntentRouter] L1 weak match ignored: {best} "
                         f"score={best_score} < {L1_MIN_SCORE}, falling back to L2")

        # ── L2: LLM classification (constrained to action list) ──
        # NOTE: implemented inline in _standard_process for simplicity,
        # since it needs the LLM instance. This method returns None
        # to signal that L1 failed and L2 should be tried.
        return self._build_l3_result(list(candidates.values()))

    @staticmethod
    def _pick_best(scores: dict, candidates: dict) -> str:
        """Pick the best action from scores, preferring queries over writes on ties."""
        max_score = max(scores.values())
        tied = [k for k, v in scores.items() if v == max_score]
        if len(tied) == 1:
            return tied[0]
        # Prefer query/read actions over create/record/delete
        query_first = [k for k in tied if '_query' in k]
        if query_first:
            return query_first[0]
        # Prefer lower requires_confirmation
        no_confirm = [k for k in tied if not candidates[k].requires_confirmation]
        if no_confirm:
            return no_confirm[0]
        return tied[0]

    def get_candidates(self, agent_name: str) -> dict:
        """Return {fn_name: ActionIndexEntry} for an agent's tools. For L2 classification."""
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
        """Build a RoutingResult for an explicitly chosen action (from L2 LLM classification)."""
        entry = self._index.get(fn_name)
        if not entry:
            return RoutingResult(no_match_reason=f"unknown action: {fn_name}")
        params = self.extract_params(message, fn_name)
        log.info(f"[IntentRouter] L2 match: {fn_name} params={params}")
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
        """Extract parameters from user message using ontology-driven rules."""
        entry = self._index.get(tool_name)
        if not entry:
            return {}

        params = {}
        for param_name, extractors in entry.param_extractors.items():
            for ext_type, ext_config in extractors:
                if ext_type == 'entity_lookup':
                    continue  # handled async, see resolve_entities()
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

    # ── Async entity resolution (DataBackend-backed) ──────────────────

    async def resolve_entities(
        self, message: str, tool_name: str, params: dict,
    ) -> dict:
        """Resolve entity references against DataBackend for cross-concept params.

        Called from _standard_process() after sync extract_params(). For each
        entity_lookup extractor on the matched action, tries to find a matching
        entity in the actual database (Neo4j / SQLite / API).

        Returns enriched params dict with resolved entity IDs.
        """
        entry = self._index.get(tool_name)
        if not entry:
            return params

        from app.services.data_backend import data_backend

        enriched = dict(params)
        for param_name, extractors in entry.param_extractors.items():
            # Skip if already filled by sync extraction
            if enriched.get(param_name):
                continue
            for ext_type, ext_config in extractors:
                if ext_type != 'entity_lookup':
                    continue
                ref_concept, ref_prop = ext_config
                candidate = self._find_entity_candidate(message, ref_concept)
                if not candidate:
                    continue
                entity = await data_backend.resolve_entity(ref_concept, candidate)
                if entity:
                    # Return the ID property as the resolved value
                    enriched[param_name] = entity.get('id') or entity.get(ext_config[1])
                    log.info(
                        f"[IntentRouter] entity resolved: {candidate} → "
                        f"{ref_concept}.id={enriched[param_name]}"
                    )
                break

        # ── Concept-level entity resolution ──
        # For query actions, always try to resolve entity names from the
        # message against the action's own concept.  This handles cases like
        # "查询设备CNC加工中心的状态" where the entity name is embedded in
        # natural language rather than a cross-concept parameter reference.
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
                            f"[IntentRouter] concept entity resolved: {candidate} → "
                            f"{concept_name}.id={entity_id}"
                        )

        # ── Cross-concept entity resolution ──
        # When a message mentions an entity from a different concept than the
        # action target (e.g., "设备EQUIP-001的生产质量" routes to
        # QualityCheck_query), resolve the cross-concept entity and store it
        # so the Neo4j backend can do multi-hop graph traversal.
        if not enriched.get('_cross_entity') and not enriched.get('_concept_entity'):
            # Try every known concept to find an entity reference
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
                        f"[IntentRouter] cross-concept entity resolved: "
                        f"{candidate} → {other_concept}.id={entity['id']} "
                        f"(target concept: {entry.concept_name})"
                    )
                    # Clear incorrectly-matched params: the user mentioned an
                    # entity ID (e.g. EQUIP-001) that was assigned to a regular
                    # param (e.g. workOrderId) but actually belongs to a different
                    # concept.  Remove them so the Neo4j multi-hop traversal
                    # doesn't get blocked by WHERE clauses on non-existent props.
                    cross_entity_id = entity['id']
                    for key in list(enriched.keys()):
                        if key.startswith('_'):
                            continue
                        val = enriched[key]
                        if isinstance(val, str) and val == cross_entity_id:
                            log.info(
                                f"[IntentRouter] clearing mismatched param "
                                f"'{key}'={val} (resolved to {other_concept}.id)"
                            )
                            del enriched[key]
                    break

        return enriched

    def _find_entity_candidate(self, message: str, concept_name: str) -> Optional[str]:
        """Try to find a candidate entity reference in the user message.

        Priority: code pattern (EQUIP-001) → noun before number (工业阀门100件)
        → quoted string → mixed Chinese-ASCII name extraction.
        """
        # 1) Code pattern: [A-Z]{2,}-\\d+ (e.g., WO-001, EQUIP-001, PROD-003)
        m = re.search(r'[A-Z]{2,}-\d+', message)
        if m:
            return m.group()

        # 2) Chinese noun before a number (e.g., "工业阀门100件")
        val = _extract_noun_before_number(message)
        if val:
            return val

        # 3) Quoted string
        m = re.search(r'[""]([^""]{1,20})[""]', message)
        if m:
            return m.group(1)

        # 4) Remainder extraction: strip known concept/action labels (from
        #    ontology metadata) and sentence patterns. Whatever remains is
        #    likely an entity name.
        stripped = message
        # Collect all known labels from ontology: concept names, concept
        # labels, action labels. Use longest-first to avoid partial matches.
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
        # Strip in descending length order so "工艺路线" is removed before "工序"
        for lbl in sorted(known_labels, key=len, reverse=True):
            stripped = stripped.replace(lbl, ' ')
        # Strip trailing "的X" patterns
        stripped = re.sub(r'的[一-鿿A-Za-z0-9]{1,4}$', ' ', stripped)
        # Extract longest remaining segment
        parts = re.findall(r'[一-鿿A-Za-z0-9-]{2,30}', stripped.strip())
        for part in parts:
            part = part.strip()
            if part and part not in known_labels:
                return part

        return None

    def _build_l3_result(self, candidate_entries: list) -> RoutingResult:
        """Build L3 result listing available actions."""
        actions = [
            {
                "name": e.tool_name,
                "label": e.action_label,
                "description": e.description,
                "concept_label": e.concept_label,
            }
            for e in candidate_entries
        ]
        return RoutingResult(
            method="l3",
            available_actions=actions,
            no_match_reason="no keyword match for any action",
        )

    def get_action_info(self, tool_name: str) -> Optional[ActionIndexEntry]:
        """Get index entry for a tool."""
        return self._index.get(tool_name)

    def get_param_schema(self, tool_name: str) -> list:
        """Get parameter schema for confirmation form rendering."""
        entry = self._index.get(tool_name)
        return entry.param_schema if entry else []

    async def enrich_params(self, tool_name: str, params: dict) -> dict:
        """Walk ontology relations to auto-fill params and build context.

        L3 graph traversal: when a param references a related concept
        (e.g. workOrderId → WorkOrder), look up the entity and follow
        its relations to provide verification context.

        Uses DataBackend abstraction — works with Neo4j, SQLite, or API.

        Returns: {'params': {...}, 'context': {...}}
            Each context entry is {"entity": {...}, "label": "中文关系标签"}.
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

        # Phase 1: for each filled param, check if it references a related concept
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
                        # Phase 2: walk target concept's own relations (fan-out)
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
        """Resolve a related entity through FK inference, backend-agnostic."""
        import re

        # Try FK column name inference: Product → product_id / productId
        fk_snake = re.sub(r"(?<!^)(?=[A-Z])", "_", target_concept).lower() + "_id"
        fk_camel = target_concept[0].lower() + target_concept[1:] + "Id"

        for fk_key in (fk_snake, fk_camel, "id"):
            fk_val = source_entity.get(fk_key)
            if fk_val:
                entity = await backend.resolve_entity(target_concept, str(fk_val))
                if entity:
                    return entity

        # Fallback: scan all entity values for IDs matching target concept prefix
        from app.services.action_executor import action_executor
        prefix = action_executor._infer_id_prefix(target_concept)
        for val in source_entity.values():
            if isinstance(val, str) and val.startswith(prefix):
                entity = await backend.resolve_entity(target_concept, val)
                if entity:
                    return entity

        return None


# Singleton
intent_router = IntentRouter()
