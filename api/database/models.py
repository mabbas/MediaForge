"""GrabItDown database ORM models.

All tables use the 'gid' schema in PostgreSQL.
In SQLite (testing), tables are in default schema.

Tables:
- tenants: multi-tenant organizations
- users: user accounts with tenant membership
- api_keys: API key management
- download_jobs: download history and active jobs
- transcripts: extracted transcripts
- usage_tracking: per-user daily usage counters
- app_settings: key-value configuration store
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from api.database.connection import Base

FlexJSON = JSON


class TenantDB(Base):
    """Tenant (organization) model. Each tenant has isolated data."""

    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(200), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    tier = Column(String(20), nullable=False, default="basic")
    settings = Column(FlexJSON, default=dict)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    users = relationship("UserDB", back_populates="tenant", cascade="all, delete-orphan")


class UserDB(Base):
    """User account model. Users belong to one tenant."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", "tenant_id", name="uq_users_email_tenant"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    email = Column(String(320), nullable=False)
    display_name = Column(String(200))
    password_hash = Column(String(256))
    auth_provider = Column(String(50), default="local")
    role = Column(String(20), default="member")
    tier_override = Column(String(20), nullable=True)
    settings = Column(FlexJSON, default=dict)
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("TenantDB", back_populates="users")
    api_keys = relationship(
        "APIKeyDB", back_populates="user", cascade="all, delete-orphan"
    )


class APIKeyDB(Base):
    """API key model for programmatic access."""

    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    key_hash = Column(String(256), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    permissions = Column(FlexJSON, default=lambda: ["read", "write"])
    rate_limit = Column(Integer, default=60)
    is_active = Column(Boolean, default=True)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("UserDB", back_populates="api_keys")


class DownloadJobDB(Base):
    """Download job model — tracks every download."""

    __tablename__ = "download_jobs"
    __table_args__ = (
        Index("ix_dj_status_created", "status", "created_at"),
        Index("ix_dj_user_status", "user_id", "status"),
        Index("ix_dj_tenant_created", "tenant_id", "created_at"),
        Index("ix_dj_parent", "parent_job_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(String(2048), nullable=False)
    provider = Column(String(50), nullable=False)
    media_type = Column(String(10), nullable=False, default="video")
    status = Column(String(20), nullable=False, default="created")
    priority = Column(String(10), nullable=False, default="normal")
    title = Column(String(500))
    quality = Column(String(20))
    format = Column(String(20))

    file_path = Column(String(1024))
    file_size_bytes = Column(Integer)
    duration_seconds = Column(Integer)

    progress_percent = Column(Float, default=0.0)
    speed_bps = Column(Float, default=0.0)
    eta_seconds = Column(Integer)
    bytes_downloaded = Column(Integer, default=0)
    total_bytes = Column(Integer)

    error_message = Column(Text)
    retry_count = Column(Integer, default=0)

    parent_job_id = Column(
        String(36),
        ForeignKey("download_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    playlist_index = Column(Integer, nullable=True)

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )

    metadata_json = Column(FlexJSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    children = relationship(
        "DownloadJobDB",
        backref="parent",
        remote_side="DownloadJobDB.id",
        foreign_keys=[parent_job_id],
    )
    transcript = relationship(
        "TranscriptDB",
        back_populates="download_job",
        uselist=False,
    )


class TranscriptDB(Base):
    """Transcript model."""

    __tablename__ = "transcripts"
    __table_args__ = (
        Index("ix_tr_user_created", "user_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(
        String(36),
        ForeignKey("download_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    url = Column(String(2048), nullable=False)
    language = Column(String(10), nullable=False)
    source = Column(String(20), nullable=False)
    format = Column(String(10), nullable=False)

    file_path = Column(String(1024))
    content = Column(Text)
    duration_seconds = Column(Float)
    word_count = Column(Integer)
    segment_count = Column(Integer)

    user_id = Column(String(36), nullable=True)
    tenant_id = Column(String(36), nullable=True)

    metadata_json = Column(FlexJSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)

    download_job = relationship("DownloadJobDB", back_populates="transcript")


class UsageTrackingDB(Base):
    """Daily usage tracking per user."""

    __tablename__ = "usage_tracking"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "tenant_id", "date", name="uq_usage_user_tenant_date"
        ),
        Index("ix_usage_date", "date"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False)
    tenant_id = Column(String(36), nullable=True, default="default")
    date = Column(String(10), nullable=False)

    downloads_count = Column(Integer, default=0)
    bytes_downloaded = Column(Integer, default=0)
    transcripts_count = Column(Integer, default=0)
    whisper_minutes = Column(Float, default=0.0)
    api_calls_count = Column(Integer, default=0)


class AppSettingDB(Base):
    """App settings key-value store."""

    __tablename__ = "app_settings"

    key = Column(String(200), primary_key=True)
    value = Column(FlexJSON)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
