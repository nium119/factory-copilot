from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime
from app.models.base import Base


class SkillEmbedding(Base):
    __tablename__ = "agent_skill_embeddings"

    skill_name = Column(String(128), primary_key=True)
    embedding = Column(Text, nullable=False)  # JSON serialized float list
    updated_at = Column(DateTime, default=datetime.utcnow)
