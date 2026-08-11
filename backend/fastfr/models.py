from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"


class IdentityCategory(str, enum.Enum):
    WHITELIST = "whitelist"
    BLACKLIST = "blacklist"


class SessionStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


class EventType(str, enum.Enum):
    MATCH = "match"
    UNKNOWN = "unknown"
    NOT_REGISTERED = "not_registered"
    BLACKLIST = "blacklist"


class AttendanceSource(str, enum.Enum):
    AUTO = "automatic"
    MANUAL = "manual"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.OPERATOR.value)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Identity(Base):
    __tablename__ = "identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, default=IdentityCategory.WHITELIST.value
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    face_samples: Mapped[list[FaceSample]] = relationship(
        back_populates="identity", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_identities_category_active", "category", "active"),
        Index("idx_identities_candidate_no", "candidate_no"),
    )


class FaceSample(Base):
    __tablename__ = "face_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("identities.id", ondelete="CASCADE"))
    embedding_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    crop_blob_key: Mapped[str] = mapped_column(String(100), nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    identity: Mapped[Identity] = relationship(back_populates="face_samples")

    __table_args__ = (Index("idx_face_samples_identity_id", "identity_id"),)


class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SessionStatus.DRAFT.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("idx_exam_sessions_status_dates", "status", "starts_at", "ends_at"),)


class SessionMember(Base):
    __tablename__ = "session_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("exam_sessions.id", ondelete="CASCADE"))
    identity_id: Mapped[int] = mapped_column(ForeignKey("identities.id", ondelete="RESTRICT"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("session_id", "identity_id", name="uq_session_member"),
        Index("idx_session_members_session", "session_id"),
        Index("idx_session_members_identity", "identity_id"),
    )


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    rtsp_url_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rotation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    roi_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("idx_cameras_enabled_session", "enabled", "session_id"),)


class RecognitionEvent(Base):
    __tablename__ = "recognition_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="RESTRICT"))
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_sessions.id", ondelete="SET NULL"), nullable=True
    )
    identity_id: Mapped[int | None] = mapped_column(
        ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    identity_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    candidate_no_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True)
    identity_category_snapshot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decision_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    snapshot_blob_key: Mapped[str] = mapped_column(String(100), nullable=False)
    resolved_identity_id: Mapped[int | None] = mapped_column(
        ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_requested")

    __table_args__ = (
        Index("idx_events_session_time", "session_id", "captured_at"),
        Index("idx_events_type_time", "event_type", "captured_at"),
        Index("idx_events_unresolved", "resolved_at", "event_type"),
    )


class Attendance(Base):
    __tablename__ = "attendance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("exam_sessions.id", ondelete="CASCADE"))
    identity_id: Mapped[int] = mapped_column(ForeignKey("identities.id", ondelete="RESTRICT"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    first_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("recognition_events.id", ondelete="SET NULL"), nullable=True
    )
    camera_id: Mapped[int | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    operator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("session_id", "identity_id", name="uq_attendance_session_identity"),
        Index("idx_attendance_session", "session_id"),
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("idx_audit_created_at", "created_at"),)
