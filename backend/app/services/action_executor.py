"""Action Executor — maps ontology tool names to SQL queries against MES demo DB.

When the LLM returns a tool_call like WorkOrder_query({status: "生产中"}),
this service executes the corresponding SQL and returns formatted results.

SQL queries are **auto-generated** from ontology property mappings and
action param conceptPropertyRef — no hardcoded queries.

Mappings source: data/ontology/manufacturing.agent-bundle.json
"""

import json
import os
import re
import sqlite3
from typing import Any, Dict, Optional

from app.core.logger import log


class ActionExecutor:
    """Executes ontology actions against the local SQLite MES database.

    Query-type actions (outputType == "list") auto-generate SELECT from
    ontology mappings. Write-type actions (create/record) use parameterised
    INSERT patterns driven by the same mappings.
    """

    def __init__(self):
        self._db_path = ""
        self._bundle: Optional[dict] = None
        self._concepts: Dict[str, dict] = {}
        self._sigs: Dict[str, dict] = {}
        self._mappings: list = []

    # ── Initialisation ──────────────────────────────────────────────

    @property
    def db_path(self) -> str:
        if not self._db_path:
            self._db_path = os.path.normpath(os.path.join(
                os.path.dirname(__file__), "..", "..", "data", "mes_demo.db",
            ))
        return self._db_path

    def _load_bundle(self) -> dict:
        if self._bundle is not None:
            return self._bundle
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "ontology",
            "manufacturing.agent-bundle.json",
        ))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                self._bundle = json.load(fh)
            self._concepts = {
                c["name"]: c for c in self._bundle.get("concepts", [])
            }
            self._sigs = {
                s["functionName"]: s
                for s in self._bundle.get("actionSignatures", [])
            }
            self._mappings = self._bundle.get("mappings", [])
            log.info(
                f"[ActionExecutor] loaded bundle: "
                f"{len(self._concepts)} concepts, {len(self._sigs)} actions, "
                f"{len(self._mappings)} mappings"
            )
        except Exception as e:
            log.warning(f"[ActionExecutor] failed to load bundle: {e}")
            self._bundle = {}
        return self._bundle

    # ── Public API ───────────────────────────────────────────────────

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        self._load_bundle()

        sig = self._sigs.get(tool_name)
        if not sig:
            # Fallback to legacy handlers for unmapped tools
            handler = self._FALLBACK_HANDLERS.get(tool_name)
            if handler:
                return handler(self, arguments)
            return f"[未实现] 工具 {tool_name} 尚未绑定执行逻辑"

        try:
            if sig.get("outputType") == "list" or tool_name.endswith("_query"):
                return self._execute_query(sig, arguments)
            return self._execute_write(sig, arguments)
        except Exception as e:
            log.error(f"Action {tool_name} execution failed: {e}", exc_info=True)
            return f"[工具执行失败] {tool_name}: {e}"

    def list_handlers(self) -> list:
        self._load_bundle()
        names = list(self._sigs.keys())
        names.extend(self._FALLBACK_HANDLERS.keys())
        return sorted(set(names))

    def execute_structured(
        self, tool_name: str, arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        result_text = self.execute(tool_name, arguments)
        row_count = 0
        for line in (result_text or "").split("\n"):
            if line.startswith("找到 ") and " 条" in line:
                try:
                    row_count = int(line.split(" ")[1])
                except ValueError:
                    pass
                break
        return {
            "tool": tool_name,
            "arguments": arguments if isinstance(arguments, dict) else {},
            "result": result_text,
            "rowCount": row_count,
            "source": "mes_demo.db",
        }

    async def execute_structured_async(
        self, tool_name: str, arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute via DataBackend (Neo4j → API → SQLite fallback chain).

        Uses ontology action definitions to build the query, then delegates
        to the configured DataBackend for execution.
        """
        self._load_bundle()
        sig = self._sigs.get(tool_name)
        if not sig:
            # Fallback to legacy handlers
            result_text = self.execute(tool_name, arguments)
            return {
                "tool": tool_name,
                "arguments": arguments,
                "result": result_text,
                "rowCount": 0,
                "source": "mes_demo.db",
            }

        from app.services.data_backend import data_backend

        concept_name = sig["conceptName"]
        backend_name = "mes_demo.db"

        if sig.get("outputType") == "list" or tool_name.endswith("_query"):
            # Query path: DataBackend.query(concept, filters)
            result_text, row_count, backend_name = await self._query_via_backend(
                concept_name, sig, arguments, data_backend,
            )
        else:
            # Write path: DataBackend.create(concept, data)
            result_text, row_count, backend_name = await self._create_via_backend(
                concept_name, sig, arguments, data_backend,
            )

        return {
            "tool": tool_name,
            "arguments": arguments if isinstance(arguments, dict) else {},
            "result": result_text,
            "rowCount": row_count,
            "source": backend_name,
        }

    async def _query_via_backend(
        self, concept_name: str, sig: dict, args: dict, backend,
    ) -> tuple[str, int, str]:
        """Build filters from action params and query via DataBackend."""
        filters = {}
        for p_name, p_value in args.items():
            if p_value is None or p_value == "":
                continue
            # Synthetic params from concept-level entity resolution
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
                    _, prop_name = prop_ref.split(".", 1)
                    filters[prop_name] = p_value
                else:
                    filters[p_name] = p_value
            else:
                filters[p_name] = p_value

        records = await backend.query(concept_name, filters)
        if not records:
            return "未找到匹配的记录。", 0, "neo4j"

        lines = [f"找到 {len(records)} 条记录："]
        for r in records:
            parts = []
            for k, v in r.items():
                if v is not None:
                    parts.append(str(v))
            lines.append("  " + " | ".join(parts))

        health = await backend.health()
        backend_name = health.get("primary", "unknown")
        return "\n".join(lines), len(records), backend_name

    async def _create_via_backend(
        self, concept_name: str, sig: dict, args: dict, backend,
    ) -> tuple[str, int, str]:
        """Create entity via DataBackend."""
        result = await backend.create(concept_name, dict(args))
        if "error" in result:
            # Fallback to sync execute
            result_text = self.execute(sig["functionName"], args)
            return result_text, 0, "mes_demo.db"

        label_kw = args.get("productName") or args.get("result") or ""
        result_id = result.get("id", "")
        health = await backend.health()
        backend_name = health.get("primary", "unknown")
        return (
            f"已创建 {sig['conceptLabel']} {result_id}: {label_kw}",
            1,
            backend_name,
        )

    # ── Query generation (mappings-driven) ──────────────────────────

    def _execute_query(self, sig: dict, args: dict) -> str:
        """Generate and execute a SELECT query from ontology mappings."""
        concept_name = sig["conceptName"]
        concept = self._concepts.get(concept_name, {})
        main_table = self._CONCEPT_TABLE.get(concept_name, concept_name.lower())

        select_cols: list[str] = []
        join_clauses: list[str] = []
        where_parts: list[str] = []
        where_params: list = []

        # ── SELECT: all mapped columns from main concept ──
        mapped = self._get_concept_columns(concept_name)
        if mapped:
            for col in mapped:
                select_cols.append(f"t.{col}")
        else:
            select_cols.append("t.*")

        # ── Process params → WHERE / JOIN ──
        used_joins: set = set()

        for p_name, p_value in args.items():
            if p_value is None or p_value == "":
                continue

            param_def = next(
                (p for p in sig.get("params", []) if p["name"] == p_name), None,
            )
            if not param_def:
                # No param definition — still try direct column match
                col = self._find_column_for_param(concept_name, p_name)
                if col:
                    where_parts.append(f"t.{col} = ?")
                    where_params.append(p_value)
                continue

            prop_ref = param_def.get("conceptPropertyRef", "")
            if not prop_ref or "." not in prop_ref:
                # Try to match param name to a concept property
                col = self._find_column_for_param(concept_name, p_name)
                if col:
                    where_parts.append(f"t.{col} = ?")
                    where_params.append(p_value)
                continue

            target_concept, target_prop = prop_ref.split(".", 1)

            if target_concept == concept_name:
                # Same-concept param: direct column filter
                col = self._find_column(target_concept, target_prop)
                if col:
                    clause, val = self._build_where(
                        "t", col, p_value, param_def["type"],
                    )
                    where_parts.append(clause)
                    where_params.append(val)
            else:
                # Cross-concept param: need JOIN
                fk_info = self._resolve_fk_path(concept_name, target_concept)
                if fk_info:
                    join_alias = fk_info["alias"]
                    join_key = fk_info["key"]
                    if join_key not in used_joins:
                        join_clauses.append(
                            f"JOIN {fk_info['target_table']} {join_alias} "
                            f"ON t.{fk_info['fk_col']} = {join_alias}.{fk_info['pk_col']}"
                        )
                        used_joins.add(join_key)

                    col = self._find_column(target_concept, target_prop)
                    if col:
                        clause, val = self._build_where(
                            join_alias, col, p_value, param_def["type"],
                        )
                        where_parts.append(clause)
                        where_params.append(val)

        # ── Assemble SQL ──
        sql = f"SELECT {', '.join(select_cols)} FROM {main_table} t"
        for jc in join_clauses:
            sql += f" {jc}"
        sql += " WHERE 1=1"
        for wp in where_parts:
            sql += f" AND {wp}"
        sql += " ORDER BY t.id"

        log.info(
            f"[ActionExecutor] generated SQL: {sql}  |  params: {where_params}",
        )

        rows = self._query(sql, where_params)
        if not rows:
            return "未找到匹配的记录。"

        fmt_rows = self._format_rows(sql, rows)
        return f"找到 {len(rows)} 条记录：\n" + "\n".join(fmt_rows)

    def _execute_write(self, sig: dict, args: dict) -> str:
        """Execute a write-type action (INSERT-based)."""
        concept_name = sig["conceptName"]
        table = self._CONCEPT_TABLE.get(concept_name, concept_name.lower())

        if sig["actionName"] == "create":
            return self._execute_insert(concept_name, table, sig, args)
        if sig["actionName"] == "record":
            return self._execute_insert(concept_name, table, sig, args)

        return f"[未实现] 写操作 {sig['functionName']}"

    def _execute_insert(
        self, concept_name: str, table: str, sig: dict, args: dict,
    ) -> str:
        """INSERT from params plus cross-concept FK resolution."""
        columns: list[str] = []
        values: list = []

        # Resolve FKs first (cross-concept params)
        for p_name, p_value in args.items():
            param_def = next(
                (p for p in sig.get("params", []) if p["name"] == p_name), None,
            )
            if not param_def:
                continue

            prop_ref = param_def.get("conceptPropertyRef", "")
            if prop_ref and "." in prop_ref:
                target_concept, target_prop = prop_ref.split(".", 1)
                if target_concept != concept_name:
                    # FK resolution: look up target entity
                    target_table = self._CONCEPT_TABLE.get(
                        target_concept, target_concept.lower(),
                    )
                    target_col = self._find_column(target_concept, target_prop)
                    if target_table and target_col:
                        rows = self._query(
                            f"SELECT id FROM {target_table} WHERE {target_col} LIKE ?",
                            [f"%{p_value}%"],
                        )
                        if not rows:
                            return f"未找到 {param_def['label']}: {p_value}"
                        fk_col = self._infer_fk_column(target_concept)
                        columns.append(fk_col)
                        values.append(rows[0][0])
                        continue

            # Direct value
            col = self._find_column_for_param(concept_name, p_name)
            if col:
                columns.append(col)
                values.append(p_value)

        # Generate ID
        count_rows = self._query(f"SELECT COUNT(*) FROM {table}")
        count = count_rows[0][0] if count_rows else 0
        prefix = self._infer_id_prefix(concept_name)
        new_id = f"{prefix}-{count + 1:03d}"

        columns.insert(0, "id")
        values.insert(0, new_id)

        placeholders = ", ".join("?" for _ in values)
        cols_str = ", ".join(columns)
        self._execute(
            f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})",
            values,
        )

        # Find label for display
        label_kw = args.get("productName") or args.get("result") or ""
        return f"已创建 {sig['conceptLabel']} {new_id}: {label_kw}"

    # ── Ontology helpers ─────────────────────────────────────────────

    _CONCEPT_TABLE = {
        "WorkOrder": "work_orders",
        "Product": "products",
        "QualityCheck": "quality_checks",
        "Equipment": "equipment",
        "Material": "materials",
        "Routing": "routings",
        "WorkCenter": "work_centers",
        "Operation": "operations",
        "ProductionLine": "production_lines",
        "WorkStation": "work_stations",
        "Employee": "employees",
    }

    @classmethod
    def concept_to_table(cls, concept_name: str) -> str:
        return cls._CONCEPT_TABLE.get(concept_name, concept_name.lower())

    def _get_concept_columns(self, concept_name: str) -> list[str]:
        """Return column names for a concept from its property mappings."""
        cols: list[str] = []
        for m in self._mappings:
            if m.get("concept") == concept_name:
                cols.append(m["column"])
        return cols

    def _find_column(self, concept_name: str, prop_name: str) -> Optional[str]:
        """Find the DB column for a concept property."""
        for m in self._mappings:
            if m.get("concept") == concept_name and m.get("property") == prop_name:
                return m["column"]
        return None

    def _find_column_for_param(
        self, concept_name: str, param_name: str,
    ) -> Optional[str]:
        """Infer column from param name (snake_case guess)."""
        # Try exact property match first
        col = self._find_column(concept_name, param_name)
        if col:
            return col
        # Try camelCase → snake_case
        guessed = re.sub(r"(?<!^)(?=[A-Z])", "_", param_name).lower()
        # Check if guessed column exists in the table
        table = self._CONCEPT_TABLE.get(concept_name, concept_name.lower())
        try:
            cols = self._get_db_columns(table)
            if guessed in cols:
                return guessed
        except Exception:
            pass
        return None

    def _resolve_fk_path(
        self, from_concept: str, to_concept: str,
    ) -> Optional[dict]:
        """Resolve a FK path from one concept to another via relations.

        Returns dict with alias, fk_col, pk_col, target_table, key or None.
        """
        from_c = self._concepts.get(from_concept, {})
        for rel in from_c.get("relations", []):
            if rel.get("target") == to_concept:
                target_table = self._CONCEPT_TABLE.get(
                    to_concept, to_concept.lower(),
                )
                fk_col = self._infer_fk_column(to_concept)
                pk_col = "id"
                alias = to_concept[0].lower()
                return {
                    "alias": alias,
                    "fk_col": fk_col,
                    "pk_col": pk_col,
                    "target_table": target_table,
                    "key": f"{from_concept}->{to_concept}",
                }
        # Try reverse: check target concept's relations that point to from_concept
        to_c = self._concepts.get(to_concept, {})
        for rel in to_c.get("relations", []):
            if rel.get("target") == from_concept:
                target_table = self._CONCEPT_TABLE.get(
                    from_concept, from_concept.lower(),
                )
                fk_col = self._infer_fk_column(from_concept)
                pk_col = "id"
                alias = from_concept[0].lower()
                return {
                    "alias": alias,
                    "fk_col": fk_col,
                    "pk_col": pk_col,
                    "target_table": target_table,
                    "key": f"{to_concept}<-{from_concept}",
                }
        return None

    def _infer_fk_column(self, concept_name: str) -> str:
        """Infer FK column name from concept: Product → product_id."""
        return re.sub(r"(?<!^)(?=[A-Z])", "_", concept_name).lower() + "_id"

    def _infer_id_prefix(self, concept_name: str) -> str:
        """Infer ID prefix: WorkOrder→WO, QualityCheck→QC, Equipment→EQUIP."""
        abbreviations = {
            "WorkOrder": "WO", "QualityCheck": "QC", "Equipment": "EQUIP",
            "Material": "MAT", "Product": "PROD", "Operation": "OP",
            "WorkCenter": "WC", "Routing": "ROUTE",
        }
        if concept_name in abbreviations:
            return abbreviations[concept_name]
        return concept_name[:4].upper()

    def _build_where(
        self, alias: str, col: str, value: Any, param_type: str,
    ) -> tuple:
        """Build a WHERE clause. Uses LIKE for string text-search types."""
        if param_type == "string":
            return f"{alias}.{col} LIKE ?", f"%{value}%"
        return f"{alias}.{col} = ?", value

    def _get_db_columns(self, table: str) -> list:
        try:
            return self._query_cols(f"PRAGMA table_info({table})")
        except Exception:
            return []

    # ── Result formatting ────────────────────────────────────────────

    def _format_rows(self, sql: str, rows: list) -> list[str]:
        """Format rows as pipe-separated strings."""
        cols = self._get_select_columns(sql)
        lines = []
        for r in rows:
            parts = []
            for i, val in enumerate(r):
                label = cols[i] if i < len(cols) else f"c{i}"
                if label in ("id",) or "id" in label:
                    parts.append(str(val))
                elif val is not None:
                    parts.append(str(val))
            lines.append("  " + " | ".join(parts))
        return lines

    def _get_select_columns(self, sql: str) -> list[str]:
        """Heuristic: extract alias-named columns from SELECT."""
        # Simple regex: SELECT t.col, j.col → ['col', 'col']
        m = re.search(r"SELECT\s+(.+?)\s+FROM", sql, re.IGNORECASE)
        if not m:
            return []
        cols = []
        for part in m.group(1).split(","):
            part = part.strip()
            # t.col or t.col AS name → col
            alias = part.split(" AS ")[-1].strip()
            dot = alias.split(".")
            cols.append(dot[-1] if len(dot) > 1 else alias)
        return cols

    # ── Entity lookup (L3 graph traversal) ───────────────────────────

    def lookup_entity(self, concept_name: str, key_value: str) -> Optional[dict]:
        table = self.concept_to_table(concept_name)
        if not table:
            return None
        pk = "id"
        rows = self._query(f"SELECT * FROM {table} WHERE {pk} = ?", [key_value])
        if not rows:
            cols = self._get_db_columns(table)
            if "name" in cols:
                rows = self._query(
                    f"SELECT * FROM {table} WHERE name LIKE ?",
                    [f"%{key_value}%"],
                )
        if not rows:
            return None
        cols = self._get_db_columns(table)
        return dict(zip(cols, rows[0]))

    def resolve_fk(
        self, table: str, row: dict, target_concept: str,
    ) -> Optional[dict]:
        target_table = self.concept_to_table(target_concept)
        if not target_table:
            return None
        fk_col = self._infer_fk_column(target_concept)
        fk_val = row.get(fk_col)
        if not fk_val:
            return None
        rows = self._query(
            f"SELECT * FROM {target_table} WHERE id = ?", [fk_val],
        )
        if not rows:
            return None
        cols = self._get_db_columns(target_table)
        return dict(zip(cols, rows[0]))

    # ── DB helpers ───────────────────────────────────────────────────

    def _query(self, sql: str, params: Optional[list] = None):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, params or []).fetchall()
        finally:
            conn.close()

    def _query_cols(self, sql: str) -> list:
        """Run PRAGMA and return first column values."""
        rows = self._query(sql)
        return [r[1] for r in rows] if rows else []

    def _execute(self, sql: str, params: Optional[list] = None):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(sql, params or [])
            conn.commit()
        finally:
            conn.close()

    # ── Legacy fallback (kept during transition) ─────────────────────

    def _handle_workorder_query(self, args: dict) -> str:
        status = args.get("status", "")
        product_name = args.get("productName", "")
        query = """
            SELECT wo.id, p.name as product, wo.quantity,
                   wo.start_date, wo.due_date, wo.status
            FROM work_orders wo
            JOIN products p ON wo.product_id = p.id
            WHERE 1=1
        """
        params: list = []
        if status:
            query += " AND wo.status = ?"
            params.append(status)
        if product_name:
            query += " AND p.name LIKE ?"
            params.append(f"%{product_name}%")
        rows = self._query(query, params)
        if not rows:
            return "未找到匹配的工单。"
        lines = [f"找到 {len(rows)} 条工单："]
        for r in rows:
            lines.append(f"  {r[0]} | {r[1]} | {r[2]}件 | {r[3]}~{r[4]} | {r[5]}")
        return "\n".join(lines)

    def _handle_workorder_create(self, args: dict) -> str:
        product_name = args.get("productName", "")
        quantity = args.get("quantity", 0)
        due_date = args.get("dueDate", "")
        rows = self._query(
            "SELECT id, name FROM products WHERE name LIKE ?",
            [f"%{product_name}%"],
        )
        if not rows:
            return f"未找到产品: {product_name}"
        product_id = rows[0][0]
        count = self._query("SELECT COUNT(*) FROM work_orders")[0][0]
        new_id = f"WO-{count + 1:03d}"
        self._execute(
            "INSERT INTO work_orders (id, product_id, quantity, start_date, due_date, status) "
            "VALUES (?,?,?,date('now'),?,?)",
            [new_id, product_id, quantity, due_date, "待生产"],
        )
        return f"已创建工单 {new_id}: {rows[0][1]} x{quantity}, 完工日期 {due_date}"

    def _handle_qualitycheck_record(self, args: dict) -> str:
        work_order_id = args.get("workOrderId", "")
        result = args.get("result", "")
        wo = self._query(
            "SELECT id, status FROM work_orders WHERE id = ?", [work_order_id],
        )
        if not wo:
            return f"工单 {work_order_id} 不存在"
        count = self._query("SELECT COUNT(*) FROM quality_checks")[0][0]
        new_id = f"QC-{count + 1:03d}"
        self._execute(
            "INSERT INTO quality_checks (id, work_order_id, result, check_date) "
            "VALUES (?,?,?,date('now'))",
            [new_id, work_order_id, result],
        )
        return f"已记录质检 {new_id}: 工单 {work_order_id} 结果为 {result}"

    def _handle_material_query(self, args: dict) -> str:
        material_type = args.get("materialType", "")
        material_name = args.get("materialName", "")
        query = (
            "SELECT id, name, type, stock, unit FROM materials WHERE 1=1"
        )
        params: list = []
        if material_type:
            query += " AND type = ?"
            params.append(material_type)
        if material_name:
            query += " AND name LIKE ?"
            params.append(f"%{material_name}%")
        rows = self._query(query, params)
        if not rows:
            return "未找到匹配的物料。"
        lines = [f"找到 {len(rows)} 条物料："]
        for r in rows:
            lines.append(f"  {r[0]} | {r[1]} | {r[2]} | 库存{r[3]}{r[4]}")
        return "\n".join(lines)

    def _handle_equipment_query(self, args: dict) -> str:
        status = args.get("status", "")
        query = """
            SELECT e.id, e.name, e.status, e.last_maintenance, e.power_kw
            FROM equipment e
            WHERE 1=1
        """
        params: list = []
        if status:
            query += " AND e.status = ?"
            params.append(status)
        rows = self._query(query, params)
        if not rows:
            return "未找到匹配的设备。"
        lines = [f"找到 {len(rows)} 台设备："]
        for r in rows:
            lines.append(
                f"  {r[0]} | {r[1]} | 状态:{r[2]} | 上次保养:{r[3]} | 功率:{r[4]}kW",
            )
        return "\n".join(lines)

    def _handle_operation_query(self, args: dict) -> str:
        routing_id = args.get("routingId", "")
        query = """
            SELECT o.id, o.name, o.sequence_no, o.cycle_time,
                   o.setup_time, wc.name
            FROM operations o
            LEFT JOIN work_centers wc ON o.work_center_id = wc.id
            WHERE 1=1
        """
        params: list = []
        if routing_id:
            query += " AND o.routing_id = ?"
            params.append(routing_id)
        query += " ORDER BY o.sequence_no"
        rows = self._query(query, params)
        if not rows:
            return "未找到匹配的工序。"
        lines = [f"找到 {len(rows)} 道工序："]
        for r in rows:
            lines.append(
                f"  {r[0]} | 序号{r[2]} {r[1]} | 节拍{r[3]}s | 准备{r[4]}min | 工作中心:{r[5]}",
            )
        return "\n".join(lines)

    def _handle_qualitycheck_query(self, args: dict) -> str:
        work_order_id = args.get("workOrderId", "")
        query = """
            SELECT qc.id, qc.work_order_id, qc.result, qc.check_date, qc.inspector
            FROM quality_checks qc
            WHERE 1=1
        """
        params: list = []
        if work_order_id:
            query += " AND qc.work_order_id = ?"
            params.append(work_order_id)
        query += " ORDER BY qc.check_date DESC"
        rows = self._query(query, params)
        if not rows:
            return "未找到匹配的质检记录。"
        lines = [f"找到 {len(rows)} 条质检记录："]
        for r in rows:
            lines.append(
                f"  {r[0]} | 工单{r[1]} | 结果:{r[2]} | 日期:{r[3]} | 检测人:{r[4]}",
            )
        return "\n".join(lines)

    _FALLBACK_HANDLERS = {
        "WorkOrder_query": _handle_workorder_query,
        "WorkOrder_create": _handle_workorder_create,
        "QualityCheck_record": _handle_qualitycheck_record,
        "Material_query": _handle_material_query,
        "Equipment_query": _handle_equipment_query,
        "Operation_query": _handle_operation_query,
        "QualityCheck_query": _handle_qualitycheck_query,
    }


action_executor = ActionExecutor()
