"""Add task chain tables.

Revision ID: 20260630_0004
Revises: 20260624_0003
Create Date: 2026-06-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260630_0004"
down_revision: Union[str, Sequence[str], None] = "20260624_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "simulation"'))

    op.create_table(
        "task_chains",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("chain_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("trigger_policy", sa.String(length=64), nullable=False),
        sa.Column("robot_strategy", sa.String(length=64), nullable=False),
        sa.Column("failure_policy", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", jsonb(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "chain_id", name="uk_simulation_task_chains_chain_id"),
        schema="simulation",
    )
    op.create_index("idx_simulation_task_chains_run", "task_chains", ["workspace_id", "run_id", "created_at"], schema="simulation")
    for column in ("workspace_id", "run_id"):
        op.create_index(op.f(f"ix_simulation_task_chains_{column}"), "task_chains", [column], schema="simulation")

    op.create_table(
        "task_chain_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("chain_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("depends_on_json", jsonb(), nullable=False),
        sa.Column("trigger_condition", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", jsonb(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "chain_id", "sequence", name="uk_simulation_task_chain_items_sequence"),
        schema="simulation",
    )
    op.create_index("idx_simulation_task_chain_items_chain", "task_chain_items", ["workspace_id", "chain_id", "sequence"], schema="simulation")
    for column in ("workspace_id", "run_id", "chain_id", "task_id"):
        op.create_index(op.f(f"ix_simulation_task_chain_items_{column}"), "task_chain_items", [column], schema="simulation")


def downgrade() -> None:
    op.drop_table("task_chain_items", schema="simulation")
    op.drop_table("task_chains", schema="simulation")
