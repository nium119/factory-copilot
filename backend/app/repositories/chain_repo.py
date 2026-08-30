"""链条 Repository"""
import json
from typing import Optional, List
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.chain import Chain, ChainStep

class ChainRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self, namespace: str = "") -> List[Chain]:
        """列出链条。namespace 非空时只返回该本体图谱项目下的链。"""
        stmt = select(Chain).options(selectinload(Chain.steps)).order_by(Chain.chain_id)
        if namespace:
            stmt = stmt.where(Chain.namespace == namespace)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, chain_id: str) -> Optional[Chain]:
        result = await self.db.execute(
            select(Chain).options(selectinload(Chain.steps)).where(Chain.chain_id == chain_id)
        )
        return result.scalar_one_or_none()

    async def get_enabled(self, namespace: str = "") -> List[Chain]:
        """列出启用的链条（链引擎加载用）。namespace 非空时按图谱过滤。"""
        stmt = select(Chain).options(selectinload(Chain.steps)).where(Chain.enabled == True)
        if namespace:
            stmt = stmt.where(Chain.namespace == namespace)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, chain_id: str, name: str = "", description: str = "",
                     triggers: list = None, final_prompt_template: str = "",
                     focus_concepts: str = "", enabled: bool = True,
                     source: str = "manual", mode: str = "merged",
                     verify_target: str = "", namespace: str = "", steps: list = None):
        chain = Chain(
            chain_id=chain_id, name=name, description=description,
            triggers=json.dumps(triggers or [], ensure_ascii=False),
            final_prompt_template=final_prompt_template,
            focus_concepts=focus_concepts, enabled=enabled, source=source,
            mode=mode, verify_target=verify_target, namespace=namespace,
        )
        if steps:
            for s in steps:
                chain.steps.append(ChainStep(
                    step_order=s.get("step_order", 0),
                    step_id=s.get("step_id", ""),
                    description=s.get("description", ""),
                    agent_name=s.get("agent_name", ""),
                    prompt_template=s.get("prompt_template", ""),
                    output_key=s.get("output_key", ""),
                    focus_concepts=s.get("focus_concepts", ""),
                    action_name=s.get("action_name", ""),
                    action_params=s.get("action_params", "{}"),
                    precondition=s.get("precondition", ""),
                    on_failure=s.get("on_failure", "abort"),
                ))
        self.db.add(chain)
        await self.db.commit()

    async def update(self, chain_id: str, **kwargs):
        chain = await self.get_by_id(chain_id)
        if not chain:
            return
        for k, v in kwargs.items():
            if k == "steps":
                # 先删旧 steps 再加新的
                await self.db.execute(delete(ChainStep).where(ChainStep.chain_id == chain_id))
                for s in (v or []):
                    self.db.add(ChainStep(
                        chain_id=chain_id,
                        step_order=s.get("step_order", 0),
                        step_id=s.get("step_id", ""),
                        description=s.get("description", ""),
                        agent_name=s.get("agent_name", ""),
                        prompt_template=s.get("prompt_template", ""),
                        output_key=s.get("output_key", ""),
                        focus_concepts=s.get("focus_concepts", ""),
                        action_name=s.get("action_name", ""),
                        action_params=s.get("action_params", "{}"),
                        precondition=s.get("precondition", ""),
                        on_failure=s.get("on_failure", "abort"),
                    ))
            elif k == "triggers":
                setattr(chain, k, json.dumps(v or [], ensure_ascii=False))
            elif hasattr(chain, k):
                setattr(chain, k, v)
        await self.db.commit()

    async def upsert(self, chain_id: str, name: str = "", description: str = "",
                     triggers: list = None, final_prompt_template: str = "",
                     focus_concepts: str = "", enabled: bool = True,
                     source: str = "manual", namespace: str = "", steps: list = None):
        """INSERT OR REPLACE（编译器同步用）"""
        existing = await self.get_by_id(chain_id)
        if existing:
            await self.update(chain_id, name=name, description=description,
                              triggers=triggers, final_prompt_template=final_prompt_template,
                              focus_concepts=focus_concepts, enabled=enabled,
                              source=source, namespace=namespace, steps=steps)
        else:
            await self.create(chain_id, name=name, description=description,
                              triggers=triggers, final_prompt_template=final_prompt_template,
                              focus_concepts=focus_concepts, enabled=enabled,
                              source=source, namespace=namespace, steps=steps)

    async def delete(self, chain_id: str):
        await self.db.execute(delete(ChainStep).where(ChainStep.chain_id == chain_id))
        await self.db.execute(delete(Chain).where(Chain.chain_id == chain_id))
        await self.db.commit()

    async def mark_stale(self, active_ids: set, namespace: str = ""):
        """将所有不在 active_ids 中的手动链标记为 disabled。namespace 非空时只处理该图谱。"""
        for chain in await self.list_all(namespace):
            if chain.chain_id not in active_ids:
                chain.enabled = False
        await self.db.commit()
