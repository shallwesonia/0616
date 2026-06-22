from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


JSON_DOCUMENT = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class SiteMapRecord(Base):
    __tablename__ = "site_maps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "map_id", "config_version", name="uk_site_maps_version"),
        Index("idx_site_maps_active", "workspace_id", "map_id", "status"),
        {"schema": "config"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    map_id: Mapped[str] = mapped_column(String(128), nullable=False)
    map_name: Mapped[str] = mapped_column(String(256), nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    map_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)


class MapDraftRecord(Base):
    __tablename__ = "map_drafts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "draft_id", name="uk_map_drafts_draft_id"),
        Index("idx_map_drafts_map", "workspace_id", "map_id", "created_at"),
        {"schema": "config"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    draft_id: Mapped[str] = mapped_column(String(128), nullable=False)
    map_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="editing")
    map_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RobotInstanceRecord(Base):
    __tablename__ = "robot_instances"
    __table_args__ = (
        UniqueConstraint("workspace_id", "robot_code", name="uk_robot_instances_code"),
        {"schema": "runtime"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    robot_code: Mapped[str] = mapped_column(String(128), nullable=False)
    robot_type: Mapped[str] = mapped_column(String(128), nullable=False)
    robot_state: Mapped[str] = mapped_column(String(32), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_action: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)


class MessageRecordRow(Base):
    __tablename__ = "message_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "message_id", name="uk_message_records_message_id"),
        Index("idx_message_records_created", "workspace_id", "created_at"),
        Index("idx_message_records_type", "workspace_id", "message_type", "created_at"),
        Index("idx_message_records_topic", "workspace_id", "topic"),
        {"schema": "message"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(String(512), nullable=False)
    command_id: Mapped[str | None] = mapped_column(String(128), index=True)
    task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    robot_code: Mapped[str | None] = mapped_column(String(128), index=True)
    event: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_logs_resource", "workspace_id", "resource_type", "resource_id"),
        {"schema": "audit"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    audit_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    actor_type: Mapped[str] = mapped_column(String(64), nullable=False, default="service")
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)


class ExportJobRecord(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        Index("idx_export_jobs_created", "workspace_id", "created_at"),
        {"schema": "export"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    export_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    export_type: Mapped[str] = mapped_column(String(64), nullable=False)
    export_status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
