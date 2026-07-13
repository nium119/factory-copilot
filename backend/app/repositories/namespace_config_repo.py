"""命名空间配置 Repository"""
import json
import datetime
from typing import Optional
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.namespace_config import NamespaceConfig

class NamespaceConfigRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, namespace: str, config_type: str) -> Optional[dict]:
        """读取配置，返回解析后的 dict，不存在返回空 dict。"""
        result = await self.db.execute(
            select(NamespaceConfig.config_data).where(
                NamespaceConfig.namespace == namespace,
                NamespaceConfig.config_type == config_type,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            try: return json.loads(row)
            except (json.JSONDecodeError, TypeError): return {}
        return {}

    async def save(self, namespace: str, config_type: str, config: dict):
        """写入配置，自动备份旧版本。"""
        # 读取旧配置
        old = await self.get(namespace, config_type)
        # 只有旧配置包含实质数据（不只是 mode/_applied 标记）且与新不同时才备份
        has_real = old and any(k not in ("mode", "_applied") for k in old)
        if has_real and old != config:
            ts = datetime.datetime.now().strftime("%m-%d %H:%M")
            backup_key = f"{config_type}_backup_{ts}"
            # 已有同名备份则跳过（同一分钟内多次保存），但仍需执行下面的 UPSERT
            existing = (await self.db.execute(
                select(NamespaceConfig).where(NamespaceConfig.namespace == namespace, NamespaceConfig.config_type == backup_key)
            )).scalar_one_or_none()
            if not existing:
                backup = NamespaceConfig(
                    namespace=namespace,
                    config_type=backup_key,
                    config_data=json.dumps(old, ensure_ascii=False),
                    updated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                self.db.add(backup)
        # UPSERT 当前配置
        data_str = json.dumps(config, ensure_ascii=False)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.db.execute(
            update(NamespaceConfig).where(
                NamespaceConfig.namespace == namespace,
                NamespaceConfig.config_type == config_type,
            ).values(config_data=data_str, updated_at=now)
        )
        if (await self.db.execute(
            select(NamespaceConfig).where(
                NamespaceConfig.namespace == namespace,
                NamespaceConfig.config_type == config_type,
            )
        )).scalar_one_or_none() is None:
            self.db.add(NamespaceConfig(
                namespace=namespace, config_type=config_type,
                config_data=data_str, updated_at=now,
            ))
        await self.db.commit()

    async def delete(self, namespace: str, config_type: str):
        await self.db.execute(
            delete(NamespaceConfig).where(
                NamespaceConfig.namespace == namespace,
                NamespaceConfig.config_type == config_type,
            )
        )
        await self.db.commit()

    async def list_backups(self, namespace: str, prefix: str, limit: int = 50) -> list[dict]:
        """列出备份版本"""
        result = await self.db.execute(
            select(NamespaceConfig).where(
                NamespaceConfig.namespace == namespace,
                NamespaceConfig.config_type.like(f"{prefix}_backup_%"),
            ).order_by(NamespaceConfig.updated_at.desc()).limit(limit)
        )
        return [{"type": r.config_type, "data": json.loads(r.config_data) if r.config_data else {}} for r in result.scalars().all()]
