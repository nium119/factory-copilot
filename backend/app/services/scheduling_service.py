# -*- coding: utf-8 -*-
"""通用排程引擎：自动排程 + 插单重排（遗传算法，本体驱动，不硬编码概念名）。

排程结构从本体关系图读取（任意 namespace 通用）：
- 排程对象概念 = 排程动作（outputType='schedule'）所在概念（如 WorkOrder）
- 工序概念 = 排程对象的 HasMany 关系目标（如 Operation）
- 工单字段 / 机器字段 = 工序的 ref 属性（refConcept 指向排程对象 / 其他概念）
- 时长字段 = duration、顺序字段 = seq（本体属性约定）

遗传算法求解 job shop scheduling，最小化 makespan（固定种子可复现）。
插单 = 定位订单建工单 → 按工艺路线复制工序 → 重新排程。
"""
import random
from datetime import datetime, timedelta

from app.core.logger import log

_POP = 20
_GENS = 60
_CROSS = 0.8
_MUT = 0.15
_SEED = 42
_DP_THRESHOLD = 15  # 工序总数 ≤ 此值用动态规划精确求最优；否则遗传算法近似


def _dp_schedule(work_orders: list):
    """动态规划（回溯 + makespan 剪枝）精确求解 job shop，最小化 makespan。

    只适合小规模（工序数 ≤ _DP_THRESHOLD）：状态空间随工序数阶乘增长，
    超过阈值在 auto_schedule 里自动切到遗传算法。返回 (makespan, {op_id: {machine,start,end}})。
    """
    op_meta = {}
    for wo in work_orders:
        ops = sorted(wo["operations"], key=lambda x: (x.get("seq") or 0))
        prev = None
        for op in ops:
            op_meta[op["id"]] = {
                "wo": wo["id"], "machine": op.get("machineId") or "",
                "dur": float(op.get("duration") or 1.0) * 60.0,
                "prev": prev,
            }
            prev = op["id"]

    n = len(op_meta)
    best = {"makespan": float("inf"), "schedule": {}}

    def dfs(done, machine_time, wo_end, makespan, cur_sched):
        if makespan >= best["makespan"]:
            return  # 剪枝：已不可能优于当前最优
        if len(done) == n:
            best["makespan"] = makespan
            best["schedule"] = dict(cur_sched)
            return
        for op_id, meta in op_meta.items():
            if op_id in done:
                continue
            if meta["prev"] and meta["prev"] not in done:
                continue  # 前序工序未排完，跳过（保持 seq 顺序）
            start = max(machine_time.get(meta["machine"], 0.0), wo_end.get(meta["wo"], 0.0))
            end = start + meta["dur"]
            done.add(op_id)
            old_mt = machine_time.get(meta["machine"], 0.0)
            old_we = wo_end.get(meta["wo"], 0.0)
            machine_time[meta["machine"]] = end
            wo_end[meta["wo"]] = end
            cur_sched[op_id] = {"machine": meta["machine"], "start": start, "end": end}
            dfs(done, machine_time, wo_end, max(makespan, end), cur_sched)
            cur_sched.pop(op_id, None)
            machine_time[meta["machine"]] = old_mt
            wo_end[meta["wo"]] = old_we
            done.remove(op_id)

    dfs(set(), {}, {}, 0.0, {})
    return best["makespan"], best["schedule"]


