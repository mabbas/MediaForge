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
    BigInteger,
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
    CheckConstraint,
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
    sessions = relationship("AuthSessionDB", back_populates="tenant", cascade="all, delete-orphan")
    artifacts = relationship("MediaArtifactDB", back_populates="tenant", cascade="all, delete-orphan")
    usage_events = relationship("UsageEventDB", back_populates="tenant", cascade="all, delete-orphan")
    quotas = relationship("TenantQuotaDB", back_populates="tenant", cascade="all, delete-orphan")
    work_items = relationship("MediaWorkItemDB", back_populates="tenant", cascade="all, delete-orphan")
    subscription = relationship("TenantSubscriptionDB", back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    entitlement_overrides = relationship("TenantEntitlementOverrideDB", back_populates="tenant", cascade="all, delete-orphan")
    commercial_audit_logs = relationship("CommercialAuditLogDB", back_populates="tenant", cascade="all, delete-orphan")


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
    sessions = relationship(
        "AuthSessionDB", back_populates="user", cascade="all, delete-orphan"
    )
    password_reset_tokens = relationship(
        "PasswordResetTokenDB", back_populates="user", cascade="all, delete-orphan"
    )
    artifacts = relationship(
        "MediaArtifactDB", back_populates="user"
    )
    usage_events = relationship(
        "UsageEventDB", back_populates="user"
    )
    work_items = relationship(
        "MediaWorkItemDB", back_populates="requester_user"
    )


class APIKeyDB(Base):
    """API key model for programmatic access (STEP 02-C2)."""

    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_tenant_id", "tenant_id"),
        Index("ix_api_keys_key_prefix", "key_prefix"),
        Index("ix_api_keys_user_id", "user_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(200), nullable=False)
    key_prefix = Column(String(16), nullable=False)
    key_hash = Column(String(256), nullable=False, unique=True)
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    permissions = Column(FlexJSON, default=list)
    rate_limit = Column(Integer, default=60, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_revoked = Column(Boolean, default=False, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revocation_reason = Column(String(255), nullable=True)
    revoked_by_user_id = Column(String(36), nullable=True)
    rotated_to_id = Column(
        String(36),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    rotated_from_id = Column(
        String(36),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    total_requests = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)

    user = relationship("UserDB", back_populates="api_keys", foreign_keys=[user_id])
    tenant = relationship("TenantDB", foreign_keys=[tenant_id])
    artifacts = relationship("MediaArtifactDB", back_populates="api_key")
    usage_events = relationship("UsageEventDB", back_populates="api_key")
    work_items = relationship("MediaWorkItemDB", back_populates="requester_api_key")


class DownloadJobDB(Base):
    """Download job model â€” tracks every download."""

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
    artifacts = relationship(
        "MediaArtifactDB",
        back_populates="source_download",
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


class PendingRegistrationDB(Base):
    """Pending tenant and user registrations awaiting email verification (STEP 02-B2A)."""

    __tablename__ = "pending_registrations"
    __table_args__ = (
        UniqueConstraint("email", name="uq_pending_registrations_email"),
        UniqueConstraint("tenant_slug", name="uq_pending_registrations_tenant_slug"),
        Index("ix_pending_registrations_token_hash", "token_hash", unique=True),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(320), nullable=False)
    password_hash = Column(String(256), nullable=False)
    tenant_name = Column(String(200), nullable=False)
    tenant_slug = Column(String(100), nullable=False)
    token_hash = Column(String(128), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    last_sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AuthSessionDB(Base):
    """Persistent authentication session and refresh token tracking (STEP 02-B2B)."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_tenant_id", "tenant_id"),
        Index("ix_auth_sessions_token_family", "token_family"),
        Index("ix_auth_sessions_current_token_hash", "current_token_hash", unique=True),
        Index("ix_auth_sessions_is_revoked", "is_revoked"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

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
    token_family = Column(String(64), nullable=False)
    current_token_hash = Column(String(128), nullable=False)
    rotation_counter = Column(Integer, default=0, nullable=False)
    is_revoked = Column(Boolean, default=False, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revocation_reason = Column(String(100), nullable=True)
    client_ip = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    last_refreshed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("UserDB", back_populates="sessions")
    tenant = relationship("TenantDB", back_populates="sessions")
    consumed_tokens = relationship(
        "ConsumedRefreshTokenDB",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class ConsumedRefreshTokenDB(Base):
    """Lineage of consumed refresh tokens for replay and theft detection (STEP 02-B2B)."""

    __tablename__ = "consumed_refresh_tokens"
    __table_args__ = (
        Index("ix_consumed_tokens_session_id", "session_id"),
        Index("ix_consumed_tokens_token_hash", "token_hash", unique=True),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(
        String(36),
        ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash = Column(String(128), nullable=False)
    consumed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    session = relationship("AuthSessionDB", back_populates="consumed_tokens")


class PasswordResetTokenDB(Base):
    """Password recovery tokens with expiration, single-use, and audit metadata (STEP 02-B2C)."""

    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_user_id", "user_id"),
        Index("ix_password_reset_tokens_token_hash", "token_hash", unique=True),
        Index("ix_password_reset_tokens_expires_at", "expires_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    client_ip = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)

    user = relationship("UserDB", back_populates="password_reset_tokens")


class MediaArtifactDB(Base):
    """Database model for persistent media artifacts (clips and merges) (STEP 02-E1)."""

    __tablename__ = "media_artifacts"
    __table_args__ = (
        Index("ix_media_artifacts_tenant_created", "tenant_id", "created_at"),
        Index("ix_media_artifacts_tenant_user", "tenant_id", "user_id"),
        Index("ix_media_artifacts_tenant_type_status", "tenant_id", "artifact_type", "status"),
        Index("ix_media_artifacts_storage_path", "tenant_id", "storage_path", unique=True),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key_id = Column(
        String(36),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_type = Column(String(20), nullable=False)  # 'clip', 'merge'
    source_download_id = Column(
        String(36),
        ForeignKey("download_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_artifact_id = Column(
        String(36),
        ForeignKey("media_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    storage_path = Column(String(512), nullable=False)
    filename = Column(String(255), nullable=False)
    format = Column(String(20), nullable=False, default="mp4")
    duration_seconds = Column(Float, nullable=True, default=0.0)
    file_size_bytes = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    sha256_checksum = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending, processing, ready, failed, deleted
    error_message = Column(Text, nullable=True)
    input_sources = Column(FlexJSON, default=list)
    metadata_json = Column(FlexJSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)

    tenant = relationship("TenantDB", back_populates="artifacts")
    user = relationship("UserDB", back_populates="artifacts")
    api_key = relationship("APIKeyDB", back_populates="artifacts")
    source_download = relationship("DownloadJobDB", back_populates="artifacts")
    parent_artifact = relationship("MediaArtifactDB", remote_side=[id], foreign_keys=[parent_artifact_id])




class UsageEventDB(Base):
    """Database model for persistent metered usage events (STEP 02-E2B)."""

    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_usage_events_tenant_idempotency"),
        Index("ix_usage_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_usage_events_tenant_type_created", "tenant_id", "event_type", "created_at"),
        Index("ix_usage_events_tenant_user_created", "tenant_id", "user_id", "created_at"),
        Index("ix_usage_events_tenant_key_created", "tenant_id", "api_key_id", "created_at"),
        Index("ix_usage_events_resource_id", "resource_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key_id = Column(
        String(36),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type = Column(String(32), nullable=False)  # 'download', 'clip', 'merge', 'transcription'
    idempotency_key = Column(String(128), nullable=False)
    resource_id = Column(String(64), nullable=True)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String(32), nullable=False, default="count")
    bytes = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    duration_seconds = Column(Float, nullable=False, default=0.0)
    auth_method = Column(String(20), nullable=False, default="jwt")
    client_ip = Column(String(45), nullable=True)
    metadata_json = Column(FlexJSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tenant = relationship("TenantDB", back_populates="usage_events")
    user = relationship("UserDB", back_populates="usage_events")
    api_key = relationship("APIKeyDB", back_populates="usage_events")


class TenantQuotaDB(Base):
    """Database model for advisory and configured tenant quotas (STEP 02-E2B)."""

    __tablename__ = "tenant_quotas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "metric", "period", name="uq_tenant_quotas_tenant_metric_period"),
        Index("ix_tenant_quotas_tenant_id", "tenant_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric = Column(String(64), nullable=False)  # 'downloads', 'download_bytes', 'clips', 'clip_seconds', 'merges', 'transcription_minutes'
    period = Column(String(20), nullable=False, default="monthly")
    limit_value = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=-1)  # -1 = UNLIMITED
    is_hard_limit = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tenant = relationship("TenantDB", back_populates="quotas")


class MediaWorkItemDB(Base):
    """Database model for durable background media work items (STEP 03-B)."""

    __tablename__ = "media_work_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_media_work_items_tenant_idempotency"),
        Index("ix_media_work_items_claimable", "status", "available_at"),
        Index("ix_media_work_items_lease_expiry", "status", "lease_expires_at"),
        Index("ix_media_work_items_tenant_created", "tenant_id", "created_at"),
        Index("ix_media_work_items_tenant_user", "tenant_id", "requester_user_id"),
        Index("ix_media_work_items_status", "status"),
        CheckConstraint("attempt_count >= 0 AND attempt_count <= max_attempts + 1", name="ck_media_work_items_attempts"),
        CheckConstraint("progress_percent >= 0.0 AND progress_percent <= 100.0", name="ck_media_work_items_progress"),
        CheckConstraint("status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')", name="ck_media_work_items_status"),
        CheckConstraint("operation_type IN ('clip_extract', 'merge')", name="ck_media_work_items_operation"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    requester_user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    requester_api_key_id = Column(
        String(36),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    operation_type = Column(String(32), nullable=False)  # 'clip_extract', 'merge'
    idempotency_key = Column(String(128), nullable=False)
    request_payload = Column(FlexJSON, default=dict, nullable=False)
    status = Column(String(32), nullable=False, default="queued")  # queued, running, retry_wait, succeeded, failed, cancelled
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    claimed_by = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)
    available_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    progress_percent = Column(Float, default=0.0, nullable=False)
    cancellation_requested_at = Column(DateTime, nullable=True)
    result_artifact_id = Column(
        String(36),
        ForeignKey("media_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    sanitized_error_code = Column(String(64), nullable=True)
    sanitized_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tenant = relationship("TenantDB", back_populates="work_items")
    requester_user = relationship("UserDB", back_populates="work_items")
    requester_api_key = relationship("APIKeyDB", back_populates="work_items")
    result_artifact = relationship("MediaArtifactDB", foreign_keys=[result_artifact_id], lazy="selectin")


class CommercialPlanDB(Base):
    """Database model for commercial plan catalog (STEP 04-A1)."""

    __tablename__ = "commercial_plans"
    __table_args__ = (
        UniqueConstraint("plan_code", "version", name="uq_commercial_plans_code_version"),
        Index("ix_commercial_plans_status_public", "status", "is_public"),
        CheckConstraint("status IN ('draft', 'active', 'retired')", name="ck_commercial_plans_status"),
        CheckConstraint("tier_type IN ('free', 'trial', 'paid')", name="ck_commercial_plans_tier_type"),
        CheckConstraint("version >= 1", name="ck_commercial_plans_version"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_code = Column(String(64), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="draft")  # draft, active, retired
    is_public = Column(Boolean, nullable=False, default=True)
    tier_type = Column(String(32), nullable=False, default="paid")  # free, trial, paid
    version = Column(Integer, nullable=False, default=1)
    billing_interval = Column(String(32), nullable=False, default="monthly")  # monthly, annual, lifetime
    currency = Column(String(3), nullable=False, default="USD")
    price_cents = Column(Integer, nullable=True, default=None)
    metadata_json = Column(FlexJSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    published_at = Column(DateTime, nullable=True)
    retired_at = Column(DateTime, nullable=True)

    entitlements = relationship("CommercialPlanEntitlementDB", back_populates="plan", cascade="all, delete-orphan")
    subscriptions = relationship("TenantSubscriptionDB", back_populates="plan")


class CommercialPlanEntitlementDB(Base):
    """Database model for typed plan entitlements (STEP 04-A1)."""

    __tablename__ = "commercial_plan_entitlements"
    __table_args__ = (
        UniqueConstraint("plan_id", "entitlement_key", name="uq_commercial_plan_entitlements_plan_key"),
        Index("ix_commercial_plan_entitlements_plan_id", "plan_id"),
        CheckConstraint("value_type IN ('boolean', 'count', 'bytes', 'seconds', 'concurrency')", name="ck_plan_entitlements_value_type"),
        CheckConstraint("value_int IS NULL OR value_int >= 0", name="ck_plan_entitlements_value_int_nonneg"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id = Column(
        String(36),
        ForeignKey("commercial_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    entitlement_key = Column(String(64), nullable=False)
    value_type = Column(String(32), nullable=False)  # boolean, count, bytes, seconds, concurrency
    value_bool = Column(Boolean, nullable=True)
    value_int = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    is_unlimited = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    plan = relationship("CommercialPlanDB", back_populates="entitlements")


class TenantSubscriptionDB(Base):
    """Database model for tenant plan assignment and trial status (STEP 04-A1)."""

    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_subscriptions_tenant_id"),
        Index("ix_tenant_subscriptions_status", "status"),
        CheckConstraint(
            "status IN ('trial', 'active', 'grace_period', 'scheduled_cancellation', 'cancelled', 'expired', 'suspended', 'legacy_active', 'reconciliation_required', 'pending_activation')",
            name="ck_tenant_subscriptions_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id = Column(
        String(36),
        ForeignKey("commercial_plans.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status = Column(String(32), nullable=False, default="trial")  # trial, active, cancelled, expired
    trial_started_at = Column(DateTime, nullable=True)
    trial_expires_at = Column(DateTime, nullable=True)
    current_period_start = Column(DateTime, default=datetime.utcnow, nullable=False)
    current_period_end = Column(DateTime, nullable=True)
    metadata_json = Column(FlexJSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tenant = relationship("TenantDB", back_populates="subscription")
    plan = relationship("CommercialPlanDB", back_populates="subscriptions")


class TenantEntitlementOverrideDB(Base):
    """Database model for tenant-specific entitlement overrides (STEP 04-A1)."""

    __tablename__ = "tenant_entitlement_overrides"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entitlement_key", name="uq_tenant_entitlement_overrides_tenant_key"),
        Index("ix_tenant_entitlement_overrides_tenant", "tenant_id"),
        CheckConstraint("value_type IN ('boolean', 'count', 'bytes', 'seconds', 'concurrency')", name="ck_tenant_overrides_value_type"),
        CheckConstraint("value_int IS NULL OR value_int >= 0", name="ck_tenant_overrides_value_int_nonneg"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    entitlement_key = Column(String(64), nullable=False)
    value_type = Column(String(32), nullable=False)  # boolean, count, bytes, seconds, concurrency
    value_bool = Column(Boolean, nullable=True)
    value_int = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    is_unlimited = Column(Boolean, nullable=False, default=False)
    reason = Column(Text, nullable=False)
    granted_by_user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tenant = relationship("TenantDB", back_populates="entitlement_overrides")
    granted_by_user = relationship("UserDB", foreign_keys=[granted_by_user_id])


class CommercialAuditLogDB(Base):
    """Database model for commercial plan and entitlement audit trail (STEP 04-A1)."""

    __tablename__ = "commercial_audit_logs"
    __table_args__ = (
        Index("ix_commercial_audit_logs_tenant", "tenant_id", "created_at"),
        Index("ix_commercial_audit_logs_action", "action", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    action = Column(String(64), nullable=False)  # plan_created, plan_published, plan_retired, tenant_assigned, trial_extended, override_granted, override_revoked
    actor_user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_role = Column(String(32), nullable=False, default="system_admin")
    reason = Column(Text, nullable=True)
    details_json = Column(FlexJSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tenant = relationship("TenantDB", back_populates="commercial_audit_logs")
    actor_user = relationship("UserDB", foreign_keys=[actor_user_id])


class CommercialReservationDB(Base):
    """Database model for durable transactional quota reservations (STEP 04-A2)."""

    __tablename__ = "commercial_reservations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_commercial_reservations_tenant_idempotency"),
        Index("ix_commercial_reservations_tenant_status", "tenant_id", "status"),
        Index("ix_commercial_reservations_resource", "resource_id"),
        Index("ix_commercial_reservations_lease", "status", "lease_expires_at"),
        CheckConstraint(
            "status IN ('reserved', 'active', 'finalized', 'released', 'expired')",
            name="ck_commercial_reservations_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_type = Column(String(32), nullable=False)  # 'download', 'clip_extract', 'merge', 'transcript'
    resource_id = Column(String(64), nullable=True)  # work item id, download id
    idempotency_key = Column(String(128), nullable=False)
    entitlement_key = Column(String(64), nullable=False)  # max_concurrent_jobs, monthly_download_count, etc.
    quantity = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="reserved")  # reserved, active, finalized, released, expired
    estimated_bytes = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    estimated_duration_seconds = Column(Float, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    metadata_json = Column(FlexJSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    finalized_at = Column(DateTime, nullable=True)
    released_at = Column(DateTime, nullable=True)

    tenant = relationship("TenantDB")


class ScheduledSubscriptionTransitionDB(Base):
    """Database model for scheduled commercial plan transitions (STEP 04-A3)."""

    __tablename__ = "scheduled_subscription_transitions"
    __table_args__ = (
        Index("ix_sched_transitions_tenant_status", "tenant_id", "status"),
        Index("ix_sched_transitions_due", "status", "scheduled_at"),
        CheckConstraint(
            "transition_type IN ('upgrade', 'downgrade', 'cancel', 'renewal')",
            name="ck_sched_trans_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'executed', 'cancelled', 'failed')",
            name="ck_sched_trans_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_plan_id = Column(
        String(36),
        ForeignKey("commercial_plans.id", ondelete="RESTRICT"),
        nullable=True,
    )
    transition_type = Column(String(32), nullable=False)  # upgrade, downgrade, cancel, renewal
    status = Column(String(32), nullable=False, default="pending")  # pending, executed, cancelled, failed
    scheduled_at = Column(DateTime, nullable=False)
    executed_at = Column(DateTime, nullable=True)
    reason = Column(Text, nullable=False)
    actor_user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_role = Column(String(64), nullable=False, default="system_admin")
    metadata_json = Column(FlexJSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    tenant = relationship("TenantDB")
    target_plan = relationship("CommercialPlanDB")