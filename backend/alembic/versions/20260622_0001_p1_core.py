"""Create P1 core workspace tables.

Revision ID: 20260622_0001
Revises:
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260622_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for schema in ("config", "runtime", "message", "audit", "export"):
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    op.create_table(
        "site_maps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("map_id", sa.String(length=128), nullable=False),
        sa.Column("map_name", sa.String(length=256), nullable=False),
        sa.Column("config_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("map_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "map_id", "config_version", name="uk_site_maps_version"),
        schema="config",
    )
    op.create_index("idx_site_maps_active", "site_maps", ["workspace_id", "map_id", "status"], schema="config")
    op.create_index(op.f("ix_config_site_maps_workspace_id"), "site_maps", ["workspace_id"], schema="config")

    op.create_table(
        "map_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.String(length=128), nullable=False),
        sa.Column("map_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("map_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "draft_id", name="uk_map_drafts_draft_id"),
        schema="config",
    )
    op.create_index("idx_map_drafts_map", "map_drafts", ["workspace_id", "map_id", "created_at"], schema="config")
    op.create_index(op.f("ix_config_map_drafts_workspace_id"), "map_drafts", ["workspace_id"], schema="config")

    op.create_table(
        "robot_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("robot_code", sa.String(length=128), nullable=False),
        sa.Column("robot_type", sa.String(length=128), nullable=False),
        sa.Column("robot_state", sa.String(length=32), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("current_action", sa.String(length=256), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "robot_code", name="uk_robot_instances_code"),
        schema="runtime",
    )
    op.create_index(op.f("ix_runtime_robot_instances_workspace_id"), "robot_instances", ["workspace_id"], schema="runtime")

    op.create_table(
        "message_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=True),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("robot_code", sa.String(length=128), nullable=True),
        sa.Column("event", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "message_id", name="uk_message_records_message_id"),
        schema="message",
    )
    op.create_index("idx_message_records_created", "message_records", ["workspace_id", "created_at"], schema="message")
    op.create_index("idx_message_records_type", "message_records", ["workspace_id", "message_type", "created_at"], schema="message")
    op.create_index("idx_message_records_topic", "message_records", ["workspace_id", "topic"], schema="message")
    for column in ("workspace_id", "command_id", "task_id", "request_id", "trace_id", "robot_code", "event", "created_at"):
        op.create_index(op.f(f"ix_message_message_records_{column}"), "message_records", [column], schema="message")

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("audit_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audit_id"),
        schema="audit",
    )
    op.create_index("idx_audit_logs_resource", "audit_logs", ["workspace_id", "resource_type", "resource_id"], schema="audit")
    op.create_index(op.f("ix_audit_audit_logs_workspace_id"), "audit_logs", ["workspace_id"], schema="audit")

    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("export_id", sa.String(length=128), nullable=False),
        sa.Column("export_type", sa.String(length=64), nullable=False),
        sa.Column("export_status", sa.String(length=32), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("export_id"),
        schema="export",
    )
    op.create_index("idx_export_jobs_created", "export_jobs", ["workspace_id", "created_at"], schema="export")
    op.create_index(op.f("ix_export_export_jobs_workspace_id"), "export_jobs", ["workspace_id"], schema="export")


def downgrade() -> None:
    op.drop_table("export_jobs", schema="export")
    op.drop_table("audit_logs", schema="audit")
    op.drop_table("message_records", schema="message")
    op.drop_table("robot_instances", schema="runtime")
    op.drop_table("map_drafts", schema="config")
    op.drop_table("site_maps", schema="config")
    for schema in ("export", "audit", "message", "runtime", "config"):
        op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}"'))
