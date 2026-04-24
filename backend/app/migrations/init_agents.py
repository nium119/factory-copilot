"""
Agent 表初始化迁移脚本
创建 agents 表并插入默认 Agent 数据
"""
import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "agent.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '🤖',
    color TEXT DEFAULT '#6c5ce7',
    description TEXT DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT 1,
    roles TEXT DEFAULT '[]',
    keywords TEXT DEFAULT '[]',
    system_prompt TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

DEFAULT_AGENTS = [
    {
        "name": "general",
        "display_name": "智能助手",
        "icon": "🤖",
        "color": "#6c5ce7",
        "description": "通用 AI 助手，支持搜索、企业信息查询、图表生成等",
        "enabled": True,
        "roles": [],
        "keywords": [],
        "sort_order": 100,
    },
    {
        "name": "scheduling",
        "display_name": "排产助手",
        "icon": "📋",
        "color": "#0984e3",
        "description": "生产计划排期",
        "enabled": True,
        "roles": [],
        "keywords": ["排产", "排期", "计划", "调度", "排班", "产线安排", "生产计划", "工单排程", "产能"],
        "sort_order": 90,
    },
    {
        "name": "quality",
        "display_name": "质检助手",
        "icon": "🔍",
        "color": "#e17055",
        "description": "质量检测分析",
        "enabled": True,
        "roles": [],
        "keywords": ["质检", "质量", "不合格", "次品", "良率", "检测", "抽检", "返工", "报废", "不良", "合格率", "SPC"],
        "sort_order": 85,
    },
    {
        "name": "equipment",
        "display_name": "设备助手",
        "icon": "⚙️",
        "color": "#fdcb6e",
        "description": "设备状态监控",
        "enabled": True,
        "roles": [],
        "keywords": ["设备", "故障", "维修", "保养", "停机", "OEE", "开机率", "设备状态", "点检", "巡检", "备件"],
        "sort_order": 80,
    },
    {
        "name": "inventory",
        "display_name": "库存助手",
        "icon": "📦",
        "color": "#00b894",
        "description": "库存管理",
        "enabled": True,
        "roles": [],
        "keywords": ["库存", "物料", "仓库", "缺料", "盘点", "出入库", "备料", "发料", "领料", "物料状态"],
        "sort_order": 75,
    },
    {
        "name": "process",
        "display_name": "工艺助手",
        "icon": "🔧",
        "color": "#e84393",
        "description": "工艺管理",
        "enabled": True,
        "roles": [],
        "keywords": ["工艺", "流程", "SOP", "工序", "参数", "工艺路线", "BOM", "工艺卡", "操作规范", "工艺优化"],
        "sort_order": 70,
    },
    {
        "name": "production_prep",
        "display_name": "生产准备助手",
        "icon": "📋",
        "color": "#20bf6b",
        "description": "生产准备管理，支持工序工单的物料齐套检查、设备状态确认、模具准备、质检标准查询、SOP查看、工艺卡配置与工单投产前准备",
        "enabled": True,
        "roles": [],
        "keywords": ["生产准备", "物料齐套", "设备确认", "模具准备", "质检标准", "工单准备", "投产准备", "齐套检查", "工序准备"],
        "sort_order": 65,
    },
    {
        "name": "andon",
        "display_name": "安灯助手",
        "icon": "🚨",
        "color": "#eb3b5a",
        "description": "安灯异常响应，支持异常呼叫、停线处理、问题上报、响应跟踪、异常分类（物料/设备/质量/工艺）、工单异常与应急响应管理",
        "enabled": True,
        "roles": [],
        "keywords": ["安灯", "异常", "停线", "报警", "呼叫", "问题上报", "应急响应", "故障报警", "产线异常", "andon"],
        "sort_order": 60,
    },
    {
        "name": "workstation",
        "display_name": "工位终端助手",
        "icon": "🖥️",
        "color": "#45aaf2",
        "description": "工位终端操作助手，支持工单开工/完工报工、SOP查看、工艺参数查询、物料状态、异常上报、首件确认、自检记录、人员签到与设备点检",
        "enabled": True,
        "roles": [],
        "keywords": ["工位", "终端", "报工", "开工", "完工", "SOP查看", "首件确认", "自检", "签到", "点检", "异常上报", "领料", "产量上报"],
        "sort_order": 55,
    },
]


def run_migration():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(CREATE_TABLE_SQL)

    for agent in DEFAULT_AGENTS:
        cursor.execute("SELECT name FROM agents WHERE name = ?", (agent["name"],))
        if cursor.fetchone() is None:
            cursor.execute(
                """INSERT INTO agents (name, display_name, icon, color, description, enabled, roles, keywords, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent["name"],
                    agent["display_name"],
                    agent["icon"],
                    agent["color"],
                    agent["description"],
                    int(agent["enabled"]),
                    json.dumps(agent["roles"]),
                    json.dumps(agent["keywords"]),
                    agent["sort_order"],
                ),
            )
            print(f"  Inserted agent: {agent['display_name']}")
        else:
            print(f"  Agent already exists: {agent['display_name']}")

    conn.commit()
    conn.close()
    print("Agent migration completed.")


if __name__ == "__main__":
    run_migration()
