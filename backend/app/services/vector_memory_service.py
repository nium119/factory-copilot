"""
SQLite 向量记忆服务
使用 SQLite 存储向量（嵌入向量序列化为 JSON），Python 端计算余弦相似度进行检索。
与主业务数据库共用同一个 SQLite 文件。
"""
import asyncio
import json
import math
import uuid
from datetime import datetime
from typing import List, Optional

import aiosqlite
from loguru import logger

from app.core.config import settings
from app.models.schemas import MemoryItem


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """计算两个向量的余弦相似度"""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_db_path() -> str:
    """从 DATABASE_URL 提取 SQLite 文件路径"""
    # "sqlite+aiosqlite:///./data/agent.db" -> "./data/agent.db"
    url = settings.DATABASE_URL
    if url.startswith("sqlite+aiosqlite:///"):
        return url.replace("sqlite+aiosqlite:///", "", 1)
    elif url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite+aiosqlite://", "", 1)
    return "./data/agent.db"


class VectorMemoryService:
    """向量记忆服务（SQLite 后端）"""

    def __init__(self):
        self.embedding_dimension = settings.EMBEDDING_DIMENSION
        self._db_path = _get_db_path()
        self._conn: Optional[aiosqlite.Connection] = None
        self._initialized = False

    async def initialize(self) -> None:
        """初始化 SQLite 连接和表"""
        try:
            self._conn = await aiosqlite.connect(self._db_path)
            await self._conn.execute("PRAGMA journal_mode=WAL")

            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_conversation_memory (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_user
                    ON agent_conversation_memory(user_id)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_conv
                    ON agent_conversation_memory(conversation_id)
            """)
            await self._conn.commit()

            self._initialized = True
            logger.info(f"VectorMemoryService initialized successfully (SQLite: {self._db_path})")

        except Exception as e:
            logger.error(f"Failed to initialize VectorMemoryService: {e}")
            raise

    async def _close(self):
        """关闭连接"""
        if self._conn:
            await self._conn.close()
            self._conn = None
            self._initialized = False

    async def store(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        role: str
    ) -> Optional[str]:
        """存储消息向量"""
        if not self._initialized or not self._conn:
            logger.warning("VectorMemoryService not initialized, skip store")
            return None

        try:
            embedding = await self._embed_query(content)
            if embedding is None:
                return None

            vector_id = str(uuid.uuid4())
            embedding_json = json.dumps(embedding)

            await self._conn.execute("""
                INSERT INTO agent_conversation_memory
                    (id, user_id, conversation_id, message_id, content, embedding, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (vector_id, user_id, conversation_id, message_id, content[:2000], embedding_json, role))
            await self._conn.commit()

            logger.debug(f"Stored vector {vector_id} for message {message_id}")
            return vector_id

        except Exception as e:
            logger.error(f"Failed to store vector: {e}")
            return None

    async def retrieve(
        self,
        user_id: str,
        query: str,
        conversation_id: Optional[str] = None,
        top_k: int = 5,
        similarity_threshold: float = 0.7
    ) -> List[MemoryItem]:
        """检索相似记忆"""
        if not self._initialized or not self._conn:
            logger.warning("VectorMemoryService not initialized, return empty list")
            return []

        try:
            query_embedding = await self._embed_query(query)
            if query_embedding is None:
                return []

            if conversation_id:
                cursor = await self._conn.execute("""
                    SELECT id, content, role, conversation_id, created_at, embedding
                    FROM agent_conversation_memory
                    WHERE user_id = ? AND conversation_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, conversation_id, top_k * 10))
            else:
                cursor = await self._conn.execute("""
                    SELECT id, content, role, conversation_id, created_at, embedding
                    FROM agent_conversation_memory
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, top_k * 10))

            rows = await cursor.fetchall()

            # Python 端计算余弦相似度并排序
            scored = []
            for row in rows:
                embedding = json.loads(row[5])
                sim = _cosine_similarity(query_embedding, embedding)
                if sim >= similarity_threshold:
                    scored.append((sim, row))

            scored.sort(key=lambda x: x[0], reverse=True)
            scored = scored[:top_k]

            memories = []
            for sim, row in scored:
                created_at = datetime.fromisoformat(row[4]) if row[4] else datetime.utcnow()
                memories.append(MemoryItem(
                    id=row[0],
                    content=row[1],
                    role=row[2],
                    conversation_id=row[3],
                    similarity=sim,
                    created_at=created_at
                ))

            logger.debug(f"Retrieved {len(memories)} memories for query")
            return memories

        except Exception as e:
            logger.error(f"Failed to retrieve memories: {e}")
            return []

    async def check_duplicate(
        self,
        user_id: str,
        content: str,
        similarity_threshold: float = 0.95
    ) -> bool:
        """检查是否存在重复内容"""
        memories = await self.retrieve(
            user_id=user_id,
            query=content,
            top_k=1,
            similarity_threshold=similarity_threshold
        )
        return len(memories) > 0

    async def delete_by_conversation(self, conversation_id: str) -> bool:
        """删除会话的所有向量"""
        if not self._initialized or not self._conn:
            return False

        try:
            await self._conn.execute("""
                DELETE FROM agent_conversation_memory
                WHERE conversation_id = ?
            """, (conversation_id,))
            await self._conn.commit()

            logger.info(f"Deleted vectors for conversation {conversation_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete vectors: {e}")
            return False

    async def delete_by_message(self, message_id: str) -> bool:
        """删除消息的向量"""
        if not self._initialized or not self._conn:
            return False

        try:
            await self._conn.execute("""
                DELETE FROM agent_conversation_memory
                WHERE message_id = ?
            """, (message_id,))
            await self._conn.commit()

            logger.debug(f"Deleted vector for message {message_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete vector: {e}")
            return False

    async def retrieve_with_fallback(
        self,
        user_id: str,
        query: str,
        conversation_id: Optional[str] = None,
        top_k: int = 5,
        similarity_threshold: float = 0.7
    ) -> List[MemoryItem]:
        """带降级策略的检索"""
        try:
            return await self.retrieve(
                user_id=user_id,
                query=query,
                conversation_id=conversation_id,
                top_k=top_k,
                similarity_threshold=similarity_threshold
            )
        except Exception as e:
            logger.warning(f"Memory retrieval failed, fallback to empty: {e}")
            return []

    async def _embed_query(self, text: str) -> Optional[list[float]]:
        """生成文本的嵌入向量。使用 DashScope 的 text-embedding-v3 模型。"""
        try:
            from langchain_community.embeddings import DashScopeEmbeddings

            from app.core.model_config import get_embedding_key, get_embedding_model
            embedding_key = get_embedding_key()
            if not embedding_key:
                logger.warning("Embedding API Key 未配置，请先在模型配置中为千问模型设置 api_key")
                return None

            embeddings = DashScopeEmbeddings(
                model=get_embedding_model(),
                dashscope_api_key=embedding_key,
            )

            embedding = await asyncio.to_thread(embeddings.embed_query, text)
            return embedding

        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            return None


# 全局实例
vector_memory_service = VectorMemoryService()
