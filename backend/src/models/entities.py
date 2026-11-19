from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base

def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(250))
class Node(Base):
    __tablename__ = 'nodes'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    term: Mapped[int] = mapped_column(Integer)
    log_position: Mapped[int] = mapped_column(Integer)
    available: Mapped[bool] = mapped_column(Boolean)
    fenced: Mapped[bool] = mapped_column(Boolean)
    vote_enabled: Mapped[bool] = mapped_column(Boolean)
class Scenario(Base):
    __tablename__ = 'scenarios'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    fail_primary: Mapped[bool] = mapped_column(Boolean)
    fence_primary: Mapped[bool] = mapped_column(Boolean)
    max_data_loss: Mapped[int] = mapped_column(Integer)
    required_term: Mapped[int] = mapped_column(Integer)
class Drill(Base):
    __tablename__ = 'drills'
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey('scenarios.id'))
    label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default='queued')
    snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class Audit(Base):
    __tablename__ = 'audits'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class PageCopy(Base):
    """一个节点上一个逻辑页的物理副本。校验和不匹配即坏页。"""
    __tablename__ = 'page_copies'
    __table_args__ = (UniqueConstraint('node_id', 'page_key', name='uq_page_node_key'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey('nodes.id'))
    page_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
class RepairJob(Base):
    """同一次补页按 repair_key 幂等：中断重试不重复发布。"""
    __tablename__ = 'repair_jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    repair_key: Mapped[str] = mapped_column(String(64), unique=True)
    target_copy_id: Mapped[int] = mapped_column(ForeignKey('page_copies.id'))
    donor_copy_id: Mapped[int | None] = mapped_column(ForeignKey('page_copies.id'), nullable=True)
    target_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    publish_count: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    blocked_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
