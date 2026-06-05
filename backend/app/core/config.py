from typing import List

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", case_sensitive=True)
    # 应用
    APP_NAME: str = "Factory Copilot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # API
    API_PREFIX: str = "/api"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8001
    CORS_ORIGINS: List[str] = ["http://localhost:3001"]

    # 数据库
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/agent.db"

    # 模型密钥
    DASHSCOPE_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # Agent
    AGENT_MODEL: str = "gpt-3.5-turbo"
    ROUTING_METHOD: str = "llm"  # Agent 路由策略（统一使用 LLM 语义路由）
    AGENT_MAX_TOKENS: int = 2000
    AGENT_TEMPERATURE: float = 0.7
    AGENT_FALLBACK_ENABLED: bool = True  # 本体路由无匹配时，回退到 LLM Agent 自由工具调用

    # 记忆（短期 + 长期 + 摘要）
    MEMORY_ENABLED: bool = True              # 是否启用长期记忆
    MEMORY_TOP_K: int = 5                    # 检索返回的记忆条数
    MEMORY_SIMILARITY_THRESHOLD: float = 0.7 # 向量检索相似度阈值
    MEMORY_AUTO_INJECT: bool = True          # 是否自动注入记忆到系统提示词
    MEMORY_DUPLICATE_THRESHOLD: float = 0.95 # 向量去重阈值
    MAX_HISTORY_LENGTH: int = 50             # 短期记忆完整保留条数（超过后旧消息被压缩为摘要）
    SUMMARY_MAX_TOKENS: int = 500            # 摘要压缩最大字数
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIMENSION: int = 1024

    # MCP 集成
    # JSON 数组，每项: {"name":"...", "command":"...", "args":["..."]}
    MCP_SERVERS: str = '[]'

    # A2A 外部 Agent
    # JSON 数组，每项: {"name":"...", "display_name":"...", "command":"...", "args":["..."]}
    A2A_EXTERNAL_AGENTS: str = '[]'

    # 资源感知优化
    RESOURCE_AWARE_ENABLED: bool = True
    MAX_CONCURRENT_REQUESTS: int = 10

    # API 鉴权（可选，留空则不启用）
    API_AUTH_TOKEN: str = ""

    # Neo4j (图数据库 — Ontology 元数据 + 业务数据)
    NEO4J_ENABLED: bool = True
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4j123"
    NEO4J_DATABASE: str = "neo4j"
    NEO4J_NAMESPACE: str = ""  # 项目命名空间，为空时不过滤（兼容旧数据）
    NEO4J_MAX_CONNECTION_LIFETIME: int = 3600
    NEO4J_MAX_CONNECTION_POOL_SIZE: int = 10
    NEO4J_CONNECTION_TIMEOUT: int = 10

    # Ontology 缓存刷新策略（生产环境可调大）
    ONTOLOGY_CACHE_TTL: int = 3           # 轻量指纹检查间隔（秒）
    ONTOLOGY_FORCE_RELOAD: int = 30       # 强制完整重载间隔（秒）
    ONTOLOGY_RELOAD_RETRY_DELAY: int = 2  # 重载失败后重试间隔（秒）
    ONTOLOGY_RELOAD_MAX_FAILURES: int = 5 # 连续失败多少次后停止自动刷新
    ONTOLOGY_MAX_STALENESS: int = 300     # 缓存最大允许过期时间（秒），超过则 health check 返回 503

    # 业务数据后端 (neo4j | sqlite | api)
    DATA_BACKEND: str = "neo4j"

    # MES API (DATA_BACKEND=api 时使用)
    MES_API_BASE_URL: str = ""
    MES_API_TOKEN: str = ""
    MES_PLANT_CODE: str = ""

    # MES CLI
    MES_API_ENABLED: bool = False
    MES_CLI_PATH: str = "mes-cli"

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    LOG_ROTATION: str = "100 MB"
    LOG_RETENTION: str = "30 days"

settings = Settings()