def _genetic_schedule(work_orders: list, seed: int = _SEED):
    """遗传算法最小化 makespan（operation-based 编码，保持工序 seq 顺序）。

    染色体 = 工单 id 序列（每个工单出现其次序数次数），decode 时按 seq 顺序排
    该工单的下一道工序。返回 (makespan_分钟, 最优工单序列, {op_id: start/end 分钟})。
    """
    rnd = random.Random(seed)
    wo_ops = {}
    op_meta = {}
    for wo in work_orders:
        ops = sorted(wo["operations"], key=lambda x: (x.get("seq") or 0))
        wo_ops[wo["id"]] = ops
        for op in ops:
            op_meta[op["id"]] = {
                "wo": wo["id"], "machine": op.get("machineId") or "",
                "dur": float(op.get("duration") or 1.0) * 60.0,
            }

    def decode(order):
        machine_time: dict = {}
        wo_idx: dict = {wo: 0 for wo in wo_ops}
        wo_last_end: dict = {wo: 0.0 for wo in wo_ops}
        schedule: dict = {}
        makespan = 0.0
        for wo_id in order:
            idx = wo_idx.get(wo_id, 0)
            if idx >= len(wo_ops.get(wo_id, [])):
                continue
            op = wo_ops[wo_id][idx]
            wo_idx[wo_id] = idx + 1
            m = op_meta[op["id"]]
            start = max(machine_time.get(m["machine"], 0.0), wo_last_end[wo_id])
            end = start + m["dur"]
            schedule[op["id"]] = {"machine": m["machine"], "start": start, "end": end}
            machine_time[m["machine"]] = end
            wo_last_end[wo_id] = end
            makespan = max(makespan, end)
        return makespan, schedule

    def random_order():
        order = []
        for wo in wo_ops:
            order.extend([wo] * len(wo_ops[wo]))
        rnd.shuffle(order)
        return order

    def tournament(scored, k=3):
        best = None
        for _ in range(k):
            c = rnd.choice(scored)
            if best is None or c[0] < best[0]:
                best = c
        return best[1]

    def mutate(order):
        # 交换两个随机位置（保持每个工单出现次数不变）
        i, j = rnd.sample(range(len(order)), 2)
        order[i], order[j] = order[j], order[i]
        return order

    population = [random_order() for _ in range(_POP)]
    for _ in range(_GENS):
        scored = [(decode(o)[0], o) for o in population]
        scored.sort(key=lambda x: x[0])
        # 精英保留 + 从 top 池变异（交换位置，天然保持工单计数）
        new_pop = [scored[0][1][:], scored[1][1][:]]
        while len(new_pop) < _POP:
            parent = rnd.choice(scored[:max(3, _POP // 4)])[1][:]
            if rnd.random() < _MUT:
                parent = mutate(parent)
            new_pop.append(parent)
        population = new_pop

    best = min(population, key=lambda o: decode(o)[0])
    makespan, schedule = decode(best)
    return makespan, best, schedule


class SchedulingService:
    """通用排程引擎（本体驱动 + 遗传算法）。"""

    async def auto_schedule(self, namespace: str, concept_name: str, direction: str = "forward") -> dict:
        from app.services.neo4j_service import neo4j_service as ns
        from app.services.ontology_service import ontology_service

        concept = ontology_service.get_concept(concept_name)
        if not concept:
            return {"scheduled": 0, "message": f"排程对象概念 {concept_name} 不存在", "version": ""}

        pk = next((p.get("name") for p in concept.get("properties", []) if p.get("isPrimary")), "id")

        # 工序概念 = 排程对象 HasMany 子概念中，同时有「ref→排程对象」和「ref→机器」的那个
        op_concept_name = None
        candidates = [rel.get("target") for rel in concept.get("relations", [])
                      if str(rel.get("type", "")).lower() in ("hasmany", "has_many")]
        for cand in candidates:
            cc = ontology_service.get_concept(cand)
            if not cc:
                continue
            refs = [p for p in cc.get("properties", []) if p.get("type") == "ref"]
            has_wo_ref = any(p.get("refConcept") == concept_name for p in refs)
            has_machine_ref = any(p.get("refConcept") and p.get("refConcept") != concept_name for p in refs)
            if has_wo_ref and has_machine_ref:
                op_concept_name = cand
                break
        if not op_concept_name:
            return {"scheduled": 0,
                    "message": f"概念 {concept_name} 的 HasMany 子概念中未找到「工序」（缺 ref→工单 + ref→机器），无法排程",
                    "version": ""}

        op_concept = ontology_service.get_concept(op_concept_name)
        if not op_concept:
            return {"scheduled": 0, "message": f"工序概念 {op_concept_name} 不存在", "version": ""}

        # 从工序概念识别字段语义
        op_pk = next((p.get("name") for p in op_concept.get("properties", []) if p.get("isPrimary")), "id")
        wo_field = None
        machine_field = None
        duration_field = "duration"
        seq_field = "seq"
        for prop in op_concept.get("properties", []):
            pname = prop.get("name", "")
            if prop.get("type") == "ref":
                if prop.get("refConcept") == concept_name:
                    wo_field = pname
                elif not machine_field:
                    machine_field = pname
            elif pname in ("duration", "processingTime", "processing_time"):
                duration_field = pname
            elif pname in ("seq", "sequence", "sortOrder", "orderNo"):
                seq_field = pname

        if not wo_field or not machine_field:
            return {"scheduled": 0,
                    "message": f"工序概念 {op_concept_name} 缺工单 ref 字段（→{concept_name}）或机器 ref 字段",
                    "version": ""}

        # 读待排程工单
        wos = await ns.execute_read(
            f"MATCH (w:`{concept_name}` {{_namespace: $ns}}) "
            f"WHERE (w.status IS NULL OR w.status <> '已排产') "
            f"RETURN w.`{pk}` AS id",
            {"ns": namespace},
        )
        if not wos:
            return {"scheduled": 0, "message": "无待排程工单", "version": ""}

        work_orders = []
        for wo in wos:
            ops = await ns.execute_read(
                f"MATCH (op:`{op_concept_name}` {{_namespace: $ns}}) "
                f"WHERE op.`{wo_field}` = $wid "
                f"RETURN op.`{op_pk}` AS id, op.`{seq_field}` AS seq, "
                f"op.`{machine_field}` AS machineId, op.`{duration_field}` AS duration",
                {"ns": namespace, "wid": wo["id"]},
            )
            work_orders.append({
                "id": wo["id"],
                "operations": [{
                    "id": o["id"], "seq": o["seq"], "machineId": o["machineId"],
                    "duration": o["duration"] or 1.0,
                } for o in ops],
            })

        # 自适应：小规模用动态规划精确求最优，大规模用遗传算法近似
        total_ops = sum(len(wo["operations"]) for wo in work_orders)
        if total_ops <= _DP_THRESHOLD:
            makespan_min, schedule = _dp_schedule(work_orders)
            algorithm = "动态规划"
        else:
            makespan_min, _, schedule = _genetic_schedule(work_orders)
            algorithm = "遗传算法"

        base = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
        op_wo = {}
        for wo in work_orders:
            for op in wo["operations"]:
                op_wo[op["id"]] = wo["id"]

        details = []
        wo_end: dict = {}
        for oid, sch in schedule.items():
            start_dt = base + timedelta(minutes=sch["start"])
            end_dt = base + timedelta(minutes=sch["end"])
            await ns.execute_write(
                f"MATCH (op:`{op_concept_name}` {{_namespace: $ns, `{op_pk}`: $oid}}) "
                f"SET op.`{machine_field}` = $mid, op.startTime = $start, op.endTime = $end, op.status = '已排程'",
                {"ns": namespace, "oid": oid, "mid": sch["machine"],
                 "start": start_dt.isoformat(), "end": end_dt.isoformat()},
            )
            wo_end[op_wo[oid]] = max(wo_end.get(op_wo[oid], 0.0), sch["end"])
            details.append({
                "workOrderId": op_wo[oid], "operationId": oid, "machineId": sch["machine"],
                "start": start_dt.strftime("%m-%d %H:%M"), "end": end_dt.strftime("%m-%d %H:%M"),
            })

        for wo in work_orders:
            wend = wo_end.get(wo["id"], 0.0)
            await ns.execute_write(
                f"MATCH (w:`{concept_name}` {{_namespace: $ns, `{pk}`: $wid}}) "
                f"SET w.status = '已排产', w.startDate = $start, w.dueDate = $due",
                {"ns": namespace, "wid": wo["id"],
                 "start": base.strftime("%Y-%m-%d"),
                 "due": (base + timedelta(minutes=wend)).strftime("%Y-%m-%d")},
            )

        sv_id = f"SV-{int(datetime.now().timestamp())}"
        makespan_h = makespan_min / 60.0
        await ns.execute_write(
            "MERGE (sv:ScheduleVersion {_namespace: $ns, id: $id}) "
            "SET sv.name = '自动排程版本', sv.algorithm = $alg, sv.direction = $dir, "
            "sv.createdAt = $now, sv.workOrderCount = $cnt, sv.makespanHours = $mk",
            {"ns": namespace, "id": sv_id, "alg": algorithm, "dir": direction,
             "now": datetime.now().isoformat(), "cnt": len(work_orders), "mk": round(makespan_h, 2)},
        )

        log.info(f"[Scheduling] 排程完成({algorithm}): {len(work_orders)} 工单, makespan={makespan_h:.2f}h")
        return {
            "scheduled": len(work_orders), "version": sv_id, "direction": direction,
            "algorithm": algorithm, "makespanHours": round(makespan_h, 2), "details": details,
            "message": f"已排程 {len(work_orders)} 个工单（共 {len(details)} 道工序，"
                       f"{algorithm}），makespan {makespan_h:.2f} 小时，排程版本 {sv_id}",
        }

    async def insert_order(self, namespace: str, sales_order_id: str) -> dict:
        """插单重排（本体驱动的订单→工单→工序复制，再委托通用排程）。"""
        from app.services.neo4j_service import neo4j_service as ns
        from app.services.ontology_service import ontology_service

        order_concept = next((c for c in ontology_service.get_concepts()
                              if any(a.get("outputType") == "schedule" and a.get("actionName") == "insertOrder"
                                     for a in c.get("actions", []))), None)
        order_name = order_concept.get("name", "SalesOrder") if order_concept else "SalesOrder"
        wo_name = next((r.get("target") for r in order_concept.get("relations", [])
                        if str(r.get("type", "")).lower() in ("hasmany", "has_many")), "WorkOrder") \
            if order_concept else "WorkOrder"

        so = await ns.execute_read(
            f"MATCH (o:`{order_name}` {{_namespace: $ns, id: $id}}) RETURN o.id AS id, o.product AS product, o.qty AS qty",
            {"ns": namespace, "id": sales_order_id},
        )
        if not so:
            return {"scheduled": 0, "message": f"插单失败：订单 {sales_order_id} 不存在", "version": ""}

        o = so[0]
        wo_id = f"WO-{sales_order_id}"
        await ns.execute_write(
            f"MERGE (w:`{wo_name}` {{_namespace: $ns, id: $wid}}) "
            f"SET w.saleOrderCode = $soid, w.materialCode = $product, w.qty = $qty, w.status = '待排产'",
            {"ns": namespace, "wid": wo_id, "soid": sales_order_id,
             "product": o.get("product") or "MAT-A1", "qty": o.get("qty") or 1},
        )

        # 基于工艺路线复制工序（RouteOperation → Operation），字段从本体读
        ro_name = "RouteOperation"
        op_name = wo_name  # 工序概念
        op_concept = ontology_service.get_concept(op_name)
        op_pk = next((p.get("name") for p in op_concept.get("properties", []) if p.get("isPrimary")), "id")
        wo_field = next((p.get("name") for p in op_concept.get("properties", [])
                         if p.get("type") == "ref" and p.get("refConcept") == wo_name), None)
        machine_field = next((p.get("name") for p in op_concept.get("properties", [])
                              if p.get("type") == "ref" and p.get("refConcept") != wo_name), None)

        route_ops = await ns.execute_read(
            f"MATCH (ro:`{ro_name}` {{_namespace: $ns}}) RETURN ro.seq AS seq, ro.machineId AS machineId, ro.name AS name, ro.duration AS duration",
            {"ns": namespace},
        )
        for i, ro in enumerate(route_ops, 1):
            op_id = f"OP-{sales_order_id}-{i}"
            await ns.execute_write(
                f"MERGE (op:`{op_name}` {{_namespace: $ns, `{op_pk}`: $oid}}) "
                f"SET op.`{wo_field}` = $wid, op.seq = $seq, op.`{machine_field}` = $mid, op.duration = $dur, op.status = '待排程'",
                {"ns": namespace, "oid": op_id, "wid": wo_id,
                 "seq": ro.get("seq") or i, "mid": ro.get("machineId") or "",
                 "dur": ro.get("duration") or 1.0},
            )

        result = await self.auto_schedule(namespace, wo_name)
        result["inserted"] = wo_id
        result["message"] = f"订单 {sales_order_id} 已插入（工单 {wo_id}），" + result.get("message", "")
        log.info(f"[Scheduling] 插单重排: 订单 {sales_order_id} → 工单 {wo_id}")
        return result


scheduling_service = SchedulingService()
