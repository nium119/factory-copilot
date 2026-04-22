"""
Milvus初始化脚本
创建Collection和索引
"""
import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.vector_memory_service import vector_memory_service
from app.core.config import settings
from loguru import logger


async def init_milvus():
    """初始化Milvus"""
    try:
        logger.info("开始初始化Milvus...")

        # 检查是否启用Milvus
        if not settings.MILVUS_ENABLED:
            logger.warning("Milvus未启用,跳过初始化")
            return

        # 初始化向量记忆服务
        await vector_memory_service.initialize()

        logger.info("Milvus初始化成功!")

        # 验证连接
        if vector_memory_service._initialized:
            logger.info(f"Collection: {settings.MILVUS_COLLECTION}")
            logger.info(f"Embedding Model: {settings.EMBEDDING_MODEL}")
            logger.info(f"Embedding Dimension: {settings.EMBEDDING_DIMENSION}")
        else:
            logger.error("Milvus初始化失败")

    except Exception as e:
        logger.error(f"Milvus初始化失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(init_milvus())
