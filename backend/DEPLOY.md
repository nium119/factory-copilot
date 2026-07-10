# Factory Copilot 部署文档

与本体的 Ontology-Graph 部署在同一台服务器上。

## 环境要求

- Docker 20+
- Docker Compose 2.x
- CentOS 8 / Rocky Linux / Ubuntu 20+
- 共享 Neo4j（与 OntoStudio 共用）

## 目录结构

```
/home/websites/
├── Ontology-Graph/       # 本体编辑器 (端口 9003)
│   ├── backend/
│   └── frontend/
└── factory-copilot/      # Agent 平台 (端口 9001)
    ├── backend/
    └── frontend/
```

## 首次部署

```bash
cd /home/websites/factory-copilot/backend

# 复制环境配置
cp .env.example .env
# 编辑 .env，配置 LLM API Key 等

# 构建并启动
docker compose up -d

# 验证
curl -s http://localhost:9001/api/chains/compile/status
```

## .env 关键配置

```env
# 数据库（默认 SQLite，也支持 PostgreSQL / SQL Server）
DB_TYPE=sqlite
# DB_TYPE=postgresql  # 或 mssql

# LLM
DASHSCOPE_API_KEY=your_key
DEEPSEEK_API_KEY=your_key

# Neo4j（与 OntoStudio 共用）
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4j123
NEO4J_NAMESPACE=manufacturing

# 监听端口
API_PORT=9001
```

## 日常更新

```bash
cd /home/websites/factory-copilot/backend

git pull
docker compose restart
```

前端更新：

```bash
cd /home/websites/factory-copilot/frontend

git pull
npm run build
docker compose -f ../backend/docker-compose.yml restart
```

## Docker compose

```yaml
# docker-compose.yml
services:
  factory-copilot:
    build: .
    container_name: factory-copilot
    ports:
      - "9001:9001"
    volumes:
      - ./app:/app/app
      - ./data:/app/data
      - ./config:/app/config
      - ./logs:/app/logs
      - ../frontend/dist:/frontend/dist
      - ./.env:/app/.env
    restart: unless-stopped
```

## 常用命令

```bash
# 查看状态
docker ps --filter name=factory-copilot
docker compose logs --tail 50

# 重启
docker compose restart

# 停止
docker compose down

# 完全重建
docker compose down
docker compose build --no-cache
docker compose up -d
```

## 端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Factory Copilot API | 9001 | Agent 对话和配置 |
| Factory Copilot 前端 | 5003 | 开发模式（nginx 代理到 9001） |
| OntoStudio API | 9003 | 本体编辑器 |
| Neo4j Bolt | 7687 | 图数据库（共用） |

## 首次使用

1. 在 OntoStudio (9003) 中打开本体文件，推送到 Neo4j
2. 打开 Factory Copilot (9001)，顶部选择本体图谱
3. 系统配置 → 业务域配置 → 规则推导 → 应用
4. 左侧出现业务域，点击开始对话
