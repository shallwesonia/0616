"""Add MVP baseline target, robot config and executor tables.

Revision ID: 20260624_0003
Revises: 20260623_0002
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260624_0003"
down_revision: Union[str, Sequence[str], None] = "20260623_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "config"'))
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "runtime"'))

    op.create_table(
        "target_registry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("map_id", sa.String(length=128), nullable=False),
        sa.Column("pose_json", jsonb(), nullable=True),
        sa.Column("geometry_ref", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", jsonb(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "target_id", name="uk_target_registry_target_id"),
        schema="config",
    )
    op.create_index("idx_target_registry_type", "target_registry", ["workspace_id", "target_type", "status"], schema="config")
    op.create_index("idx_target_registry_map", "target_registry", ["workspace_id", "map_id"], schema="config")
    op.create_index(op.f("ix_config_target_registry_workspace_id"), "target_registry", ["workspace_id"], schema="config")

    op.create_table(
        "robot_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("robot_code", sa.String(length=128), nullable=False),
        sa.Column("robot_name", sa.String(length=256), nullable=True),
        sa.Column("robot_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("capabilities_json", jsonb(), nullable=False),
        sa.Column("action_set_id", sa.String(length=128), nullable=False),
        sa.Column("map_id", sa.String(length=128), nullable=False),
        sa.Column("initial_pose_json", jsonb(), nullable=False),
        sa.Column("executor_id", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "robot_code", name="uk_robot_configs_code"),
        schema="config",
    )
    op.create_index("idx_robot_configs_status", "robot_configs", ["workspace_id", "status", "enabled"], schema="config")
    op.create_index(op.f("ix_config_robot_configs_workspace_id"), "robot_configs", ["workspace_id"], schema="config")
    op.create_index(op.f("ix_config_robot_configs_executor_id"), "robot_configs", ["executor_id"], schema="config")

    op.create_table(
        "executor_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("executor_id", sa.String(length=128), nullable=False),
        sa.Column("robot_code", sa.String(length=128), nullable=False),
        sa.Column("executor_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mqtt_client_id", sa.String(length=256), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("container_name", sa.String(length=256), nullable=True),
        sa.Column("gateway_endpoint", sa.String(length=512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", jsonb(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "executor_id", name="uk_executor_instances_executor_id"),
        schema="runtime",
    )
    op.create_index("idx_executor_instances_robot", "executor_instances", ["workspace_id", "robot_code", "status"], schema="runtime")
    op.create_index(op.f("ix_runtime_executor_instances_workspace_id"), "executor_instances", ["workspace_id"], schema="runtime")
    op.create_index(op.f("ix_runtime_executor_instances_robot_code"), "executor_instances", ["robot_code"], schema="runtime")


def downgrade() -> None:
    op.drop_table("executor_instances", schema="runtime")
    op.drop_table("robot_configs", schema="config")
    op.drop_table("target_registry", schema="config")
