"""链条 + 步骤模型"""
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.models.base import Base, TimestampMixin

class Chain(Base, TimestampMixin):
    __tablename__ = "agent_chains"

    chain_id = Column(String(64), primary_key=True)
    name = Column(String(100), default="")
    description = Column(String(500), default="")
    triggers = Column(Text, default="[]")
    final_prompt_template = Column(Text, default="")
    focus_concepts = Column(String(500), default="")
    enabled = Column(Boolean, default=True)  # SQLite stores as 0/1, SQLAlchemy handles it
    source = Column(String(16), default="manual")  # manual | compiler
    mode = Column(String(16), default="merged")  # merged | chained | pipeline
    steps = relationship("ChainStep", back_populates="chain", cascade="all, delete-orphan", order_by="ChainStep.step_order")


class ChainStep(Base):
    __tablename__ = "agent_chain_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chain_id = Column(String(64), ForeignKey("agent_chains.chain_id"), nullable=False)
    step_order = Column(Integer, default=0)
    step_id = Column(String(64), default="")
    description = Column(String(500), default="")
    agent_name = Column(String(64), default="")
    prompt_template = Column(Text, default="")
    output_key = Column(String(64), default="")
    focus_concepts = Column(String(500), default="")
    # pipeline 模式字段
    action_name = Column(String(255), default="")
    action_params = Column(Text, default="{}")
    precondition = Column(String(500), default="")
    on_failure = Column(String(20), default="abort")
    chain = relationship("Chain", back_populates="steps")
