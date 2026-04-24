from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    # 应用
    APP_NAME: str = "Factory Copilot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # API
    API_PREFIX: str = "/api"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ORIGINS: List[str] = ["*"]

    # 数据库
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/agent.db"

    # 模型密钥
    DASHSCOPE_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # Agent
    AGENT_MODEL: str = "gpt-3.5-turbo"
    AGENT_MAX_TOKENS: int = 2000
    AGENT_TEMPERATURE: float = 0.7

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

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    LOG_ROTATION: str = "100 MB"
    LOG_RETENTION: str = "30 days"

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
